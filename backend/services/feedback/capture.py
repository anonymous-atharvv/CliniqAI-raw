"""
Feedback Capture Service
Implicit (physician actions) + Explicit (thumbs) + Outcome (30-day) signals.
Must add <3 seconds to physician workflow or adoption = zero.
"""
import uuid, json, logging
from datetime import datetime, timezone
from typing import Optional, Dict, List
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class FeedbackEvent:
    feedback_id: str
    feedback_at: str
    patient_deident_id: str
    encounter_id: str
    ai_output_type: str
    ai_prediction: Dict
    signal: str
    ml_signal: float
    actor_role: str
    is_treating_physician: bool
    is_in_distribution: bool
    modification_details: Optional[Dict] = None
    free_text_reason: Optional[str] = None
    outcome_linked: bool = False

SIGNAL_MAP = {"accepted":1.0,"thumbs_up":1.0,"modified":0.5,"rejected":-0.5,"thumbs_down":-1.0}

class FeedbackCaptureService:
    def __init__(self, store=None, kafka=None):
        self._store=store; self._kafka=kafka

    async def capture_implicit(self, patient_deident_id:str, encounter_id:str,
                               actor_role:str, ai_output_type:str, ai_prediction:Dict,
                               physician_action:str, modification_details:Optional[Dict]=None) -> FeedbackEvent:
        action_map={"accepted":"accepted","modified":"modified","rejected":"rejected","ordered_different":"rejected"}
        signal=action_map.get(physician_action,"rejected")
        event=FeedbackEvent(feedback_id=str(uuid.uuid4()),feedback_at=datetime.now(timezone.utc).isoformat(),
            patient_deident_id=patient_deident_id,encounter_id=encounter_id,
            ai_output_type=ai_output_type,ai_prediction=ai_prediction,signal=signal,
            ml_signal=SIGNAL_MAP.get(signal,0.0),actor_role=actor_role,
            is_treating_physician=True,is_in_distribution=True,modification_details=modification_details)
        await self._persist(event)
        logger.info(f"FEEDBACK implicit signal={signal} type={ai_output_type}")
        return event

    async def capture_explicit(self, patient_deident_id:str, encounter_id:str,
                               actor_role:str, ai_output_type:str, ai_prediction:Dict,
                               is_positive:bool, reason:Optional[str]=None) -> FeedbackEvent:
        signal="thumbs_up" if is_positive else "thumbs_down"
        # Quality filter: thumbs_down with no reason = low signal
        in_distribution = not (not is_positive and not reason)
        event=FeedbackEvent(feedback_id=str(uuid.uuid4()),feedback_at=datetime.now(timezone.utc).isoformat(),
            patient_deident_id=patient_deident_id,encounter_id=encounter_id,
            ai_output_type=ai_output_type,ai_prediction=ai_prediction,signal=signal,
            ml_signal=SIGNAL_MAP[signal],actor_role=actor_role,
            is_treating_physician=True,is_in_distribution=in_distribution,free_text_reason=reason)
        await self._persist(event)
        return event

    async def _persist(self, event:FeedbackEvent):
        if self._store: await self._store.save(event)
        if self._kafka: await self._kafka.send("ai.feedback",json.dumps(asdict(event),default=str))
        else: logger.debug(f"FEEDBACK_EVENT {json.dumps(asdict(event),default=str)}")
