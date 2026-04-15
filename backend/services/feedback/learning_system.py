"""
Feedback Learning System (Layer 6) — YOUR REAL COMPETITIVE MOAT

This is not a nice-to-have. This is why you win long-term.

Every new hospital adds data that improves models.
After 10 hospitals: outperforms generic models.
After 50 hospitals: 3-year moat no competitor can replicate.

CRITICAL DESIGN RULES:
1. DO NOT use raw feedback for direct fine-tuning (creates feedback loops)
2. Weekly: aggregate metrics per diagnosis category
3. Monthly: clinical review board reviews performance
4. Quarterly: human-reviewed feedback for fine-tuning
5. Continuous: RLHF signals update reward model ONLY (not base model)
6. Any model update requires clinical validation before deployment
7. Min 2% accuracy improvement required to deploy new model version

Feedback sources:
- Implicit: physician actions (accept/modify/reject AI recommendations)
- Explicit: 1-tap thumbs up/down (must add <3 seconds to workflow)
- Outcome: 30-day readmission, ICU transfer, mortality (gold standard)
"""

import uuid
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class FeedbackSignal(str, Enum):
    ACCEPTED = "accepted"          # Physician took AI recommendation
    MODIFIED = "modified"          # Physician changed AI recommendation
    REJECTED = "rejected"          # Physician ignored AI recommendation
    THUMBS_UP = "thumbs_up"        # Explicit positive
    THUMBS_DOWN = "thumbs_down"    # Explicit negative


class OutcomeType(str, Enum):
    READMISSION_30D = "readmission_30d"
    ICU_TRANSFER = "icu_transfer"
    MORTALITY_24H = "mortality_24h"
    SEPSIS_ONSET = "sepsis_onset"
    LOS_REDUCTION = "los_reduction"
    ADVERSE_DRUG_EVENT = "adverse_drug_event"
    DIAGNOSIS_CONFIRMED = "diagnosis_confirmed"


class ModelUpdateType(str, Enum):
    WEEKLY_METRICS = "weekly_metrics"
    MONTHLY_REVIEW = "monthly_review"
    QUARTERLY_FINETUNE = "quarterly_finetune"
    RLHF_REWARD_UPDATE = "rlhf_reward_update"


@dataclass
class FeedbackRecord:
    """
    A single feedback event.
    
    Sources:
    - Implicit: physician action on AI output (no extra clicks)
    - Explicit: 1-tap thumbs up/down (physician actively rates)
    - Outcome: linked 30/90-day outcome (gold standard, delayed)
    """
    feedback_id: str
    timestamp: str
    
    # What was the AI doing?
    patient_id: str  # De-identified
    encounter_id: str
    ai_recommendation_id: str
    ai_output_type: str  # "risk_alert" | "differential" | "action" | "drug_alert"
    ai_prediction: Dict[str, Any]
    
    # What did the physician do?
    signal: FeedbackSignal
    actor_id: str  # Anonymized physician ID
    actor_role: str
    modification_details: Optional[Dict] = None  # What changed (if MODIFIED)
    free_text_reason: Optional[str] = None       # Optional voice-to-text
    
    # Quality filter fields
    is_treating_physician: bool = True
    is_in_distribution: bool = True  # False = edge case, filter out
    
    # Outcome linkage (filled in async when outcome occurs)
    outcome_linked: bool = False
    outcome_type: Optional[OutcomeType] = None
    outcome_occurred: Optional[bool] = None
    outcome_within_timeframe: Optional[bool] = None
    outcome_linked_at: Optional[str] = None
    
    @property
    def is_valid_for_training(self) -> bool:
        """Quality filter — only high-quality feedback used for model updates."""
        if not self.is_treating_physician:
            return False  # Must be actual treating provider
        if not self.is_in_distribution:
            return False  # Edge cases excluded
        if self.signal == FeedbackSignal.THUMBS_DOWN and not self.free_text_reason:
            # Anonymous rejection with no context is noise
            return False
        return True
    
    @property
    def ml_signal(self) -> float:
        """
        Convert feedback to ML training signal.
        +1.0 = strong positive, -1.0 = strong negative, 0.5 = mixed.
        """
        signal_map = {
            FeedbackSignal.ACCEPTED: 1.0,
            FeedbackSignal.THUMBS_UP: 1.0,
            FeedbackSignal.MODIFIED: 0.5,    # Partial positive
            FeedbackSignal.REJECTED: -0.5,
            FeedbackSignal.THUMBS_DOWN: -1.0,
        }
        
        base_signal = signal_map.get(self.signal, 0.0)
        
        # Outcome validation amplifies the signal
        if self.outcome_linked and self.outcome_occurred is not None:
            if self.outcome_occurred and base_signal > 0:
                return min(1.0, base_signal * 1.3)  # AI was right
            elif not self.outcome_occurred and base_signal < 0:
                return max(-1.0, base_signal * 1.3)  # AI was correctly wrong
        
        return base_signal


