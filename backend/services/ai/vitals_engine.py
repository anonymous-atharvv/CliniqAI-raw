"""
Vitals Time-Series AI Engine (Layer 3 — Multi-Modal AI)

Architecture: Temporal Fusion Transformer (TFT)
Pre-trained on: MIMIC-IV dataset (50,000+ ICU patients, Beth Israel Deaconess Medical Center)
Fine-tuned on: Local hospital ICU data (after collection)

Tasks:
a) Anomaly detection — statistical + learned bounds per patient baseline
b) Deterioration prediction — predict clinical deterioration 6 hours ahead
   Training signal: Modified Early Warning Score (MEWS) transitions
c) Sepsis early warning — predict sepsis onset 12 hours ahead
   Training signal: Sepsis-3 criteria (SOFA ≥ 2 + suspected infection)
d) Ventilator weaning readiness — predict successful extubation
   Training signal: Spontaneous breathing trial outcomes

CRITICAL: Models must output calibrated uncertainty (Monte Carlo Dropout).
Overconfident AI in medicine kills people.
An AI confident at 0.85 that's actually only 0.60 accurate will get patients hurt.

VALIDATION REQUIREMENTS (before clinical deployment):
- Sepsis prediction: AUROC > 0.85, sensitivity > 0.80 at specificity 0.85
- Deterioration: AUROC > 0.80
- Must run 30-day shadow mode at each new hospital
"""

import numpy as np
import logging
import time
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Alert Thresholds (configurable per hospital)
# Hospital can override these in their config
# ─────────────────────────────────────────────

DEFAULT_ALERT_THRESHOLDS = {
    "news2_high_alert": 5,
    "deterioration_6h_threshold": 0.70,
    "sepsis_12h_threshold": 0.50,
    "mortality_24h_threshold": 0.40,
    "weaning_readiness_threshold": 0.75,
}

# Physiological bounds for anomaly detection
VITAL_BOUNDS = {
    "heart_rate":           {"mean": 80,  "std": 15,  "min": 20,  "max": 300},
    "spo2_pulse_ox":        {"mean": 97,  "std": 2,   "min": 50,  "max": 100},
    "bp_systolic":          {"mean": 120, "std": 20,  "min": 50,  "max": 300},
    "bp_diastolic":         {"mean": 80,  "std": 12,  "min": 20,  "max": 200},
    "bp_mean":              {"mean": 93,  "std": 15,  "min": 40,  "max": 200},
    "respiratory_rate":     {"mean": 16,  "std": 4,   "min": 4,   "max": 60},
    "temperature":          {"mean": 37.0,"std": 0.5, "min": 25,  "max": 45},
    "gcs_total":            {"mean": 14,  "std": 2,   "min": 3,   "max": 15},
}


class TrendDirection(str, Enum):
    IMPROVING = "improving"
    STABLE = "stable"
    WORSENING = "worsening"
    CRITICAL_CHANGE = "critical_change"


@dataclass
class VitalAnomaly:
    """A detected anomaly in a vital sign time series."""
    parameter: str
    current_value: float
    expected_range: Tuple[float, float]
    patient_baseline: Optional[float]  # Patient-specific baseline (not population mean)
    deviation_sigma: float             # How many standard deviations from baseline
    anomaly_type: str                  # "spike" | "drop" | "sustained_high" | "sustained_low" | "trend"
    severity: str                      # "mild" | "moderate" | "severe" | "critical"
    timestamp: str
    triggered_alert: bool


@dataclass
class VitalsTFTPrediction:
    """
    Output from the Temporal Fusion Transformer.
    
    IMPORTANT: confidence is calibrated probability (from MC Dropout).
    Do NOT display raw model logits to clinicians.
    uncertainty = epistemic uncertainty (how sure the model is)
    """
    patient_id: str
    timestamp: str
    
    # Clinical severity scores (rule-based, high reliability)
    news2_score: int
    sofa_score: Optional[int]
    mews_score: int
    
    # AI predictions (TFT model outputs)
    deterioration_6h: float       # P(clinical deterioration in 6h)
    sepsis_12h: float             # P(sepsis onset in 12h)
    mortality_24h: float          # P(mortality in 24h)
    weaning_readiness: Optional[float]  # P(successful extubation) — ICU only
    
    # Uncertainty quantification (Monte Carlo Dropout)
    deterioration_uncertainty: float   # Epistemic uncertainty
    sepsis_uncertainty: float
    
    # Trend analysis
    trend: TrendDirection
    anomalies: List[VitalAnomaly]
    
    # Alerts
    active_alerts: List[str]
    alert_priority: str  # "NONE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    
    # Model metadata
    model_version: str = "tft-v1.0-mimic4"
    inference_ms: int = 0


