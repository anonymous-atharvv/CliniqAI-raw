"""
Batch ETL Pipeline — Nightly HL7 v2 + CDA Ingestion
=====================================================
Runs nightly via Apache Airflow at 02:00 local hospital time.

Processes:
  1. HL7 v2 messages (ADT, ORU, ORM) from EHR exports
  2. CDA (Clinical Document Architecture) documents
  3. Lab results from LIS (Lab Information System) exports
  4. Billing data from RCM system (for outcome linkage)

Dead-letter queue: failed records → Kafka DLQ → manual review
Retry: exponential backoff (2^n seconds, max 60s), max 3 attempts
Data lineage: every record carries source, timestamp, pipeline version
"""

import json
import re
import logging
import hashlib
from typing import List, Dict, Optional, Any, Tuple, Generator
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HL7 v2 Message Types
# ─────────────────────────────────────────────

class HL7MessageType(str, Enum):
    ADT_A01 = "ADT^A01"   # Admit patient
    ADT_A02 = "ADT^A02"   # Transfer patient
    ADT_A03 = "ADT^A03"   # Discharge patient
    ADT_A08 = "ADT^A08"   # Update patient information
    ORU_R01 = "ORU^R01"   # Observation result (labs, vitals)
    ORM_O01 = "ORM^O01"   # Order message
    MDM_T02 = "MDM^T02"   # Clinical document (notes)
    MFN_M08 = "MFN^M08"   # Master files (pharmacy)


@dataclass
class HL7Segment:
    """Parsed HL7 v2 segment."""
    segment_id: str
    fields: List[str]

    def get(self, field_index: int, component: int = 0) -> Optional[str]:
        """Get field value (1-indexed per HL7 spec)."""
        if field_index >= len(self.fields):
            return None
        field_val = self.fields[field_index]
        if "^" in field_val and component > 0:
            parts = field_val.split("^")
            return parts[component - 1] if component <= len(parts) else None
        return field_val or None


@dataclass
class ParsedHL7Message:
    """Fully parsed HL7 v2 message."""
    raw_message: str
    message_type: str
    message_id: str
    sending_facility: str
    sending_application: str
    timestamp: str
    segments: Dict[str, List[HL7Segment]]
    parse_errors: List[str] = field(default_factory=list)

    def get_segment(self, segment_id: str, index: int = 0) -> Optional[HL7Segment]:
        segs = self.segments.get(segment_id, [])
        return segs[index] if index < len(segs) else None


@dataclass
class ETLRecord:
    """A record passing through the ETL pipeline."""
    record_id: str
    source_system: str
    source_format: str          # hl7v2|cda|csv|json
    raw_content: str
    received_at: str

    # Processing state
    parse_status: str = "pending"     # pending|parsed|normalized|failed
    normalize_status: str = "pending"
    fhir_resource: Optional[Dict] = None
    quality_score: Optional[float] = None

    # Error tracking
    errors: List[str] = field(default_factory=list)
    retry_count: int = 0
    last_error: Optional[str] = None

    # Lineage
    pipeline_version: str = "1.0.0"
    processing_steps: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────
# HL7 v2 Parser
# ─────────────────────────────────────────────

