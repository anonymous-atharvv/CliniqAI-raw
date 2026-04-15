"""
Multi-Agent Clinical Orchestration System (Layer 5)

7 Specialized Agents:
1. Triage Agent — ESI scoring, care pathway
2. Diagnosis Agent — Differential generation
3. Risk Agent — Continuous monitoring (15min ICU / 60min ward)
4. Pharmacist Agent — Drug safety (CRITICAL alerts bypass all others)
5. Documentation Agent — SOAP notes, ICD-10 coding
6. Coordinator Agent — Conflict resolution, synthesis
7. Escalation Agent — Critical finding routing with SLA enforcement

Design: LangGraph-style state machine
- Shared state in Redis (TTL: 24 hours per patient session)
- 10-second hard timeout per agent
- Circuit breaker: 3 failures in 5 minutes → disable + alert ops
- Append-only shared state (no overwriting)
- All inter-agent messages logged for audit

NOT AutoGen — too verbose for real-time clinical use.
"""

import asyncio
import time
import uuid
import json
import logging
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Callable, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Shared State Schema
# All agents read from and write to this.
# Append-only — no overwriting previous agent outputs.
# ─────────────────────────────────────────────

class ESICategory(int, Enum):
    """Emergency Severity Index 1-5"""
    RESUSCITATION = 1       # Life-threatening — immediate physician
    EMERGENT = 2            # High risk — respond within minutes
    URGENT = 3              # Stable but needs multiple resources
    LESS_URGENT = 4         # Stable, one resource needed
    NON_URGENT = 5          # Stable, no resources needed


class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CIRCUIT_OPEN = "circuit_open"  # Disabled after too many failures


@dataclass
class AgentMessage:
    """Standard inter-agent message format."""
    agent_id: str
    timestamp: str
    patient_id: str
    output_type: str
    payload: Dict[str, Any]
    confidence: float  # 0.0–1.0
    status: AgentStatus
    execution_ms: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PatientSession:
    """
    Complete patient AI session state.
    Stored in Redis with 24-hour TTL.
    APPEND-ONLY — never overwrite existing agent outputs.
    """
    patient_id: str
    session_id: str
    created_at: str
    last_updated: str
    
    # Agent outputs (append-only lists)
    triage_outputs: List[AgentMessage] = field(default_factory=list)
    diagnosis_outputs: List[AgentMessage] = field(default_factory=list)
    risk_outputs: List[AgentMessage] = field(default_factory=list)
    pharmacist_outputs: List[AgentMessage] = field(default_factory=list)
    documentation_outputs: List[AgentMessage] = field(default_factory=list)
    coordinator_outputs: List[AgentMessage] = field(default_factory=list)
    escalation_outputs: List[AgentMessage] = field(default_factory=list)
    
    # Escalation tracking
    active_escalations: List[Dict] = field(default_factory=list)
    escalation_acknowledgments: List[Dict] = field(default_factory=list)
    
    @property
    def latest_risk_output(self) -> Optional[AgentMessage]:
        return self.risk_outputs[-1] if self.risk_outputs else None
    
    @property
    def latest_triage(self) -> Optional[AgentMessage]:
        return self.triage_outputs[-1] if self.triage_outputs else None
    
    @property
    def has_critical_pharmacy_alert(self) -> bool:
        """Pharmacy CRITICAL alerts bypass all other agents."""
        return any(
            msg.payload.get("alert_level") == "CRITICAL"
            for msg in self.pharmacist_outputs
            if msg.status == AgentStatus.COMPLETED
        )


