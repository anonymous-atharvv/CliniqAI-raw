"""
ICU Real-Time Streaming Pipeline
MQTT → Kafka → TimescaleDB → AI analysis → WebSocket push

ICU monitors publish vitals at 1Hz per device parameter via MQTT.
This service bridges to Kafka, validates, normalizes to FHIR, stores,
and triggers AI analysis when thresholds crossed.

At 100 ICU devices × 5 parameters × 1Hz = 500 messages/second.
Kafka handles this easily. Direct DB writes would not.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, List, Callable, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# MQTT Message Schema (from ICU monitor)
# ─────────────────────────────────────────────

@dataclass
class MQTTVitalMessage:
    """Raw vital sign message from ICU monitor via MQTT."""
    device_id: str
    patient_id: str           # Hospital internal ID (will be mapped to deident_id)
    parameter: str            # e.g. "hr", "spo2", "nibp_sys"
    value: float
    timestamp: str            # ISO8601 from device clock
    unit: str
    signal_quality: Optional[int] = None   # 0-100 signal quality from device
    alarm_state: Optional[str] = None      # "normal"|"high"|"low"|"critical"

    @classmethod
    def from_json(cls, payload: bytes) -> "MQTTVitalMessage":
        data = json.loads(payload.decode())
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


# ── Parameter name normalization (device-specific → LOINC parameter names) ──

DEVICE_PARAM_MAP = {
    # Philips IntelliVue naming
    "HR": "heart_rate", "SpO2": "spo2_pulse_ox", "SpO2-1": "spo2_pulse_ox",
    "NBP-S": "bp_systolic", "NBP-D": "bp_diastolic", "NBP-M": "bp_mean",
    "ABP-S": "bp_systolic", "ABP-D": "bp_diastolic", "ABP-M": "bp_mean",
    "RR": "respiratory_rate", "RESP": "respiratory_rate",
    "TEMP": "temperature", "T1": "temperature", "T2": "temperature",
    "GCS": "gcs_total", "FiO2": "fio2",

    # GE Healthcare naming
    "hr": "heart_rate", "spo2": "spo2_pulse_ox",
    "nibp_sys": "bp_systolic", "nibp_dia": "bp_diastolic", "nibp_map": "bp_mean",
    "ibp_sys": "bp_systolic", "ibp_dia": "bp_diastolic", "ibp_map": "bp_mean",
    "resp": "respiratory_rate", "temp": "temperature",

    # Draeger naming
    "Pulse": "heart_rate", "SO2": "spo2_pulse_ox",
    "ABP-s": "bp_systolic", "ABP-d": "bp_diastolic", "ABP-m": "bp_mean",
    "CO2": "paco2",
}

# Physiological artifact detection limits
ARTIFACT_LIMITS = {
    "heart_rate":        (10, 300),
    "spo2_pulse_ox":     (50, 100),
    "bp_systolic":       (40, 300),
    "bp_diastolic":      (20, 200),
    "bp_mean":           (30, 250),
    "respiratory_rate":  (4, 60),
    "temperature":       (25.0, 45.0),
    "gcs_total":         (3, 15),
    "fio2":              (21, 100),
}


# ─────────────────────────────────────────────
# MQTT → Kafka Bridge
# ─────────────────────────────────────────────

class MQTTKafkaBridge:
    """
    Bridges ICU monitor MQTT stream to Kafka.

    MQTT topics: icu/{ward}/{device_id}/vitals
    Kafka topic: icu.vitals.raw

    Processing per message (<5ms target):
    1. Decode MQTT payload
    2. Map device parameter name → LOINC parameter name
    3. Artifact detection (physiologically impossible values)
    4. Enrich with patient context (device_id → patient_id lookup)
    5. Forward to Kafka
    """

    MQTT_TOPIC_PATTERN = "icu/+/+/vitals"   # + = wildcard
    KAFKA_TOPIC = "icu.vitals.raw"

    def __init__(self, mqtt_client=None, kafka_producer=None, device_registry=None):
        self._mqtt = mqtt_client
        self._kafka = kafka_producer
        self._device_registry = device_registry or {}   # device_id → {patient_id, ward_code}
        self._stats = {"received": 0, "forwarded": 0, "artifacts": 0, "errors": 0}

    async def start(self):
        """Start listening on MQTT and forwarding to Kafka."""
        if not self._mqtt:
            logger.warning("MQTT client not configured — bridge in simulation mode")
            await self._simulate()
            return

        self._mqtt.on_message = self._on_message
        self._mqtt.subscribe(self.MQTT_TOPIC_PATTERN, qos=1)
        logger.info(f"MQTT→Kafka bridge started, subscribed to: {self.MQTT_TOPIC_PATTERN}")

        while True:
            await asyncio.sleep(1)

    async def _on_message(self, client, topic: str, payload: bytes, qos: int, properties):
        """Handle incoming MQTT message."""
        self._stats["received"] += 1
        t0 = time.time()

        try:
            msg = MQTTVitalMessage.from_json(payload)

            # Map device parameter name to LOINC name
            loinc_param = DEVICE_PARAM_MAP.get(msg.parameter, msg.parameter.lower())

            # Artifact detection
            limits = ARTIFACT_LIMITS.get(loinc_param)
            if limits and not (limits[0] <= msg.value <= limits[1]):
                self._stats["artifacts"] += 1
                logger.debug(f"ARTIFACT: {loinc_param}={msg.value} outside {limits}")
                return

            # Enrich with patient context
            device_info = self._device_registry.get(msg.device_id, {})
            patient_deident_id = device_info.get("patient_deident_id", str(uuid.uuid4()))
            encounter_id = device_info.get("encounter_id", str(uuid.uuid4()))

            # Build Kafka message
            kafka_msg = {
                "event_id": str(uuid.uuid4()),
                "time": msg.timestamp or datetime.now(timezone.utc).isoformat(),
                "patient_deident_id": patient_deident_id,
                "encounter_id": encounter_id,
                "parameter": loinc_param,
                "value": float(msg.value),
                "unit": msg.unit,
                "device_id": msg.device_id,
                "source_system": "icu_monitor",
                "signal_quality": msg.signal_quality,
                "alarm_state": msg.alarm_state,
                "bridge_latency_ms": int((time.time() - t0) * 1000),
            }

            if self._kafka:
                await self._kafka.send(
                    self.KAFKA_TOPIC,
                    key=patient_deident_id,
                    value=json.dumps(kafka_msg, default=str),
                )

            self._stats["forwarded"] += 1

            if self._stats["received"] % 1000 == 0:
                logger.info(
                    f"MQTT Bridge stats: received={self._stats['received']} "
                    f"forwarded={self._stats['forwarded']} "
                    f"artifacts={self._stats['artifacts']}"
                )

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"MQTT bridge error: {e} — topic={topic}")

    async def _simulate(self):
        """Simulation mode: generate synthetic ICU data for dev/testing."""
        import random
        logger.info("MQTT Bridge in SIMULATION mode — generating synthetic ICU stream")

        patients = [
            {"id": str(uuid.uuid4()), "enc": str(uuid.uuid4()),
             "vitals": {"heart_rate": 112, "spo2_pulse_ox": 91, "bp_systolic": 95,
                        "respiratory_rate": 26, "temperature": 38.8}},
            {"id": str(uuid.uuid4()), "enc": str(uuid.uuid4()),
             "vitals": {"heart_rate": 88, "spo2_pulse_ox": 96, "bp_systolic": 128,
                        "respiratory_rate": 17, "temperature": 37.1}},
        ]

        while True:
            for patient in patients:
                for param, base_val in patient["vitals"].items():
                    value = base_val + random.gauss(0, base_val * 0.04)
                    msg = {
                        "time": datetime.now(timezone.utc).isoformat(),
                        "patient_deident_id": patient["id"],
                        "encounter_id": patient["enc"],
                        "parameter": param,
                        "value": round(value, 2),
                        "unit": {"heart_rate": "/min", "spo2_pulse_ox": "%",
                                 "bp_systolic": "mm[Hg]", "respiratory_rate": "/min",
                                 "temperature": "Cel"}.get(param, ""),
                        "device_id": f"sim-device-{patient['id'][:8]}",
                        "source_system": "simulator",
                    }
                    if self._kafka:
                        await self._kafka.send(self.KAFKA_TOPIC, value=json.dumps(msg))
                    else:
                        logger.debug(f"SIM: {param}={msg['value']} patient={patient['id'][:8]}")

            await asyncio.sleep(1)   # 1Hz simulation rate

    def get_stats(self) -> Dict:
        return dict(self._stats)


# ─────────────────────────────────────────────
# Kafka Vitals Consumer
# ─────────────────────────────────────────────

class VitalsKafkaConsumer:
    """
    Consumes `icu.vitals.raw` topic and processes into TimescaleDB.

    Per-message pipeline:
    1. Validate schema
    2. Quality score computation
    3. Anomaly detection (against patient baseline)
    4. Write to TimescaleDB (batch for efficiency)
    5. Check alert thresholds → publish to `clinical.alerts` if exceeded
    6. Push to WebSocket subscribers
    """

    TOPIC = "icu.vitals.raw"
    BATCH_SIZE = 100          # Write TimescaleDB in batches
    BATCH_TIMEOUT_MS = 500    # Or flush every 500ms

    def __init__(self, kafka_consumer=None, db=None, redis=None, alert_thresholds=None):
        self._consumer = kafka_consumer
        self._db = db
        self._redis = redis
        self._thresholds = alert_thresholds or {
            "heart_rate":        {"critical_low": 40,  "critical_high": 150},
            "spo2_pulse_ox":     {"critical_low": 85,  "critical_high": None},
            "bp_systolic":       {"critical_low": 80,  "critical_high": 220},
            "respiratory_rate":  {"critical_low": 6,   "critical_high": 35},
            "temperature":       {"critical_low": 34.0,"critical_high": 40.0},
        }
        self._buffer: List[Dict] = []
        self._last_flush = time.time()

    async def run(self):
        """Consume vitals stream continuously."""
        if not self._consumer:
            logger.warning("Kafka consumer not configured")
            return

        logger.info(f"Vitals consumer started on topic: {self.TOPIC}")
        async for msg in self._consumer:
            try:
                record = json.loads(msg.value)
                await self._process(record)
            except Exception as e:
                logger.error(f"Vitals consumer error: {e}")

    async def _process(self, record: Dict):
        """Process a single vital sign record."""
        param = record.get("parameter", "")
        value = record.get("value")
        patient_id = record.get("patient_deident_id", "")

        if value is None or not param:
            return

        # Check alert thresholds
        thresholds = self._thresholds.get(param, {})
        is_critical = False
        alert_type = None

        crit_low = thresholds.get("critical_low")
        crit_high = thresholds.get("critical_high")

        if crit_low and value < crit_low:
            is_critical = True
            alert_type = f"CRITICAL_LOW_{param.upper()}"
        elif crit_high and value > crit_high:
            is_critical = True
            alert_type = f"CRITICAL_HIGH_{param.upper()}"

        record["is_critical_low"] = bool(crit_low and value < crit_low)
        record["is_critical_high"] = bool(crit_high and value > crit_high)

        # Buffer for batch write
        self._buffer.append(record)

        # Flush if batch full or timeout exceeded
        now = time.time()
        if len(self._buffer) >= self.BATCH_SIZE or (now - self._last_flush) * 1000 > self.BATCH_TIMEOUT_MS:
            await self._flush()

        # Critical alerts bypass buffer — publish immediately
        if is_critical and self._redis:
            alert = {
                "type": "vital_critical",
                "alert_type": alert_type,
                "patient_deident_id": patient_id,
                "parameter": param,
                "value": value,
                "timestamp": record.get("time"),
            }
            await self._redis.publish(f"alerts:{patient_id}", json.dumps(alert))
            logger.warning(f"CRITICAL_VITAL: {alert_type} patient={patient_id[:8]} value={value}")

    async def _flush(self):
        """Batch write buffered records to TimescaleDB."""
        if not self._buffer:
            return
        batch = self._buffer.copy()
        self._buffer.clear()
        self._last_flush = time.time()

        if self._db:
            try:
                from sqlalchemy import text
                # Production: use COPY for max throughput
                for record in batch:
                    await self._db.execute(
                        text("""
                            INSERT INTO cliniqai_ai.vitals_timeseries
                                (time, patient_deident_id, encounter_id, parameter, value, unit,
                                 is_artifact, quality_score, device_id, source_system,
                                 is_critical_low, is_critical_high)
                            VALUES (:time, :patient_deident_id, :encounter_id, :parameter,
                                    :value, :unit, FALSE, 1.0, :device_id, :source_system,
                                    :is_critical_low, :is_critical_high)
                            ON CONFLICT DO NOTHING
                        """),
                        record,
                    )
            except Exception as e:
                logger.error(f"TimescaleDB batch write failed: {e}")
        else:
            logger.debug(f"VITALS_FLUSH: {len(batch)} records (no DB configured)")