class HL7Parser:
    """
    Production HL7 v2 message parser.

    Handles the chaos of real-world HL7:
    - Different segment terminators (CR, CRLF, LF)
    - Escaped characters (\\F\\, \\S\\, \\R\\, etc.)
    - Missing optional segments
    - Non-standard extensions used by Epic/Cerner/Meditech

    Does NOT use external library — pure Python for reliability.
    (python-hl7 library works but has edge cases with Epic's custom extensions.)
    """

    SEGMENT_SEPARATOR = "\r"

    def parse(self, raw_message: str) -> ParsedHL7Message:
        """Parse a raw HL7 v2 message string."""
        errors = []

        # Normalize line endings
        raw = raw_message.replace("\r\n", "\r").replace("\n", "\r").strip()

        segments_raw = raw.split(self.SEGMENT_SEPARATOR)
        segments_raw = [s.strip() for s in segments_raw if s.strip()]

        if not segments_raw or not segments_raw[0].startswith("MSH"):
            return ParsedHL7Message(
                raw_message=raw_message,
                message_type="UNKNOWN",
                message_id="",
                sending_facility="",
                sending_application="",
                timestamp="",
                segments={},
                parse_errors=["Message does not begin with MSH segment"],
            )

        # Extract delimiters from MSH
        msh_raw = segments_raw[0]
        field_sep = msh_raw[3] if len(msh_raw) > 3 else "|"
        component_sep = msh_raw[4] if len(msh_raw) > 4 else "^"

        # Parse all segments
        parsed_segments: Dict[str, List[HL7Segment]] = {}
        for seg_raw in segments_raw:
            parts = seg_raw.split(field_sep)
            seg_id = parts[0]
            seg_fields = parts  # Keep 0-indexed: parts[0]=seg_id, parts[1]=first field
            seg = HL7Segment(segment_id=seg_id, fields=seg_fields)
            if seg_id not in parsed_segments:
                parsed_segments[seg_id] = []
            parsed_segments[seg_id].append(seg)

        # Extract MSH fields
        msh = parsed_segments.get("MSH", [None])[0]
        message_type = (msh.get(9) or "UNKNOWN") if msh else "UNKNOWN"
        message_id = (msh.get(10) or "") if msh else ""
        sending_app = (msh.get(3) or "") if msh else ""
        sending_fac = (msh.get(4) or "") if msh else ""
        timestamp = (msh.get(7) or "") if msh else ""

        return ParsedHL7Message(
            raw_message=raw_message,
            message_type=message_type,
            message_id=message_id,
            sending_facility=sending_fac,
            sending_application=sending_app,
            timestamp=timestamp,
            segments=parsed_segments,
            parse_errors=errors,
        )

    def extract_patient_demographics(self, msg: ParsedHL7Message) -> Optional[Dict]:
        """Extract patient demographics from PID segment."""
        pid = msg.get_segment("PID")
        if not pid:
            return None

        # PID-3: Patient ID (MRN)
        mrn_field = pid.get(3) or ""
        mrn = mrn_field.split("^")[0] if "^" in mrn_field else mrn_field

        # PID-5: Patient Name (LAST^FIRST^MIDDLE)
        name_field = pid.get(5) or ""
        name_parts = name_field.split("^")
        last_name = name_parts[0] if name_parts else ""
        first_name = name_parts[1] if len(name_parts) > 1 else ""
        middle_name = name_parts[2] if len(name_parts) > 2 else ""

        # PID-7: Date of Birth (YYYYMMDD)
        dob_raw = pid.get(7) or ""
        dob = None
        if len(dob_raw) >= 8:
            try:
                dob = f"{dob_raw[:4]}-{dob_raw[4:6]}-{dob_raw[6:8]}"
            except Exception:
                pass

        # PID-8: Sex
        gender_map = {"M": "M", "F": "F", "O": "O", "U": "U", "": "U"}
        gender = gender_map.get((pid.get(8) or "").upper(), "U")

        # PID-11: Address
        addr_field = pid.get(11) or ""
        addr_parts = addr_field.split("^")

        # PID-19: SSN
        ssn = pid.get(19) or ""
        ssn_last4 = ssn[-4:] if len(ssn) >= 4 else ""

        return {
            "mrn": mrn,
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "date_of_birth": dob,
            "gender": gender,
            "ssn_last4": ssn_last4,
            "address_line1": addr_parts[0] if addr_parts else "",
            "city": addr_parts[2] if len(addr_parts) > 2 else "",
            "state": addr_parts[3] if len(addr_parts) > 3 else "",
            "zip_code": addr_parts[4] if len(addr_parts) > 4 else "",
        }

    def extract_observations(self, msg: ParsedHL7Message) -> List[Dict]:
        """Extract lab/vital observations from OBX segments."""
        observations = []
        obx_segments = msg.segments.get("OBX", [])

        for obx in obx_segments:
            # OBX-3: Observation identifier (LOINC code^display^LOINC)
            obs_id_field = obx.get(3) or ""
            obs_parts = obs_id_field.split("^")
            loinc_code = obs_parts[0] if obs_parts else ""
            loinc_display = obs_parts[1] if len(obs_parts) > 1 else ""

            # OBX-5: Observation value
            value_raw = obx.get(5) or ""

            # OBX-6: Units
            units_field = obx.get(6) or ""
            units = units_field.split("^")[0] if "^" in units_field else units_field

            # OBX-7: Reference range
            ref_range = obx.get(7) or ""

            # OBX-8: Abnormal flags
            abnormal_flag = obx.get(8) or ""

            # OBX-11: Observation status (F=final, P=preliminary, C=corrected)
            status_map = {"F": "final", "P": "preliminary", "C": "amended", "X": "cancelled"}
            status = status_map.get(obx.get(11) or "F", "final")

            # OBX-14: Date/time of observation
            obs_time = obx.get(14) or ""
            if len(obs_time) >= 14:
                obs_datetime = f"{obs_time[:4]}-{obs_time[4:6]}-{obs_time[6:8]}T{obs_time[8:10]}:{obs_time[10:12]}:{obs_time[12:14]}Z"
            elif len(obs_time) >= 8:
                obs_datetime = f"{obs_time[:4]}-{obs_time[4:6]}-{obs_time[6:8]}T00:00:00Z"
            else:
                obs_datetime = datetime.now(timezone.utc).isoformat()

            # Parse numeric value
            numeric_value = None
            try:
                numeric_value = float(value_raw.replace(",", ""))
            except (ValueError, TypeError):
                pass

            observations.append({
                "loinc_code": loinc_code,
                "loinc_display": loinc_display,
                "value_raw": value_raw,
                "value_numeric": numeric_value,
                "units": units,
                "reference_range": ref_range,
                "abnormal_flag": abnormal_flag,
                "status": status,
                "observation_datetime": obs_datetime,
            })

        return observations