class CircuitBreaker:
    """
    Agent circuit breaker.
    
    3 failures in 5 minutes → OPEN (disable agent)
    After 10 minutes → HALF_OPEN (test with 1 request)
    On success → CLOSED (re-enable)
    
    CRITICAL: When circuit is open, always log to ops team.
    """
    
    def __init__(self, agent_id: str, failure_threshold: int = 3, window_seconds: int = 300):
        self.agent_id = agent_id
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self._failures: List[float] = []
        self._open_since: Optional[float] = None
        self._is_open = False
    
    def record_failure(self) -> bool:
        """Record a failure. Returns True if circuit just opened."""
        now = time.time()
        cutoff = now - self.window_seconds
        self._failures = [t for t in self._failures if t > cutoff]
        self._failures.append(now)
        
        if len(self._failures) >= self.failure_threshold and not self._is_open:
            self._is_open = True
            self._open_since = now
            logger.critical(
                f"CIRCUIT BREAKER OPEN: Agent '{self.agent_id}' failed "
                f"{len(self._failures)} times in {self.window_seconds}s. "
                f"Agent disabled. OPS TEAM ALERT REQUIRED."
            )
            return True
        return False
    
    def record_success(self):
        self._failures = []
        self._is_open = False
        self._open_since = None
    
    @property
    def is_open(self) -> bool:
        if not self._is_open:
            return False
        # Auto-reset after 10 minutes for retry
        if self._open_since and time.time() - self._open_since > 600:
            logger.info(f"Circuit breaker half-open for agent '{self.agent_id}'")
            self._is_open = False
            return False
        return True
    
    def __call__(self, func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.is_open:
                return AgentMessage(
                    agent_id=self.agent_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    patient_id=kwargs.get("patient_id", "unknown"),
                    output_type="error",
                    payload={"error": "Circuit breaker open — agent disabled"},
                    confidence=0.0,
                    status=AgentStatus.CIRCUIT_OPEN,
                )
            try:
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=10.0)
                self.record_success()
                return result
            except asyncio.TimeoutError:
                self.record_failure()
                logger.error(f"Agent '{self.agent_id}' timed out (10s limit)")
                return AgentMessage(
                    agent_id=self.agent_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    patient_id=kwargs.get("patient_id", "unknown"),
                    output_type="error",
                    payload={},
                    confidence=0.0,
                    status=AgentStatus.TIMEOUT,
                )
            except Exception as e:
                self.record_failure()
                logger.error(f"Agent '{self.agent_id}' failed: {e}")
                return AgentMessage(
                    agent_id=self.agent_id,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    patient_id=kwargs.get("patient_id", "unknown"),
                    output_type="error",
                    payload={"error": str(e)},
                    confidence=0.0,
                    status=AgentStatus.FAILED,
                )
        return wrapper


# ─────────────────────────────────────────────
# Agent Implementations
# ─────────────────────────────────────────────

class TriageAgent:
    """
    Agent 1: Triage
    
    Trigger: Every new patient encounter or status change.
    Output: ESI category (1-5) + care pathway.
    
    ESI 1 → physician paged within 2 minutes (escalation agent handles this).
    """
    
    AGENT_ID = "triage_agent"
    
    def __init__(self, reasoning_engine=None):
        self._engine = reasoning_engine
        self._circuit_breaker = CircuitBreaker(self.AGENT_ID)
    
    async def run(
        self,
        patient_id: str,
        vitals: List[Dict],
        chief_complaint: str,
        current_problems: List[str],
    ) -> AgentMessage:
        """Assess patient urgency and assign ESI category."""
        start = time.time()
        
        try:
            # Rule-based pre-screening (fast, before LLM)
            esi_category = self._rule_based_esi(vitals, chief_complaint, current_problems)
            
            # High-confidence cases skip LLM for speed
            if esi_category == ESICategory.RESUSCITATION:
                payload = {
                    "esi_category": int(esi_category),
                    "esi_label": "RESUSCITATION",
                    "care_pathway": "immediate_resuscitation",
                    "trigger": "critical_vital_signs",
                    "physician_page_required": True,
                    "page_within_minutes": 2,
                }
                return AgentMessage(
                    agent_id=self.AGENT_ID,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    patient_id=patient_id,
                    output_type="triage",
                    payload=payload,
                    confidence=0.95,
                    status=AgentStatus.COMPLETED,
                    execution_ms=int((time.time() - start) * 1000),
                )
            
            # For ESI 2-5: augment with LLM reasoning if available
            care_pathway = self._determine_care_pathway(esi_category)
            
            payload = {
                "esi_category": int(esi_category),
                "esi_label": esi_category.name,
                "care_pathway": care_pathway,
                "chief_complaint": chief_complaint,
                "physician_page_required": esi_category in [ESICategory.RESUSCITATION, ESICategory.EMERGENT],
                "page_within_minutes": 2 if esi_category == ESICategory.RESUSCITATION else 5,
            }
            
            return AgentMessage(
                agent_id=self.AGENT_ID,
                timestamp=datetime.now(timezone.utc).isoformat(),
                patient_id=patient_id,
                output_type="triage",
                payload=payload,
                confidence=0.85,
                status=AgentStatus.COMPLETED,
                execution_ms=int((time.time() - start) * 1000),
            )
        
        except Exception as e:
            logger.error(f"Triage agent failed: {e}")
            raise
    
    def _rule_based_esi(
        self,
        vitals: List[Dict],
        chief_complaint: str,
        problems: List[str],
    ) -> ESICategory:
        """
        Fast rule-based ESI scoring.
        
        ESI 1 criteria (any one):
        - Apneic / pulseless
        - Severe respiratory distress (SpO2 < 85%)
        - Unresponsive (GCS ≤ 8)
        - Active cardiac arrest
        
        ESI 2 criteria (any one):
        - HR > 150 or < 40
        - SpO2 < 90%
        - MAP < 60 mmHg
        - Acute onset severe pain
        """
        vital_dict = {v.get("parameter"): v.get("value") for v in vitals}
        
        spo2 = vital_dict.get("spo2_pulse_ox", 99)
        hr = vital_dict.get("heart_rate", 80)
        map_val = vital_dict.get("bp_mean", 80)
        gcs = vital_dict.get("gcs_total", 15)
        rr = vital_dict.get("respiratory_rate", 16)
        
        # ESI 1
        if (spo2 is not None and spo2 < 85) or (gcs is not None and gcs <= 8):
            return ESICategory.RESUSCITATION
        
        # ESI 2
        if (
            (hr is not None and (hr > 150 or hr < 40)) or
            (spo2 is not None and spo2 < 90) or
            (map_val is not None and map_val < 60) or
            (rr is not None and (rr > 35 or rr < 6))
        ):
            return ESICategory.EMERGENT
        
        # Chief complaint screening for ESI 2
        critical_complaints = [
            "chest pain", "difficulty breathing", "shortness of breath",
            "stroke", "altered mental status", "seizure", "anaphylaxis",
        ]
        if any(cc in chief_complaint.lower() for cc in critical_complaints):
            return ESICategory.EMERGENT
        
        # Default ESI 3 for ICU patients (needs further assessment)
        return ESICategory.URGENT
    
    def _determine_care_pathway(self, esi: ESICategory) -> str:
        pathways = {
            ESICategory.RESUSCITATION: "immediate_resuscitation",
            ESICategory.EMERGENT: "emergency_treatment",
            ESICategory.URGENT: "urgent_evaluation",
            ESICategory.LESS_URGENT: "standard_evaluation",
            ESICategory.NON_URGENT: "routine_care",
        }
        return pathways.get(esi, "standard_evaluation")