@dataclass
class OutcomeRecord:
    """
    Gold standard outcome — linked to AI predictions for validation.
    
    Requires integration with billing/coding data.
    Compare predicted ICD-10s vs actual discharge ICD-10s.
    """
    outcome_id: str
    patient_id: str  # De-identified
    encounter_id: str
    outcome_type: OutcomeType
    occurred: bool
    outcome_date: str
    
    # Prediction that this validates
    ai_prediction_id: Optional[str] = None
    ai_predicted_probability: Optional[float] = None
    ai_predicted_correctly: Optional[bool] = None
    
    # ICD-10 comparison
    predicted_icd10_codes: List[str] = field(default_factory=list)
    actual_icd10_codes: List[str] = field(default_factory=list)
    icd10_match_score: Optional[float] = None
    
    def calculate_prediction_accuracy(self) -> Optional[float]:
        """Binary: did the prediction match the outcome?"""
        if self.ai_predicted_probability is None:
            return None
        if self.ai_predicted_correctly is not None:
            return 1.0 if self.ai_predicted_correctly else 0.0
        
        # Threshold: prediction > 0.5 = "predicted yes"
        predicted_yes = self.ai_predicted_probability > 0.5
        return 1.0 if predicted_yes == self.occurred else 0.0


@dataclass
class ModelPerformanceSnapshot:
    """Weekly performance metrics per diagnosis/prediction category."""
    snapshot_id: str
    week_start: str
    week_end: str
    hospital_id: str
    
    # Per-category accuracy
    sepsis_prediction_auroc: Optional[float] = None
    deterioration_prediction_auroc: Optional[float] = None
    diagnosis_accuracy_by_category: Dict[str, float] = field(default_factory=dict)
    
    # Feedback signals
    total_feedback_count: int = 0
    acceptance_rate: float = 0.0
    rejection_rate: float = 0.0
    modification_rate: float = 0.0
    
    # Alert quality
    false_positive_rate: float = 0.0
    false_negative_rate: float = 0.0
    
    # Drift indicators
    accuracy_vs_baseline: Optional[float] = None
    drift_detected: bool = False
    drift_reason: Optional[str] = None