@dataclass
class PatientBaseline:
    """
    Patient-specific vital sign baseline.
    Computed from first 24–72 hours of admission.
    
    Why patient-specific baselines?
    A chronically hypertensive patient with BP 160/95 is their "normal".
    An anomaly detector using population means would constantly alarm on them.
    Patient baselines dramatically reduce false positive alert rates.
    """
    patient_id: str
    computed_at: str
    observation_hours: int
    
    baselines: Dict[str, Dict[str, float]]  # parameter → {mean, std, n_samples}
    
    @property
    def is_mature(self) -> bool:
        """Baseline is reliable after 24+ hours of observation."""
        return self.observation_hours >= 24


class VitalSignPreprocessor:
    """
    Prepares raw vital sign streams for model input.
    
    Handles:
    - Missing values (ICU monitors miss readings)
    - Artifact removal (movement artifacts, disconnected sensors)
    - Resampling to uniform 1-minute intervals
    - Feature engineering (rolling windows, rate of change)
    - Normalization per patient baseline
    """
    
    WINDOW_SIZES = [5, 15, 60]  # Minutes: short, medium, long-term trends
    
    def preprocess(
        self,
        vitals_stream: List[Dict],  # Raw readings from Kafka
        patient_baseline: Optional[PatientBaseline],
        window_hours: int = 6,
    ) -> Dict[str, Any]:
        """
        Process raw vital sign stream into model-ready features.
        
        Input: List of raw vital sign readings from last N hours
        Output: Feature tensor for TFT model
        """
        if not vitals_stream:
            return {"error": "empty_stream", "features": None}
        
        # Sort by timestamp
        sorted_vitals = sorted(vitals_stream, key=lambda x: x.get("time", x.get("timestamp", "")))
        
        # Extract per-parameter time series
        series = {}
        for vital in sorted_vitals:
            param = vital.get("parameter")
            value = vital.get("value")
            ts = vital.get("time") or vital.get("timestamp")
            
            if param and value is not None and ts:
                if param not in series:
                    series[param] = []
                series[param].append({"timestamp": ts, "value": float(value)})
        
        # Compute features per parameter
        features = {}
        for param, readings in series.items():
            if param not in VITAL_BOUNDS:
                continue
            
            values = [r["value"] for r in readings]
            
            # Basic statistics
            features[f"{param}_mean"] = np.mean(values)
            features[f"{param}_std"] = np.std(values) if len(values) > 1 else 0.0
            features[f"{param}_min"] = np.min(values)
            features[f"{param}_max"] = np.max(values)
            features[f"{param}_last"] = values[-1] if values else 0.0
            
            # Trend: linear regression slope
            if len(values) >= 3:
                x = np.arange(len(values))
                slope = np.polyfit(x, values, 1)[0]
                features[f"{param}_slope"] = slope
            else:
                features[f"{param}_slope"] = 0.0
            
            # Normalized deviation from baseline
            if patient_baseline and param in patient_baseline.baselines:
                baseline = patient_baseline.baselines[param]
                bl_mean = baseline.get("mean", VITAL_BOUNDS[param]["mean"])
                bl_std = baseline.get("std", VITAL_BOUNDS[param]["std"])
                if bl_std > 0:
                    features[f"{param}_z_score"] = (values[-1] - bl_mean) / bl_std
                else:
                    features[f"{param}_z_score"] = 0.0
            else:
                # Fall back to population baseline
                pop_mean = VITAL_BOUNDS[param]["mean"]
                pop_std = VITAL_BOUNDS[param]["std"]
                features[f"{param}_z_score"] = (values[-1] - pop_mean) / pop_std
            
            # Variability (high variability = instability)
            features[f"{param}_cv"] = (
                (np.std(values) / np.mean(values)) * 100
                if np.mean(values) != 0 else 0.0
            )
        
        return {
            "features": features,
            "n_readings": len(sorted_vitals),
            "time_window_hours": window_hours,
            "params_available": list(series.keys()),
        }
    
    def detect_artifacts(self, value: float, parameter: str) -> bool:
        """
        Detect physiologically impossible values (sensor artifacts).
        
        Returns True if the value is an artifact and should be discarded.
        """
        bounds = VITAL_BOUNDS.get(parameter)
        if not bounds:
            return False
        
        # Hard physiological limits
        if value < bounds["min"] or value > bounds["max"]:
            return True
        
        # Zero values (common sensor artifact)
        if value == 0 and parameter in ["heart_rate", "spo2_pulse_ox", "bp_systolic"]:
            return True
        
        return False