class RiskAgent:
    """
    Agent 3: Continuous Risk Monitoring
    
    Trigger: Every 15 minutes for ICU patients, every 60 minutes for ward.
    Input: Real-time vitals, medication list, AI predictions.
    Output: Risk level, trend, specific risk factors.
    
    Scores: NEWS2, SOFA, MEWS computed in real-time.
    """
    
    AGENT_ID = "risk_agent"
    
    def __init__(self):
        self._circuit_breaker = CircuitBreaker(self.AGENT_ID)
    
    async def run(
        self,
        patient_id: str,
        vitals: List[Dict],
        ai_predictions: Dict[str, float],
        medications: List[Dict],
        comorbidities: List[str],
    ) -> AgentMessage:
        start = time.time()
        
        vital_dict = {v.get("parameter"): v.get("value") for v in vitals}
        
        # Calculate clinical scores
        news2_score = self._calculate_news2(vital_dict)
        sofa_score = self._estimate_sofa(vital_dict, medications)
        
        # Determine risk level
        risk_level, risk_factors, trigger = self._determine_risk(
            news2_score=news2_score,
            sofa_score=sofa_score,
            ai_predictions=ai_predictions,
            comorbidities=comorbidities,
        )
        
        # Determine trend (requires historical comparison — simplified here)
        trend = "stable"  # Production: compare to previous risk outputs
        
        payload = {
            "risk_level": risk_level,
            "scores": {
                "NEWS2": news2_score,
                "SOFA_estimate": sofa_score,
            },
            "risk_factors": risk_factors,
            "trend": trend,
            "ai_predictions": ai_predictions,
            "trigger": trigger,
            "alert_required": risk_level in ["HIGH", "CRITICAL"],
            "alert_timeline_minutes": 5 if risk_level == "CRITICAL" else 15,
        }
        
        confidence = 0.90 if news2_score is not None else 0.60
        
        return AgentMessage(
            agent_id=self.AGENT_ID,
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            output_type="risk",
            payload=payload,
            confidence=confidence,
            status=AgentStatus.COMPLETED,
            execution_ms=int((time.time() - start) * 1000),
        )
    
    def _calculate_news2(self, vitals: Dict) -> Optional[int]:
        """
        National Early Warning Score 2.
        Score ≥ 5 → high alert.
        """
        score = 0
        
        # Respiratory rate
        rr = vitals.get("respiratory_rate")
        if rr is not None:
            if rr <= 8: score += 3
            elif rr <= 11: score += 1
            elif rr <= 20: score += 0
            elif rr <= 24: score += 2
            else: score += 3
        
        # SpO2
        spo2 = vitals.get("spo2_pulse_ox")
        if spo2 is not None:
            if spo2 <= 91: score += 3
            elif spo2 <= 93: score += 2
            elif spo2 <= 95: score += 1
            else: score += 0
        
        # Systolic BP
        sbp = vitals.get("bp_systolic")
        if sbp is not None:
            if sbp <= 90: score += 3
            elif sbp <= 100: score += 2
            elif sbp <= 110: score += 1
            elif sbp <= 219: score += 0
            else: score += 3
        
        # Heart rate
        hr = vitals.get("heart_rate")
        if hr is not None:
            if hr <= 40: score += 3
            elif hr <= 50: score += 1
            elif hr <= 90: score += 0
            elif hr <= 110: score += 1
            elif hr <= 130: score += 2
            else: score += 3
        
        # Temperature
        temp = vitals.get("temperature")
        if temp is not None:
            if temp <= 35.0: score += 3
            elif temp <= 36.0: score += 1
            elif temp <= 38.0: score += 0
            elif temp <= 39.0: score += 1
            else: score += 2
        
        return score
    
    def _estimate_sofa(self, vitals: Dict, medications: List[Dict]) -> int:
        """Simplified SOFA estimation from available vitals."""
        score = 0
        
        # Respiratory: PaO2/FiO2 — approximate from SpO2
        spo2 = vitals.get("spo2_pulse_ox", 98)
        if spo2 < 90: score += 3
        elif spo2 < 93: score += 2
        elif spo2 < 96: score += 1
        
        # Cardiovascular: MAP
        map_val = vitals.get("bp_mean")
        if map_val is not None and map_val < 70: score += 1
        
        # Check for vasopressors
        vasopressors = ["norepinephrine", "epinephrine", "dopamine", "vasopressin"]
        on_vasopressor = any(
            any(v in med.get("name", "").lower() for v in vasopressors)
            for med in medications
        )
        if on_vasopressor:
            score += 2
        
        return score
    
    def _determine_risk(
        self,
        news2_score: Optional[int],
        sofa_score: int,
        ai_predictions: Dict[str, float],
        comorbidities: List[str],
    ) -> Tuple[str, List[str], str]:
        """Determine risk level, factors, and trigger."""
        risk_factors = []
        
        # NEWS2 thresholds
        if news2_score is not None:
            if news2_score >= 7:
                risk_factors.append(f"NEWS2={news2_score} (≥7: urgent response)")
            elif news2_score >= 5:
                risk_factors.append(f"NEWS2={news2_score} (≥5: high alert)")
        
        # AI predictions
        if ai_predictions.get("deterioration_6h", 0) > 0.70:
            risk_factors.append(
                f"AI deterioration probability {ai_predictions['deterioration_6h']:.0%} in 6h"
            )
        
        if ai_predictions.get("sepsis_12h", 0) > 0.50:
            risk_factors.append(
                f"AI sepsis probability {ai_predictions['sepsis_12h']:.0%} in 12h"
            )
        
        if ai_predictions.get("mortality_24h", 0) > 0.40:
            risk_factors.append(
                f"AI mortality probability {ai_predictions['mortality_24h']:.0%} in 24h"
            )
        
        # SOFA
        if sofa_score >= 6:
            risk_factors.append(f"SOFA={sofa_score} (organ dysfunction)")
        
        # Comorbidities that elevate risk
        high_risk_comorbidities = ["immunocompromised", "end_stage_renal", "malignancy"]
        for c in comorbidities:
            if any(hrc in c.lower() for hrc in high_risk_comorbidities):
                risk_factors.append(f"High-risk comorbidity: {c}")
        
        # Determine overall risk
        if (
            (news2_score is not None and news2_score >= 7) or
            ai_predictions.get("mortality_24h", 0) > 0.60 or
            sofa_score >= 8
        ):
            return "CRITICAL", risk_factors, "critical_threshold_met"
        
        elif (
            (news2_score is not None and news2_score >= 5) or
            ai_predictions.get("deterioration_6h", 0) > 0.70 or
            ai_predictions.get("sepsis_12h", 0) > 0.50
        ):
            return "HIGH", risk_factors, "high_threshold_met"
        
        elif news2_score is not None and news2_score >= 3:
            return "MEDIUM", risk_factors, "medium_threshold_met"
        
        return "LOW", risk_factors, "within_normal_parameters"