class FeedbackCaptureService:
    """
    Captures feedback with minimal physician friction.
    
    UI constraint: feedback must add <3 seconds to workflow.
    If it takes longer, adoption = zero.
    
    Three capture modes:
    1. Implicit: automated capture from physician actions
    2. Explicit: 1-tap thumbs up/down
    3. Outcome: automated linkage to billing/clinical outcomes
    """
    
    def __init__(self, feedback_store=None, kafka_producer=None):
        self._store = feedback_store
        self._kafka = kafka_producer
    
    async def capture_implicit_feedback(
        self,
        patient_id: str,
        encounter_id: str,
        actor_id: str,
        actor_role: str,
        ai_recommendation_id: str,
        ai_output_type: str,
        ai_prediction: Dict[str, Any],
        physician_action: str,  # "accepted" | "modified" | "rejected" | "ordered_different"
        modification_details: Optional[Dict] = None,
    ) -> FeedbackRecord:
        """
        Capture feedback from physician EHR actions.
        
        This is triggered by EHR event hooks:
        - Physician accepts AI-suggested order → "accepted"
        - Physician modifies AI order before accepting → "modified"
        - Physician ignores AI suggestion → "rejected" (after 30-min timeout)
        """
        signal_map = {
            "accepted": FeedbackSignal.ACCEPTED,
            "modified": FeedbackSignal.MODIFIED,
            "rejected": FeedbackSignal.REJECTED,
            "ordered_different": FeedbackSignal.REJECTED,
        }
        
        record = FeedbackRecord(
            feedback_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            encounter_id=encounter_id,
            ai_recommendation_id=ai_recommendation_id,
            ai_output_type=ai_output_type,
            ai_prediction=ai_prediction,
            signal=signal_map.get(physician_action, FeedbackSignal.REJECTED),
            actor_id=actor_id,
            actor_role=actor_role,
            modification_details=modification_details,
            is_treating_physician=True,
        )
        
        await self._store_feedback(record)
        logger.info(
            f"Implicit feedback captured: {physician_action} on {ai_output_type} "
            f"for encounter {encounter_id}"
        )
        
        return record
    
    async def capture_explicit_feedback(
        self,
        patient_id: str,
        encounter_id: str,
        actor_id: str,
        actor_role: str,
        ai_recommendation_id: str,
        ai_output_type: str,
        ai_prediction: Dict[str, Any],
        is_positive: bool,
        free_text_reason: Optional[str] = None,
    ) -> FeedbackRecord:
        """
        Capture 1-tap explicit feedback.
        
        UI: Single button tap (thumbs up / thumbs down).
        MUST be rendered within physician view — not a separate screen.
        Optional voice note for rejection reason.
        """
        record = FeedbackRecord(
            feedback_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            patient_id=patient_id,
            encounter_id=encounter_id,
            ai_recommendation_id=ai_recommendation_id,
            ai_output_type=ai_output_type,
            ai_prediction=ai_prediction,
            signal=FeedbackSignal.THUMBS_UP if is_positive else FeedbackSignal.THUMBS_DOWN,
            actor_id=actor_id,
            actor_role=actor_role,
            free_text_reason=free_text_reason,
        )
        
        await self._store_feedback(record)
        return record
    
    async def link_outcome(
        self,
        encounter_id: str,
        outcome_type: OutcomeType,
        occurred: bool,
        outcome_date: str,
        actual_icd10_codes: List[str],
    ) -> List[FeedbackRecord]:
        """
        Link clinical outcome to prior AI predictions.
        Called by: billing data integration (discharge ICD-10 codes available).
        
        This is the gold standard signal — weeks delayed but high value.
        """
        # Find AI predictions for this encounter (simplified)
        encounter_feedback = await self._get_encounter_feedback(encounter_id)
        
        updated_records = []
        for record in encounter_feedback:
            # Link outcome to relevant predictions
            relevant_types = {
                OutcomeType.READMISSION_30D: ["readmission_risk"],
                OutcomeType.SEPSIS_ONSET: ["sepsis_prediction"],
                OutcomeType.ICU_TRANSFER: ["deterioration_prediction"],
                OutcomeType.MORTALITY_24H: ["mortality_prediction"],
                OutcomeType.DIAGNOSIS_CONFIRMED: ["differential_diagnosis"],
            }
            
            if record.ai_output_type in relevant_types.get(outcome_type, []):
                record.outcome_linked = True
                record.outcome_type = outcome_type
                record.outcome_occurred = occurred
                record.outcome_linked_at = datetime.now(timezone.utc).isoformat()
                updated_records.append(record)
        
        logger.info(
            f"Outcome linked: type={outcome_type} occurred={occurred} "
            f"encounter={encounter_id} records_updated={len(updated_records)}"
        )
        
        return updated_records
    
    async def _store_feedback(self, record: FeedbackRecord):
        """Persist feedback record and emit to Kafka for async processing."""
        if self._store:
            await self._store.save(record)
        
        # Emit to Kafka feedback topic for pipeline processing
        if self._kafka:
            await self._kafka.send(
                "ai.feedback",
                key=record.encounter_id,
                value=json.dumps(asdict(record), default=str),
            )
    
    async def _get_encounter_feedback(self, encounter_id: str) -> List[FeedbackRecord]:
        if self._store:
            return await self._store.get_by_encounter(encounter_id)
        return []