class MockTFTModel:
    """
    Mock TFT model for development without actual ML infrastructure.
    
    In production: replace with actual PyTorch TFT or ONNX model.
    The real model is pre-trained on MIMIC-IV and fine-tuned on local data.
    
    Monte Carlo Dropout: run inference N=20 times with dropout active.
    Mean of predictions = calibrated probability.
    Std of predictions = epistemic uncertainty.
    """
    
    def predict(self, features: Dict[str, float], n_mc_samples: int = 20) -> Dict[str, Any]:
        """
        Run inference with Monte Carlo Dropout for uncertainty estimation.
        
        Real implementation:
        1. Set model.train() to enable dropout
        2. Run N forward passes
        3. Return mean + std of predictions
        
        Here: simulate with rule-based logic for dev testing.
        """
        predictions = []
        
        # Simulate N MC Dropout runs
        for _ in range(n_mc_samples):
            pred = self._single_forward(features)
            predictions.append(pred)
        
        # Aggregate MC samples
        result = {}
        for key in predictions[0].keys():
            values = [p[key] for p in predictions]
            result[f"{key}_mean"] = float(np.mean(values))
            result[f"{key}_std"] = float(np.std(values))
        
        return result
    
    def _single_forward(self, features: Dict[str, float]) -> Dict[str, float]:
        """
        Single forward pass — rule-based TFT approximation for development.
        Production: actual PyTorch TFT model from MIMIC-IV pretraining.
        
        Design: deterministic core + tiny noise so tests are reliable,
        but predictions clearly scale with clinical severity.
        """
        # Extract vital sign features
        hr   = features.get("heart_rate_last", 80)
        spo2 = features.get("spo2_pulse_ox_last", 97)
        sbp  = features.get("bp_systolic_last", 120)
        rr   = features.get("respiratory_rate_last", 16)
        temp = features.get("temperature_last", 37.0)

        # Slope features (trend over window)
        hr_slope  = features.get("heart_rate_slope", 0)
        sbp_slope = features.get("bp_systolic_slope", 0)

        # ── Deterioration risk (0–1) ─────────────────────────────────────────
        deterioration_risk = 0.05  # low baseline

        # Heart rate: tachycardia is the most reliable deterioration signal
        if hr > 130:     deterioration_risk += 0.30
        elif hr > 120:   deterioration_risk += 0.22
        elif hr > 110:   deterioration_risk += 0.14
        elif hr > 100:   deterioration_risk += 0.07
        elif hr < 40:    deterioration_risk += 0.35
        elif hr < 50:    deterioration_risk += 0.20

        # SpO2: hypoxemia is life-threatening
        if spo2 < 85:    deterioration_risk += 0.40
        elif spo2 < 88:  deterioration_risk += 0.30
        elif spo2 < 90:  deterioration_risk += 0.22
        elif spo2 < 94:  deterioration_risk += 0.12
        elif spo2 < 96:  deterioration_risk += 0.04

        # Blood pressure: hypotension is an emergency
        if sbp < 80:     deterioration_risk += 0.40
        elif sbp < 90:   deterioration_risk += 0.28
        elif sbp < 100:  deterioration_risk += 0.15

        # Respiratory rate: tachypnoea signals respiratory distress
        if rr > 30:      deterioration_risk += 0.25
        elif rr > 25:    deterioration_risk += 0.18
        elif rr > 20:    deterioration_risk += 0.08
        elif rr < 6:     deterioration_risk += 0.35
        elif rr < 8:     deterioration_risk += 0.20

        # Temperature extremes
        if temp > 39.5:  deterioration_risk += 0.10
        elif temp > 38.5:deterioration_risk += 0.05
        elif temp < 35.0:deterioration_risk += 0.15

        # Trend amplifiers: sustained worsening increases risk
        if hr_slope > 3:   deterioration_risk += 0.12
        elif hr_slope > 1: deterioration_risk += 0.06
        if sbp_slope < -3: deterioration_risk += 0.15
        elif sbp_slope < -1:deterioration_risk += 0.07

        # ── Sepsis risk (0–1) ─────────────────────────────────────────────────
        # Sepsis-3 SIRS-like criteria: fever/hypothermia + tachycardia +
        # tachypnoea + signs of infection
        sepsis_risk = 0.02  # low baseline

        # Fever is the strongest sepsis signal
        if temp > 39.5:  sepsis_risk += 0.28
        elif temp > 39.0:sepsis_risk += 0.22
        elif temp > 38.5:sepsis_risk += 0.15
        elif temp > 38.0:sepsis_risk += 0.08
        elif temp < 35.5:sepsis_risk += 0.20  # hypothermia also sepsis sign
        elif temp < 36.0:sepsis_risk += 0.10

        # Tachycardia (Sepsis-3 criterion: HR > 90)
        if hr > 120:     sepsis_risk += 0.25
        elif hr > 110:   sepsis_risk += 0.18
        elif hr > 100:   sepsis_risk += 0.12

        # Tachypnoea (Sepsis-3 criterion: RR > 22)
        if rr > 28:      sepsis_risk += 0.20
        elif rr > 24:    sepsis_risk += 0.14
        elif rr > 20:    sepsis_risk += 0.08

        # Hypotension (septic shock criterion)
        if sbp < 90:     sepsis_risk += 0.30
        elif sbp < 100:  sepsis_risk += 0.18
        elif sbp < 110:  sepsis_risk += 0.08

        # Hypoxemia (organ dysfunction)
        if spo2 < 90:    sepsis_risk += 0.15
        elif spo2 < 94:  sepsis_risk += 0.08

        # Add tiny noise for MC dropout simulation (kept very small for test stability)
        noise = np.random.normal(0, 0.005)

        return {
            "deterioration_6h": min(0.99, max(0.01, deterioration_risk + noise)),
            "sepsis_12h":       min(0.99, max(0.01, sepsis_risk + noise)),
            "mortality_24h":    min(0.99, max(0.01,
                                    deterioration_risk * 0.55 + sepsis_risk * 0.45 + noise)),
        }