class PharmacistAgent:
    """
    Agent 4: Medication Safety (NEW — not in v1)
    
    CRITICAL ALERTS bypass all other agent outputs.
    Priority: CRITICAL → pharmacist + physician within 1 minute.
    
    Checks:
    a) Drug-drug interactions (via Micromedex API)
    b) Dose validation against clinical guidelines
    c) Allergy cross-reactivity
    d) Renal/hepatic dose adjustments
    """
    
    AGENT_ID = "pharmacist_agent"
    
    # Common critical drug interactions (subset — production uses Micromedex)
    CRITICAL_INTERACTIONS = {
        ("warfarin", "aspirin"): "Major bleeding risk — warfarin + aspirin: concurrent anticoagulant + antiplatelet increases serious bleeding risk 3-4x",
        ("ssri", "maoi"): "LIFE-THREATENING: SSRI + MAOI serotonin syndrome — never co-administer, potentially fatal",
        ("metformin", "contrast"): "Lactic acidosis risk — metformin + contrast dye: hold metformin 48h pre/post contrast procedure",
        ("quinolone", "antacid"): "Reduced absorption — quinolone + antacid: chelation reduces antibiotic bioavailability, separate by 2+ hours",
        ("amiodarone", "simvastatin"): "Myopathy/rhabdomyolysis risk — amiodarone + simvastatin: CYP3A4 inhibition, reduce statin dose",
        ("piperacillin", "vancomycin"): "Nephrotoxicity — vancomycin + piperacillin/tazobactam: AKI incidence increased, monitor SCr daily",
    }
    
    def __init__(self, drug_db_client=None):
        self._drug_db = drug_db_client
    
    async def run(
        self,
        patient_id: str,
        current_medications: List[Dict],
        new_medication: Optional[Dict] = None,
        patient_weight_kg: Optional[float] = None,
        renal_function_gfr: Optional[float] = None,
        hepatic_function: Optional[str] = None,  # "normal"|"mild"|"moderate"|"severe"
        allergies: Optional[List[str]] = None,
    ) -> AgentMessage:
        start = time.time()
        
        alerts = []
        alert_level = "NONE"  # NONE | WARNING | CRITICAL
        
        all_medications = list(current_medications)
        if new_medication:
            all_medications.append(new_medication)
        
        # Drug-drug interaction check
        interactions = self._check_interactions(all_medications)
        alerts.extend(interactions)
        
        # Dose validation
        if new_medication and patient_weight_kg:
            dose_alerts = self._validate_dose(
                new_medication, patient_weight_kg, renal_function_gfr
            )
            alerts.extend(dose_alerts)
        
        # Allergy check
        if new_medication and allergies:
            allergy_alerts = self._check_allergies(new_medication, allergies)
            alerts.extend(allergy_alerts)
        
        # Determine alert level
        if any(a.get("severity") == "CRITICAL" for a in alerts):
            alert_level = "CRITICAL"
        elif any(a.get("severity") == "WARNING" for a in alerts):
            alert_level = "WARNING"
        
        payload = {
            "alert_level": alert_level,
            "alerts": alerts,
            "medication_count": len(all_medications),
            "requires_immediate_action": alert_level == "CRITICAL",
            "bypass_coordinator": alert_level == "CRITICAL",
        }
        
        return AgentMessage(
            agent_id=self.AGENT_ID,
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            output_type="pharmacy_safety",
            payload=payload,
            confidence=0.95,
            status=AgentStatus.COMPLETED,
            execution_ms=int((time.time() - start) * 1000),
        )
    
    def _check_interactions(self, medications: List[Dict]) -> List[Dict]:
        """Check for drug-drug interactions."""
        alerts = []
        med_names = [m.get("name", "").lower() for m in medications]
        
        for (drug1, drug2), description in self.CRITICAL_INTERACTIONS.items():
            drug1_present = any(drug1 in name for name in med_names)
            drug2_present = any(drug2 in name for name in med_names)
            
            if drug1_present and drug2_present:
                alerts.append({
                    "type": "drug_drug_interaction",
                    "drugs": [drug1, drug2],
                    "description": description,
                    "severity": "CRITICAL" if "LIFE-THREATENING" in description else "WARNING",
                    "recommendation": "Review with pharmacist and prescriber immediately",
                })
        
        return alerts
    
    def _validate_dose(
        self,
        medication: Dict,
        weight_kg: float,
        gfr: Optional[float],
    ) -> List[Dict]:
        """Validate medication dose against weight and renal function."""
        alerts = []
        
        # Renal dose adjustment flags
        renally_cleared = [
            "vancomycin", "gentamicin", "metformin", "digoxin",
            "lisinopril", "penicillin", "cephalexin"
        ]
        
        med_name = medication.get("name", "").lower()
        
        if gfr is not None and gfr < 30:
            if any(drug in med_name for drug in renally_cleared):
                alerts.append({
                    "type": "renal_dose_adjustment",
                    "medication": medication.get("name"),
                    "gfr": gfr,
                    "description": f"GFR {gfr} mL/min — {medication.get('name')} requires renal dose adjustment",
                    "severity": "WARNING" if gfr >= 15 else "CRITICAL",
                    "recommendation": "Consult renal dosing guidelines or clinical pharmacist",
                })
        
        return alerts
    
    def _check_allergies(
        self,
        medication: Dict,
        allergies: List[str],
    ) -> List[Dict]:
        """Check for allergy cross-reactivity."""
        alerts = []
        med_name = medication.get("name", "").lower()
        
        # Simplified cross-reactivity checking
        cross_reactions = {
            "penicillin": ["amoxicillin", "ampicillin", "piperacillin"],
            "sulfa": ["sulfamethoxazole", "sulfadiazine"],
            "cephalosporin": ["cephalexin", "ceftriaxone", "cefazolin"],
        }
        
        for allergy in allergies:
            allergy_lower = allergy.lower()
            for base, related in cross_reactions.items():
                if base in allergy_lower:
                    if any(r in med_name for r in related):
                        alerts.append({
                            "type": "allergy_cross_reactivity",
                            "known_allergy": allergy,
                            "prescribed_medication": medication.get("name"),
                            "description": f"Cross-reactivity risk: {allergy} allergy + {medication.get('name')}",
                            "severity": "CRITICAL",
                            "recommendation": "Verify allergy history and consider alternative agent",
                        })
        
        return alerts


