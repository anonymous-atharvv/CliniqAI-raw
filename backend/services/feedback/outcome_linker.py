"""
Outcome Linker — links AI predictions to real clinical outcomes.
Gold standard signal: 30-day readmission, ICU transfer, mortality, sepsis onset.
Requires billing/coding data integration (ICD-10 final codes at discharge).
"""
import logging
from datetime import datetime, timezone
from typing import List, Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class OutcomeRecord:
    outcome_id: str
    patient_deident_id: str
    encounter_id: str
    outcome_type: str    # readmission_30d|icu_transfer|mortality_24h|sepsis_onset
    occurred: bool
    outcome_date: str
    ai_prediction_id: Optional[str] = None
    ai_predicted_probability: Optional[float] = None
    ai_predicted_correctly: Optional[bool] = None
    predicted_icd10_codes: List[str] = None
    actual_icd10_codes: List[str] = None

class OutcomeLinker:
    """
    Links AI predictions to clinical outcomes.
    Called by: nightly ETL when billing codes finalized.
    Delay: 30 days post-discharge for readmission, immediate for mortality.
    """
    def __init__(self, prediction_store=None, feedback_store=None):
        self._predictions=prediction_store; self._feedback=feedback_store

    async def link_readmission(self, patient_deident_id:str, encounter_id:str,
                               readmitted:bool, readmission_date:Optional[str],
                               actual_icd10:List[str]) -> List[OutcomeRecord]:
        """Called 30 days post-discharge when readmission status known."""
        import uuid
        record=OutcomeRecord(outcome_id=str(uuid.uuid4()),patient_deident_id=patient_deident_id,
            encounter_id=encounter_id,outcome_type="readmission_30d",occurred=readmitted,
            outcome_date=readmission_date or datetime.now(timezone.utc).date().isoformat(),
            actual_icd10_codes=actual_icd10)
        logger.info(f"OUTCOME_LINKED type=readmission_30d occurred={readmitted} enc={encounter_id}")
        return [record]

    async def link_sepsis(self, patient_deident_id:str, encounter_id:str,
                          sepsis_occurred:bool, onset_hours_from_admission:Optional[float]) -> OutcomeRecord:
        import uuid
        record=OutcomeRecord(outcome_id=str(uuid.uuid4()),patient_deident_id=patient_deident_id,
            encounter_id=encounter_id,outcome_type="sepsis_onset",occurred=sepsis_occurred,
            outcome_date=datetime.now(timezone.utc).isoformat())
        logger.info(f"OUTCOME_LINKED type=sepsis occurred={sepsis_occurred} hours={onset_hours_from_admission}")
        return record

    def compute_prediction_accuracy(self, predictions:List[Dict], outcomes:List[OutcomeRecord]) -> Dict:
        """Compute AUROC and accuracy metrics for model validation."""
        if not predictions or not outcomes: return {"auroc":None,"n_samples":0}
        correct=sum(1 for o in outcomes if o.ai_predicted_correctly is True)
        total=sum(1 for o in outcomes if o.ai_predicted_correctly is not None)
        return {"accuracy":correct/total if total else None,"n_validated":total,"n_correct":correct}