class VitalsTFTEngine:
    """
    Main engine for vitals-based AI predictions.
    
    Orchestrates:
    1. Preprocessing → artifact removal, feature engineering
    2. Baseline management → patient-specific reference
    3. Model inference → TFT predictions + MC uncertainty
    4. Anomaly detection → statistical + rule-based
    5. Clinical scoring → NEWS2, MEWS, SOFA
    6. Alert generation → threshold-based alerts
    """
    
    def __init__(
        self,
        model=None,
        thresholds: Optional[Dict] = None,
    ):
        self._model = model or MockTFTModel()
        self._preprocessor = VitalSignPreprocessor()
        self._thresholds = {**DEFAULT_ALERT_THRESHOLDS, **(thresholds or {})}
        self._baselines: Dict[str, PatientBaseline] = {}  # patient_id → baseline
    
    def analyze(
        self,
        patient_id: str,
        vitals_stream: List[Dict],
        patient_context: Optional[Dict] = None,
    ) -> VitalsTFTPrediction:
        """
        Full analysis pipeline for a patient's vitals stream.
        
        Called every 15 minutes for ICU patients.
        Must complete in < 5 seconds (soft real-time requirement).
        """
        start_time = time.time()
        
        # Get or compute patient baseline
        baseline = self._baselines.get(patient_id)
        
        # Preprocess vitals
        preprocessed = self._preprocessor.preprocess(
            vitals_stream=vitals_stream,
            patient_baseline=baseline,
            window_hours=6,
        )
        
        if preprocessed.get("error") or preprocessed.get("features") is None:
            return self._empty_prediction(patient_id, "insufficient_data")
        
        features = preprocessed["features"]
        
        # Get latest vitals for scoring
        latest = self._extract_latest(vitals_stream)
        
        # Rule-based clinical scores (high reliability, no ML needed)
        news2 = self._calculate_news2(latest)
        mews = self._calculate_mews(latest)
        sofa = self._estimate_sofa(latest, patient_context or {})
        
        # AI model predictions (TFT with MC Dropout)
        model_output = self._model.predict(features, n_mc_samples=20)
        
        det_6h = model_output.get("deterioration_6h_mean", 0.1)
        det_unc = model_output.get("deterioration_6h_std", 0.1)
        sep_12h = model_output.get("sepsis_12h_mean", 0.05)
        sep_unc = model_output.get("sepsis_12h_std", 0.05)
        mort_24h = model_output.get("mortality_24h_mean", 0.05)
        
        # Anomaly detection
        anomalies = self._detect_anomalies(latest, baseline)
        
        # Trend determination
        trend = self._compute_trend(vitals_stream)
        
        # Alert generation
        alerts, priority = self._generate_alerts(
            news2=news2,
            det_6h=det_6h,
            sep_12h=sep_12h,
            mort_24h=mort_24h,
            anomalies=anomalies,
        )
        
        inference_ms = int((time.time() - start_time) * 1000)
        
        return VitalsTFTPrediction(
            patient_id=patient_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            news2_score=news2,
            sofa_score=sofa,
            mews_score=mews,
            deterioration_6h=round(det_6h, 4),
            sepsis_12h=round(sep_12h, 4),
            mortality_24h=round(mort_24h, 4),
            weaning_readiness=None,  # Computed separately for ventilated patients
            deterioration_uncertainty=round(det_unc, 4),
            sepsis_uncertainty=round(sep_unc, 4),
            trend=trend,
            anomalies=anomalies,
            active_alerts=alerts,
            alert_priority=priority,
            inference_ms=inference_ms,
        )
    
    def update_baseline(
        self,
        patient_id: str,
        vitals_stream: List[Dict],
        observation_hours: int,
    ) -> PatientBaseline:
        """
        Compute or update patient-specific baseline from observed vitals.
        
        Called after 24 hours of new admission.
        Updates dynamically as patient stabilizes.
        
        IMPORTANT: Baseline is patient-specific, NOT population-mean.
        A COPD patient's SpO2 of 88% may be their baseline, not an emergency.
        """
        baselines = {}
        
        # Group by parameter
        by_param: Dict[str, List[float]] = {}
        for reading in vitals_stream:
            param = reading.get("parameter")
            value = reading.get("value")
            if param and value is not None:
                # Skip artifacts
                if not self._preprocessor.detect_artifacts(float(value), param):
                    if param not in by_param:
                        by_param[param] = []
                    by_param[param].append(float(value))
        
        for param, values in by_param.items():
            if len(values) >= 10:  # Minimum samples for reliable baseline
                baselines[param] = {
                    "mean": float(np.mean(values)),
                    "std": float(np.std(values)),
                    "median": float(np.median(values)),
                    "p10": float(np.percentile(values, 10)),
                    "p90": float(np.percentile(values, 90)),
                    "n_samples": len(values),
                }
        
        baseline = PatientBaseline(
            patient_id=patient_id,
            computed_at=datetime.now(timezone.utc).isoformat(),
            observation_hours=observation_hours,
            baselines=baselines,
        )
        
        self._baselines[patient_id] = baseline
        
        logger.info(
            f"Baseline updated for patient {patient_id}: "
            f"{len(baselines)} parameters from {observation_hours}h observation"
        )
        
        return baseline
    
    def _extract_latest(self, vitals_stream: List[Dict]) -> Dict[str, float]:
        """Extract most recent reading per parameter."""
        latest: Dict[str, float] = {}
        for reading in sorted(vitals_stream, key=lambda x: x.get("timestamp", "")):
            param = reading.get("parameter")
            value = reading.get("value")
            if param and value is not None:
                latest[param] = float(value)
        return latest
    
    def _calculate_news2(self, vitals: Dict[str, float]) -> int:
        """National Early Warning Score 2 (validated clinical score)."""
        score = 0
        
        rr = vitals.get("respiratory_rate")
        if rr is not None:
            if rr <= 8: score += 3
            elif 9 <= rr <= 11: score += 1
            elif 12 <= rr <= 20: score += 0
            elif 21 <= rr <= 24: score += 2
            else: score += 3
        
        spo2 = vitals.get("spo2_pulse_ox")
        if spo2 is not None:
            if spo2 <= 91: score += 3
            elif 92 <= spo2 <= 93: score += 2
            elif 94 <= spo2 <= 95: score += 1
        
        sbp = vitals.get("bp_systolic")
        if sbp is not None:
            if sbp <= 90: score += 3
            elif 91 <= sbp <= 100: score += 2
            elif 101 <= sbp <= 110: score += 1
            elif 111 <= sbp <= 219: score += 0
            else: score += 3
        
        hr = vitals.get("heart_rate")
        if hr is not None:
            if hr <= 40: score += 3
            elif 41 <= hr <= 50: score += 1
            elif 51 <= hr <= 90: score += 0
            elif 91 <= hr <= 110: score += 1
            elif 111 <= hr <= 130: score += 2
            else: score += 3
        
        temp = vitals.get("temperature")
        if temp is not None:
            if temp <= 35.0: score += 3
            elif 35.1 <= temp <= 36.0: score += 1
            elif 36.1 <= temp <= 38.0: score += 0
            elif 38.1 <= temp <= 39.0: score += 1
            else: score += 2
        
        return score
    
    def _calculate_mews(self, vitals: Dict[str, float]) -> int:
        """Modified Early Warning Score."""
        score = 0
        
        sbp = vitals.get("bp_systolic")
        if sbp is not None:
            if sbp <= 70: score += 3
            elif 71 <= sbp <= 80: score += 2
            elif 81 <= sbp <= 100: score += 1
            elif 101 <= sbp <= 199: score += 0
            else: score += 2
        
        hr = vitals.get("heart_rate")
        if hr is not None:
            if hr <= 40: score += 2
            elif 41 <= hr <= 50: score += 1
            elif 51 <= hr <= 100: score += 0
            elif 101 <= hr <= 110: score += 1
            elif 111 <= hr <= 129: score += 2
            else: score += 3
        
        rr = vitals.get("respiratory_rate")
        if rr is not None:
            if rr < 9: score += 2
            elif 9 <= rr <= 14: score += 0
            elif 15 <= rr <= 20: score += 1
            elif 21 <= rr <= 29: score += 2
            else: score += 3
        
        return score
    
    def _estimate_sofa(self, vitals: Dict, context: Dict) -> int:
        """Simplified SOFA estimate from available vitals."""
        score = 0
        
        spo2 = vitals.get("spo2_pulse_ox", 98)
        if spo2 < 90: score += 3
        elif spo2 < 93: score += 2
        elif spo2 < 96: score += 1
        
        sbp = vitals.get("bp_systolic")
        if sbp is not None and sbp < 90:
            score += 2
        
        gfr = context.get("renal_gfr")
        if gfr:
            if gfr < 15: score += 4
            elif gfr < 30: score += 3
            elif gfr < 60: score += 2
            elif gfr < 90: score += 1
        
        return score
    
    def _detect_anomalies(
        self,
        latest: Dict[str, float],
        baseline: Optional[PatientBaseline],
    ) -> List[VitalAnomaly]:
        """Detect anomalies using patient-specific baseline if available."""
        anomalies = []
        
        for param, value in latest.items():
            bounds = VITAL_BOUNDS.get(param)
            if not bounds:
                continue
            
            # Determine reference baseline
            if baseline and param in baseline.baselines:
                bl = baseline.baselines[param]
                ref_mean = bl["mean"]
                ref_std = bl["std"] if bl["std"] > 0 else bounds["std"]
                patient_baseline_val = ref_mean
            else:
                ref_mean = bounds["mean"]
                ref_std = bounds["std"]
                patient_baseline_val = None
            
            # Z-score
            z_score = abs(value - ref_mean) / ref_std if ref_std > 0 else 0
            
            # Expected range (mean ± 2 std)
            expected_range = (ref_mean - 2 * ref_std, ref_mean + 2 * ref_std)
            
            if z_score >= 2.0:  # Outside 2 standard deviations
                if z_score >= 4.0:
                    severity = "critical"
                elif z_score >= 3.0:
                    severity = "severe"
                elif z_score >= 2.5:
                    severity = "moderate"
                else:
                    severity = "mild"
                
                anomaly_type = "spike" if value > ref_mean else "drop"
                
                anomalies.append(VitalAnomaly(
                    parameter=param,
                    current_value=value,
                    expected_range=expected_range,
                    patient_baseline=patient_baseline_val,
                    deviation_sigma=round(z_score, 2),
                    anomaly_type=anomaly_type,
                    severity=severity,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    triggered_alert=severity in ["severe", "critical"],
                ))
        
        return anomalies
    
    def _compute_trend(self, vitals_stream: List[Dict]) -> TrendDirection:
        """Compute overall patient trend from vital sign streams."""
        if len(vitals_stream) < 10:
            return TrendDirection.STABLE
        
        # Compute NEWS2 over last 3 time windows and check direction
        sorted_vitals = sorted(vitals_stream, key=lambda x: x.get("time", x.get("timestamp", "")))
        third = len(sorted_vitals) // 3
        
        # First third vs last third
        early_vitals = {
            v.get("parameter"): v.get("value")
            for v in sorted_vitals[:third]
            if v.get("parameter") and v.get("value") is not None
        }
        late_vitals = {
            v.get("parameter"): v.get("value")
            for v in sorted_vitals[-third:]
            if v.get("parameter") and v.get("value") is not None
        }
        
        early_news2 = self._calculate_news2(early_vitals)
        late_news2 = self._calculate_news2(late_vitals)
        
        diff = late_news2 - early_news2
        
        if diff >= 3:
            return TrendDirection.WORSENING
        elif diff >= 5:
            return TrendDirection.CRITICAL_CHANGE
        elif diff <= -2:
            return TrendDirection.IMPROVING
        else:
            return TrendDirection.STABLE
    
    def _generate_alerts(
        self,
        news2: int,
        det_6h: float,
        sep_12h: float,
        mort_24h: float,
        anomalies: List[VitalAnomaly],
    ) -> Tuple[List[str], str]:
        """Generate clinical alerts based on thresholds."""
        alerts = []
        priority = "NONE"
        
        # NEWS2 alerts
        if news2 >= 7:
            alerts.append(f"NEWS2={news2} — Urgent clinical response required")
            priority = "CRITICAL"
        elif news2 >= self._thresholds["news2_high_alert"]:
            alerts.append(f"NEWS2={news2} — High alert, close monitoring required")
            priority = max(priority, "HIGH") if priority != "CRITICAL" else "CRITICAL"
        
        # AI prediction alerts
        if det_6h >= self._thresholds["deterioration_6h_threshold"]:
            alerts.append(
                f"AI deterioration probability {det_6h:.0%} in 6h — "
                f"Physician review recommended"
            )
            priority = "HIGH" if priority not in ["CRITICAL"] else priority
        
        if sep_12h >= self._thresholds["sepsis_12h_threshold"]:
            alerts.append(
                f"AI sepsis probability {sep_12h:.0%} in 12h — "
                f"Evaluate for sepsis bundle"
            )
            priority = "CRITICAL" if sep_12h > 0.70 else "HIGH"
        
        if mort_24h >= self._thresholds["mortality_24h_threshold"]:
            alerts.append(
                f"AI mortality probability {mort_24h:.0%} in 24h — "
                f"Goals of care discussion recommended"
            )
            if mort_24h > 0.60:
                priority = "CRITICAL"
        
        # Anomaly-based alerts
        critical_anomalies = [a for a in anomalies if a.severity == "critical"]
        if critical_anomalies:
            for anomaly in critical_anomalies:
                alerts.append(
                    f"Critical anomaly: {anomaly.parameter}={anomaly.current_value:.1f} "
                    f"({anomaly.deviation_sigma:.1f}σ from baseline)"
                )
            priority = "CRITICAL"
        
        # Set priority baseline if no alerts
        if not alerts:
            priority = "NONE"
        elif priority == "NONE":
            priority = "LOW"
        
        return alerts, priority
    
    def _empty_prediction(
        self,
        patient_id: str,
        reason: str,
    ) -> VitalsTFTPrediction:
        """Return empty prediction when data is insufficient."""
        return VitalsTFTPrediction(
            patient_id=patient_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            news2_score=0,
            sofa_score=None,
            mews_score=0,
            deterioration_6h=0.0,
            sepsis_12h=0.0,
            mortality_24h=0.0,
            weaning_readiness=None,
            deterioration_uncertainty=1.0,  # Maximum uncertainty = don't trust
            sepsis_uncertainty=1.0,
            trend=TrendDirection.STABLE,
            anomalies=[],
            active_alerts=[f"Prediction unavailable: {reason}"],
            alert_priority="NONE",
        )