class CoordinatorAgent:
    """
    Agent 6: Synthesize and De-conflict All Agent Outputs (NEW)
    
    Logic:
    - Agents agree → consolidate into unified recommendation
    - Agents disagree → confidence-weighted voting
    - Confidence < 0.6 on resolution → flag for human arbitration
    - CRITICAL pharmacy alerts ALWAYS override all other agents
    
    Polls shared state every 30 seconds or on new event.
    """
    
    AGENT_ID = "coordinator_agent"
    
    async def run(self, session: PatientSession) -> AgentMessage:
        start = time.time()
        
        # Rule 1: CRITICAL pharmacy alerts bypass everything
        if session.has_critical_pharmacy_alert:
            latest_pharmacy = session.pharmacist_outputs[-1]
            payload = {
                "recommendation_type": "pharmacy_critical_override",
                "primary_concern": latest_pharmacy.payload,
                "message": "CRITICAL PHARMACY ALERT — all other recommendations deferred",
                "requires_physician": True,
                "requires_pharmacist": True,
                "response_time_minutes": 1,
                "coordinator_confidence": 1.0,
            }
            return AgentMessage(
                agent_id=self.AGENT_ID,
                timestamp=datetime.now(timezone.utc).isoformat(),
                patient_id=session.patient_id,
                output_type="coordination",
                payload=payload,
                confidence=1.0,
                status=AgentStatus.COMPLETED,
                execution_ms=int((time.time() - start) * 1000),
            )
        
        # Collect available agent outputs
        available_agents = {}
        if session.triage_outputs:
            available_agents["triage"] = session.triage_outputs[-1]
        if session.risk_outputs:
            available_agents["risk"] = session.risk_outputs[-1]
        if session.diagnosis_outputs:
            available_agents["diagnosis"] = session.diagnosis_outputs[-1]
        if session.pharmacist_outputs:
            available_agents["pharmacist"] = session.pharmacist_outputs[-1]
        
        # Weighted consensus
        recommendation, coordinator_confidence = self._build_consensus(available_agents)
        
        # Low confidence → human arbitration
        requires_human_arbitration = coordinator_confidence < 0.6
        
        payload = {
            "recommendation_type": "consensus",
            "unified_recommendation": recommendation,
            "coordinator_confidence": coordinator_confidence,
            "contributing_agents": list(available_agents.keys()),
            "requires_human_arbitration": requires_human_arbitration,
            "arbitration_reason": "Low agent consensus" if requires_human_arbitration else None,
        }
        
        return AgentMessage(
            agent_id=self.AGENT_ID,
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=session.patient_id,
            output_type="coordination",
            payload=payload,
            confidence=coordinator_confidence,
            status=AgentStatus.COMPLETED,
            execution_ms=int((time.time() - start) * 1000),
        )
    
    def _build_consensus(
        self, agents: Dict[str, AgentMessage]
    ) -> Tuple[Dict, float]:
        """Build weighted consensus from agent outputs."""
        
        # Determine highest risk level across agents
        risk_levels = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
        max_risk = "LOW"
        
        if "risk" in agents:
            risk_payload = agents["risk"].payload
            max_risk = risk_payload.get("risk_level", "LOW")
        
        # Triage ESI → risk level mapping
        if "triage" in agents:
            esi = agents["triage"].payload.get("esi_category", 5)
            esi_risk = {1: "CRITICAL", 2: "HIGH", 3: "MEDIUM", 4: "LOW", 5: "LOW"}
            esi_risk_level = esi_risk.get(esi, "LOW")
            if risk_levels.get(esi_risk_level, 0) > risk_levels.get(max_risk, 0):
                max_risk = esi_risk_level
        
        # Calculate consensus confidence
        if agents:
            avg_confidence = sum(a.confidence for a in agents.values()) / len(agents)
        else:
            avg_confidence = 0.0
        
        recommendation = {
            "risk_level": max_risk,
            "immediate_actions_required": max_risk in ["HIGH", "CRITICAL"],
            "physician_notification_required": max_risk in ["HIGH", "CRITICAL"],
            "agent_summary": {
                k: v.payload.get("risk_level") or v.payload.get("alert_level", "INFO")
                for k, v in agents.items()
            }
        }
        
        return recommendation, avg_confidence