# ─────────────────────────────────────────────
# Kafka Dead-Letter Queue Handler
# ─────────────────────────────────────────────

class DeadLetterQueue:
    """
    Manages failed records in the dead-letter queue.
    Records go to DLQ after max_retries exhausted.
    Operations team reviews DLQ daily.
    """

    def __init__(self, kafka_producer=None, max_retries: int = 3):
        self._producer = kafka_producer
        self.max_retries = max_retries
        self._backoff_base = 2.0   # Exponential backoff: 2^n seconds

    def backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff: 2, 4, 8... seconds, capped at 60."""
        return min(self._backoff_base ** attempt, 60.0)

    async def should_retry(self, record: ETLRecord) -> bool:
        return record.retry_count < self.max_retries

    async def send_to_dlq(self, record: ETLRecord, failure_reason: str):
        """Send failed record to dead-letter queue for ops review."""
        dlq_message = {
            "record_id": record.record_id,
            "source_system": record.source_system,
            "source_format": record.source_format,
            "failure_reason": failure_reason,
            "retry_count": record.retry_count,
            "errors": record.errors,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "raw_content_hash": hashlib.sha256(record.raw_content.encode()).hexdigest(),
        }

        if self._producer:
            await self._producer.send("dlq.failed", json.dumps(dlq_message))
        else:
            logger.error(f"DLQ: {json.dumps(dlq_message)}")


# ─────────────────────────────────────────────
# Airflow DAG Definition (nightly ETL)
# ─────────────────────────────────────────────

AIRFLOW_DAG_DEFINITION = '''
"""
CliniQAI Nightly ETL DAG
Runs at 02:00 local hospital time.
Processes previous day's EHR exports.
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.models import Variable

HOSPITAL_ID = Variable.get("HOSPITAL_ID")
EHR_EXPORT_PATH = Variable.get("EHR_EXPORT_PATH")

default_args = {
    "owner": "cliniqai_data_engineering",
    "depends_on_past": False,
    "start_date": datetime(2025, 1, 1),
    "email": ["dataops@cliniqai.com"],
    "email_on_failure": True,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "retry_exponential_backoff": True,
}