class ModelDriftDetector:
    """
    Monitors for AI model performance degradation.
    
    Drift causes:
    - Seasonal disease patterns (flu season changes baseline)
    - New treatment protocols
    - Changing patient demographics
    - Data pipeline changes
    
    Thresholds:
    - Accuracy drop > 5% → alert + freeze auto-updates
    - False positive rate increase > 10% → alert
    - Feedback rejection rate > 30% → alert
    
    Response: freeze → human review → remediation → revalidation
    """
    
    ACCURACY_DROP_THRESHOLD = 0.05   # 5%
    FP_RATE_INCREASE_THRESHOLD = 0.10  # 10%
    REJECTION_RATE_THRESHOLD = 0.30    # 30%
    
    def __init__(self, baseline_metrics: Optional[Dict] = None):
        """
        baseline_metrics: Established performance at deployment.
        Should be computed from retrospective validation set.
        """
        self._baseline = baseline_metrics or {
            "sepsis_auroc": 0.88,
            "deterioration_auroc": 0.85,
            "acceptance_rate": 0.72,
            "false_positive_rate": 0.15,
        }
        self._auto_updates_frozen = False
    
    def analyze_weekly_snapshot(
        self,
        snapshot: ModelPerformanceSnapshot,
    ) -> Tuple[bool, List[str], str]:
        """
        Analyze weekly performance snapshot for drift.
        
        Returns:
            (drift_detected: bool, alerts: List[str], recommendation: str)
        """
        alerts = []
        
        # Check sepsis prediction drift
        if snapshot.sepsis_prediction_auroc is not None:
            baseline_auroc = self._baseline.get("sepsis_auroc", 0.88)
            drop = baseline_auroc - snapshot.sepsis_prediction_auroc
            if drop > self.ACCURACY_DROP_THRESHOLD:
                alerts.append(
                    f"DRIFT: Sepsis AUROC dropped {drop:.1%} "
                    f"({baseline_auroc:.2f} → {snapshot.sepsis_prediction_auroc:.2f})"
                )
        
        # Check false positive rate
        if snapshot.false_positive_rate > 0:
            baseline_fpr = self._baseline.get("false_positive_rate", 0.15)
            fpr_increase = snapshot.false_positive_rate - baseline_fpr
            if fpr_increase > self.FP_RATE_INCREASE_THRESHOLD:
                alerts.append(
                    f"DRIFT: False positive rate increased {fpr_increase:.1%} "
                    f"({baseline_fpr:.2f} → {snapshot.false_positive_rate:.2f})"
                )
        
        # Check rejection rate
        if snapshot.rejection_rate > self.REJECTION_RATE_THRESHOLD:
            alerts.append(
                f"DRIFT: Physician rejection rate {snapshot.rejection_rate:.1%} "
                f"exceeds threshold {self.REJECTION_RATE_THRESHOLD:.0%}"
            )
        
        drift_detected = len(alerts) > 0
        
        if drift_detected:
            if not self._auto_updates_frozen:
                self._auto_updates_frozen = True
                logger.critical(
                    f"MODEL DRIFT DETECTED — Auto-updates FROZEN. "
                    f"Alerts: {alerts}. Human review required."
                )
            
            recommendation = (
                "FREEZE auto-updates → clinical review board → root cause analysis → "
                "remediate → revalidate on held-out test set → re-enable if ≥2% improvement"
            )
        else:
            recommendation = "Performance within acceptable range. Continue monitoring."
        
        return drift_detected, alerts, recommendation
    
    def compute_weekly_metrics(
        self,
        feedback_records: List[FeedbackRecord],
        outcome_records: List[OutcomeRecord],
        hospital_id: str,
        week_start: str,
        week_end: str,
    ) -> ModelPerformanceSnapshot:
        """Compute weekly performance snapshot from raw feedback and outcomes."""
        
        valid_feedback = [r for r in feedback_records if r.is_valid_for_training]
        
        # Acceptance/rejection rates
        total = len(valid_feedback)
        if total > 0:
            accepted = sum(1 for r in valid_feedback if r.signal == FeedbackSignal.ACCEPTED)
            rejected = sum(1 for r in valid_feedback if r.signal == FeedbackSignal.REJECTED)
            modified = sum(1 for r in valid_feedback if r.signal == FeedbackSignal.MODIFIED)
            
            acceptance_rate = accepted / total
            rejection_rate = rejected / total
            modification_rate = modified / total
        else:
            acceptance_rate = rejection_rate = modification_rate = 0.0
        
        # Compute AUROC from outcomes (simplified)
        sepsis_outcomes = [
            o for o in outcome_records 
            if o.outcome_type == OutcomeType.SEPSIS_ONSET
            and o.ai_predicted_probability is not None
        ]
        
        sepsis_auroc = self._calculate_auroc(sepsis_outcomes) if sepsis_outcomes else None
        
        # False positive rate (high risk alerts that didn't need intervention)
        high_risk_feedback = [
            r for r in valid_feedback
            if r.ai_prediction.get("risk_level") in ["HIGH", "CRITICAL"]
        ]
        unnecessary_alerts = sum(
            1 for r in high_risk_feedback
            if r.signal == FeedbackSignal.REJECTED
        )
        fpr = unnecessary_alerts / len(high_risk_feedback) if high_risk_feedback else 0.0
        
        return ModelPerformanceSnapshot(
            snapshot_id=str(uuid.uuid4()),
            week_start=week_start,
            week_end=week_end,
            hospital_id=hospital_id,
            sepsis_prediction_auroc=sepsis_auroc,
            total_feedback_count=total,
            acceptance_rate=acceptance_rate,
            rejection_rate=rejection_rate,
            modification_rate=modification_rate,
            false_positive_rate=fpr,
        )
    
    def _calculate_auroc(self, outcomes: List[OutcomeRecord]) -> float:
        """Simplified AUROC calculation."""
        if len(outcomes) < 10:
            return None  # Not enough data
        
        positives = [o for o in outcomes if o.occurred]
        negatives = [o for o in outcomes if not o.occurred]
        
        if not positives or not negatives:
            return None
        
        # Count concordant pairs (simplified Mann-Whitney U)
        concordant = 0
        total_pairs = len(positives) * len(negatives)
        
        for pos in positives:
            for neg in negatives:
                if pos.ai_predicted_probability > neg.ai_predicted_probability:
                    concordant += 1
                elif pos.ai_predicted_probability == neg.ai_predicted_probability:
                    concordant += 0.5
        
        return concordant / total_pairs if total_pairs > 0 else 0.5