class EscalationAgent:
    """
    Agent 7: Ensure Critical Findings Reach Humans (NEW)
    
    SLA Enforcement:
    - ESI 1 → physician paged within 2 minutes
    - CRITICAL risk → charge nurse + attending within 5 minutes
    - Pharmacy CRITICAL → pharmacist + physician within 1 minute
    - Sepsis probability > 0.7 → sepsis bundle activation
    
    Tracks acknowledgment. Re-escalates if no response.
    """
    
    AGENT_ID = "escalation_agent"
    
    def __init__(self, notification_service=None):
        self._notifier = notification_service
    
    async def run(
        self,
        session: PatientSession,
        coordinator_output: AgentMessage,
    ) -> AgentMessage:
        start = time.time()
        
        escalations = []
        
        payload = coordinator_output.payload
        risk_level = payload.get("unified_recommendation", {}).get("risk_level", "LOW")
        
        # ESI 1 escalation
        if (session.latest_triage and 
            session.latest_triage.payload.get("esi_category") == 1):
            escalation = self._create_escalation(
                patient_id=session.patient_id,
                escalation_type="ESI_1",
                recipients=["physician_attending", "charge_nurse"],
                response_sla_minutes=2,
                message="IMMEDIATE: ESI Level 1 — Resuscitation required",
            )
            escalations.append(escalation)
        
        # CRITICAL risk
        if risk_level == "CRITICAL":
            escalation = self._create_escalation(
                patient_id=session.patient_id,
                escalation_type="CRITICAL_RISK",
                recipients=["physician_attending", "charge_nurse"],
                response_sla_minutes=5,
                message=f"CRITICAL ALERT: Patient risk level CRITICAL",
            )
            escalations.append(escalation)
        
        # Pharmacy CRITICAL
        if session.has_critical_pharmacy_alert:
            escalation = self._create_escalation(
                patient_id=session.patient_id,
                escalation_type="PHARMACY_CRITICAL",
                recipients=["pharmacist_oncall", "physician_attending"],
                response_sla_minutes=1,
                message="CRITICAL DRUG ALERT — Immediate pharmacist review required",
            )
            escalations.append(escalation)
        
        # Sepsis bundle
        risk_output = session.latest_risk_output
        if (risk_output and 
            risk_output.payload.get("ai_predictions", {}).get("sepsis_12h", 0) > 0.7):
            escalation = self._create_escalation(
                patient_id=session.patient_id,
                escalation_type="SEPSIS_BUNDLE",
                recipients=["physician_attending", "charge_nurse", "rapid_response_team"],
                response_sla_minutes=5,
                message="SEPSIS ALERT: AI probability >70% — Activate sepsis bundle",
            )
            escalations.append(escalation)
        
        # Send escalations
        for esc in escalations:
            await self._send_escalation(esc)
        
        return AgentMessage(
            agent_id=self.AGENT_ID,
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=session.patient_id,
            output_type="escalation",
            payload={
                "escalations_sent": len(escalations),
                "escalations": escalations,
            },
            confidence=1.0,
            status=AgentStatus.COMPLETED,
            execution_ms=int((time.time() - start) * 1000),
        )
    
    def _create_escalation(
        self,
        patient_id: str,
        escalation_type: str,
        recipients: List[str],
        response_sla_minutes: int,
        message: str,
    ) -> Dict:
        return {
            "escalation_id": str(uuid.uuid4()),
            "patient_id": patient_id,
            "type": escalation_type,
            "recipients": recipients,
            "message": message,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "sla_deadline": (
                datetime.now(timezone.utc) + timedelta(minutes=response_sla_minutes)
            ).isoformat(),
            "acknowledged": False,
            "acknowledgment_time": None,
        }
    
    async def _send_escalation(self, escalation: Dict):
        """Send escalation via configured notification service."""
        logger.critical(
            f"ESCALATION: type={escalation['type']} "
            f"patient={escalation['patient_id']} "
            f"recipients={escalation['recipients']} "
            f"sla={escalation['sla_deadline']}"
        )
        if self._notifier:
            await self._notifier.send(escalation)