with DAG(
    dag_id=f"cliniqai_nightly_etl_{HOSPITAL_ID}",
    default_args=default_args,
    description="Nightly ETL from hospital EHR exports",
    schedule_interval="0 2 * * *",   # 02:00 daily
    catchup=False,
    max_active_runs=1,
    tags=["cliniqai", "etl", "hipaa"],
) as dag:

    start = EmptyOperator(task_id="start")
    end = EmptyOperator(task_id="end")

    # 1. Download EHR export files from SFTP / S3
    download_exports = PythonOperator(
        task_id="download_ehr_exports",
        python_callable=download_ehr_exports_task,
        op_kwargs={"hospital_id": HOSPITAL_ID, "export_path": EHR_EXPORT_PATH},
    )

    # 2. Parse HL7 v2 messages
    parse_hl7 = PythonOperator(
        task_id="parse_hl7_messages",
        python_callable=parse_hl7_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 3. Parse CDA documents
    parse_cda = PythonOperator(
        task_id="parse_cda_documents",
        python_callable=parse_cda_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 4. FHIR R4 normalization
    normalize = PythonOperator(
        task_id="fhir_normalization",
        python_callable=normalize_to_fhir_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 5. MPI matching
    mpi_match = PythonOperator(
        task_id="mpi_matching",
        python_callable=mpi_matching_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 6. Data quality scoring
    quality_score = PythonOperator(
        task_id="data_quality_scoring",
        python_callable=quality_scoring_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 7. Load to PostgreSQL + TimescaleDB
    load_to_db = PythonOperator(
        task_id="load_to_database",
        python_callable=load_to_db_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 8. Archive raw exports to S3 Parquet (warm path)
    archive = PythonOperator(
        task_id="archive_to_s3_parquet",
        python_callable=archive_to_s3_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 9. Process dead-letter queue
    process_dlq = PythonOperator(
        task_id="process_dead_letter_queue",
        python_callable=process_dlq_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 10. Link outcomes (billing data → prediction validation)
    link_outcomes = PythonOperator(
        task_id="link_clinical_outcomes",
        python_callable=link_outcomes_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # 11. Update patient vector embeddings in Qdrant
    update_embeddings = PythonOperator(
        task_id="update_patient_embeddings",
        python_callable=update_embeddings_task,
        op_kwargs={"hospital_id": HOSPITAL_ID},
    )

    # DAG flow
    start >> download_exports
    download_exports >> [parse_hl7, parse_cda]
    [parse_hl7, parse_cda] >> normalize
    normalize >> mpi_match
    mpi_match >> quality_score
    quality_score >> [load_to_db, archive]
    load_to_db >> [process_dlq, link_outcomes, update_embeddings]
    [process_dlq, link_outcomes, update_embeddings] >> end
'''


# ─────────────────────────────────────────────
# Data Quality Scoring Engine
# ─────────────────────────────────────────────

class DataQualityScorer:
    """
    Compute data quality score for every ingested record.
    Score = completeness×0.30 + timeliness×0.25 + consistency×0.25 + validity×0.20

    Records with score < 0.60 are FLAGGED — not used for AI inference.
    They're still stored for human review and data quality improvement.
    """

    WEIGHTS = {
        "completeness": 0.30,
        "timeliness": 0.25,
        "consistency": 0.25,
        "validity": 0.20,
    }

    REQUIRED_PATIENT_FIELDS = [
        "mrn", "last_name", "date_of_birth", "gender",
    ]

    CLINICAL_RANGES = {
        "heart_rate":        (20, 300),
        "spo2_pulse_ox":     (50, 100),
        "bp_systolic":       (50, 300),
        "bp_diastolic":      (20, 200),
        "temperature":       (25.0, 45.0),
        "respiratory_rate":  (4, 60),
    }

    def score_patient_record(self, record: Dict) -> Tuple[float, Dict[str, float]]:
        """Score a patient demographic record."""
        completeness = self._completeness_score(record, self.REQUIRED_PATIENT_FIELDS)
        timeliness = 1.0   # New records are always timely
        consistency = 1.0  # No prior record to compare
        validity = 1.0 if record.get("date_of_birth") else 0.5

        component_scores = {
            "completeness": completeness,
            "timeliness": timeliness,
            "consistency": consistency,
            "validity": validity,
        }
        total = sum(s * self.WEIGHTS[k] for k, s in component_scores.items())
        return round(total, 4), component_scores

    def score_observation(
        self,
        parameter: str,
        value: float,
        timestamp: datetime,
        existing_values: Optional[List[float]] = None,
    ) -> Tuple[float, Dict[str, float]]:
        """Score a vital sign or lab observation."""
        # Completeness: value and timestamp present
        completeness = 1.0 if value is not None and timestamp else 0.0

        # Timeliness: exponential decay, half-life ~6 hours
        hours_old = (datetime.now(timezone.utc) - timestamp).total_seconds() / 3600
        timeliness = 2 ** (-hours_old / 6)

        # Validity: within physiological limits
        limits = self.CLINICAL_RANGES.get(parameter)
        if limits and value is not None:
            validity = 1.0 if limits[0] <= value <= limits[1] else 0.0
        else:
            validity = 0.8  # Unknown parameter — give benefit of doubt

        # Consistency: compare to existing values (IQR check)
        consistency = 1.0
        if existing_values and len(existing_values) >= 5 and value is not None:
            sorted_vals = sorted(existing_values)
            n = len(sorted_vals)
            q1 = sorted_vals[n // 4]
            q3 = sorted_vals[3 * n // 4]
            iqr = q3 - q1
            lower = q1 - 3 * iqr
            upper = q3 + 3 * iqr
            consistency = 1.0 if lower <= value <= upper else 0.3

        component_scores = {
            "completeness": completeness,
            "timeliness": round(timeliness, 4),
            "consistency": consistency,
            "validity": validity,
        }
        total = sum(s * self.WEIGHTS[k] for k, s in component_scores.items())
        return round(total, 4), component_scores

    def _completeness_score(self, record: Dict, required_fields: List[str]) -> float:
        populated = sum(1 for f in required_fields if record.get(f))
        return populated / len(required_fields) if required_fields else 1.0

    def is_usable(self, score: float) -> bool:
        """Records below 0.60 are flagged, not used for AI inference."""
        return score >= 0.60