class ModelGovernanceProcess:
    """
    Formal process for model updates.
    
    WHO APPROVES UPDATES:
    1. Weekly metrics: Data science team (automated)
    2. Monthly review: Clinical informatics director + data science lead
    3. Quarterly fine-tune: Clinical review board (physicians) + data science + legal
    4. Emergency rollback: CMO + CTO (joint decision)
    
    REQUIREMENTS TO DEPLOY:
    - Clinical validation on held-out test set
    - Minimum 2% accuracy improvement
    - No bias increase in any demographic subgroup
    - Shadow mode for 30 days at new hospital
    - Pilot physician sign-off (5+ physicians)
    """
    
    MIN_ACCURACY_IMPROVEMENT = 0.02  # 2%
    MIN_RETROSPECTIVE_MONTHS = 12
    MIN_SHADOW_DAYS = 30
    MIN_PILOT_PHYSICIANS = 5
    
    def validate_update_eligibility(
        self,
        current_metrics: ModelPerformanceSnapshot,
        proposed_metrics: ModelPerformanceSnapshot,
        bias_audit_passed: bool,
        shadow_mode_days: int,
        pilot_physician_count: int,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Validate whether a model update meets deployment requirements.
        
        Returns:
            (eligible: bool, passed_checks: List[str], failed_checks: List[str])
        """
        passed = []
        failed = []
        
        # Check 1: Accuracy improvement
        current_auroc = current_metrics.sepsis_prediction_auroc or 0
        proposed_auroc = proposed_metrics.sepsis_prediction_auroc or 0
        improvement = proposed_auroc - current_auroc
        
        if improvement >= self.MIN_ACCURACY_IMPROVEMENT:
            passed.append(f"Accuracy improvement: +{improvement:.1%} ≥ required {self.MIN_ACCURACY_IMPROVEMENT:.0%}")
        else:
            failed.append(f"Insufficient accuracy improvement: +{improvement:.1%} < required {self.MIN_ACCURACY_IMPROVEMENT:.0%}")
        
        # Check 2: Bias audit
        if bias_audit_passed:
            passed.append("Bias audit: No subgroup performance gaps > 5%")
        else:
            failed.append("CRITICAL: Bias audit failed — demographic performance disparities detected")
        
        # Check 3: Shadow mode
        if shadow_mode_days >= self.MIN_SHADOW_DAYS:
            passed.append(f"Shadow mode: {shadow_mode_days} days ≥ required {self.MIN_SHADOW_DAYS}")
        else:
            failed.append(f"Insufficient shadow mode: {shadow_mode_days} days < required {self.MIN_SHADOW_DAYS}")
        
        # Check 4: Pilot physicians
        if pilot_physician_count >= self.MIN_PILOT_PHYSICIANS:
            passed.append(f"Pilot physicians: {pilot_physician_count} ≥ required {self.MIN_PILOT_PHYSICIANS}")
        else:
            failed.append(f"Insufficient pilot: {pilot_physician_count} < required {self.MIN_PILOT_PHYSICIANS}")
        
        # Check 5: Not frozen due to drift
        if not proposed_metrics.drift_detected:
            passed.append("No active drift detected")
        else:
            failed.append("Model drift detected — resolve before update")
        
        eligible = len(failed) == 0
        
        return eligible, passed, failed
    
    def generate_bias_report(
        self,
        outcomes_by_subgroup: Dict[str, List[OutcomeRecord]],
    ) -> Dict[str, Any]:
        """
        Quarterly bias audit report by demographic subgroup.
        
        Alert threshold: >5% performance gap between any two groups.
        
        Subgroups monitored: race, gender, age group, payer type.
        """
        report = {
            "audit_date": datetime.now(timezone.utc).isoformat(),
            "subgroup_performance": {},
            "gaps_detected": [],
            "audit_passed": True,
        }
        
        accuracies = {}
        for subgroup, outcomes in outcomes_by_subgroup.items():
            if not outcomes:
                continue
            
            accuracies[subgroup] = sum(
                1 for o in outcomes if o.ai_predicted_correctly
            ) / len(outcomes)
            
            report["subgroup_performance"][subgroup] = {
                "accuracy": accuracies[subgroup],
                "n_samples": len(outcomes),
            }
        
        # Check for gaps
        if len(accuracies) >= 2:
            max_acc = max(accuracies.values())
            min_acc = min(accuracies.values())
            gap = max_acc - min_acc
            
            if gap > 0.05:
                best_group = max(accuracies, key=accuracies.get)
                worst_group = min(accuracies, key=accuracies.get)
                report["gaps_detected"].append({
                    "best_subgroup": best_group,
                    "best_accuracy": max_acc,
                    "worst_subgroup": worst_group,
                    "worst_accuracy": min_acc,
                    "gap": gap,
                    "threshold": 0.05,
                    "action_required": "Model review and potential bias correction required",
                })
                report["audit_passed"] = False
        
        return report