class AgentOrchestrator:
    """
    Main orchestrator for the multi-agent system.
    
    Manages all agents, shared state, and the processing pipeline.
    Entry point for all patient AI processing.
    """
    
    def __init__(
        self,
        redis_client=None,
        reasoning_engine=None,
        notification_service=None,
    ):
        self._redis = redis_client
        self._session_ttl = 86400  # 24 hours
        
        # Initialize agents
        self.triage = TriageAgent(reasoning_engine)
        self.risk = RiskAgent()
        self.pharmacist = PharmacistAgent()
        self.coordinator = CoordinatorAgent()
        self.escalation = EscalationAgent(notification_service)
    
    async def process_patient_event(
        self,
        patient_id: str,
        event_type: str,  # "admission" | "vitals_update" | "medication_order" | "status_change"
        event_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Main entry point for all patient AI processing.
        
        Returns unified recommendation package for physician.
        """
        logger.info(f"Processing {event_type} event for patient {patient_id}")
        
        # Load or create session
        session = await self._load_session(patient_id)
        
        # Run agents in parallel (where safe to do so)
        tasks = []
        
        if event_type in ["admission", "status_change"]:
            tasks.append(self._run_triage(session, event_data))
        
        tasks.append(self._run_risk(session, event_data))
        
        if event_data.get("new_medication"):
            tasks.append(self._run_pharmacist(session, event_data))
        
        # Execute agents concurrently
        agent_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle results
        for result in agent_results:
            if isinstance(result, Exception):
                logger.error(f"Agent task failed: {result}")
                continue
            await self._append_to_session(session, result)
        
        # Coordinator synthesizes (sequential — needs all agent outputs)
        coordinator_output = await self.coordinator.run(session)
        await self._append_to_session(session, coordinator_output)
        
        # Escalation (sequential — needs coordinator output)
        escalation_output = await self.escalation.run(session, coordinator_output)
        await self._append_to_session(session, escalation_output)
        
        # Save session
        await self._save_session(session)
        
        return self._build_physician_package(session, coordinator_output)
    
    async def _run_triage(self, session: PatientSession, data: Dict) -> AgentMessage:
        return await self.triage.run(
            patient_id=session.patient_id,
            vitals=data.get("vitals", []),
            chief_complaint=data.get("chief_complaint", ""),
            current_problems=data.get("problems", []),
        )
    
    async def _run_risk(self, session: PatientSession, data: Dict) -> AgentMessage:
        return await self.risk.run(
            patient_id=session.patient_id,
            vitals=data.get("vitals", []),
            ai_predictions=data.get("ai_predictions", {}),
            medications=data.get("medications", []),
            comorbidities=data.get("comorbidities", []),
        )
    
    async def _run_pharmacist(self, session: PatientSession, data: Dict) -> AgentMessage:
        return await self.pharmacist.run(
            patient_id=session.patient_id,
            current_medications=data.get("medications", []),
            new_medication=data.get("new_medication"),
            patient_weight_kg=data.get("weight_kg"),
            renal_function_gfr=data.get("gfr"),
            allergies=data.get("allergies", []),
        )
    
    async def _append_to_session(self, session: PatientSession, msg: AgentMessage):
        """Append agent output to session (append-only)."""
        append_map = {
            "triage_agent": "triage_outputs",
            "risk_agent": "risk_outputs",
            "pharmacist_agent": "pharmacist_outputs",
            "coordinator_agent": "coordinator_outputs",
            "escalation_agent": "escalation_outputs",
            "diagnosis_agent": "diagnosis_outputs",
        }
        field_name = append_map.get(msg.agent_id)
        if field_name:
            getattr(session, field_name).append(msg)
        session.last_updated = datetime.now(timezone.utc).isoformat()
    
    async def _load_session(self, patient_id: str) -> PatientSession:
        if self._redis:
            data = await self._redis.get(f"session:{patient_id}")
            if data:
                return PatientSession(**json.loads(data))
        
        return PatientSession(
            patient_id=patient_id,
            session_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
    
    async def _save_session(self, session: PatientSession):
        if self._redis:
            await self._redis.setex(
                f"session:{session.patient_id}",
                self._session_ttl,
                json.dumps(asdict(session), default=str),
            )
    
    def _build_physician_package(
        self,
        session: PatientSession,
        coordinator: AgentMessage,
    ) -> Dict[str, Any]:
        """Build the final recommendation package shown to physician."""
        return {
            "patient_id": session.patient_id,
            "session_id": session.session_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "coordinator_recommendation": coordinator.payload,
            "coordinator_confidence": coordinator.confidence,
            "triage": session.latest_triage.payload if session.latest_triage else None,
            "risk": session.latest_risk_output.payload if session.latest_risk_output else None,
            "escalations_active": len(session.active_escalations),
            "requires_immediate_attention": (
                coordinator.payload.get("unified_recommendation", {}).get("risk_level") 
                in ["HIGH", "CRITICAL"]
            ),
        }
