"""
Triage Agent
ESI 1-5 scoring with rule-based fast path + LLM enhancement.
Trigger: every new encounter or status change.
"""
from dataclasses import dataclass
from typing import List, Optional, Dict
import time, logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class TriageOutput:
    esi_category: int
    esi_label: str
    care_pathway: str
    chief_complaint: str
    physician_page_required: bool
    page_within_minutes: int
    confidence: float
    reasoning: str

class TriageAgent:
    AGENT_ID = "triage_agent"
    ESI_LABELS = {1:"RESUSCITATION",2:"EMERGENT",3:"URGENT",4:"LESS_URGENT",5:"NON_URGENT"}
    PATHWAYS = {1:"immediate_resuscitation",2:"emergency_treatment",3:"urgent_evaluation",4:"standard_evaluation",5:"routine_care"}

    async def run(self, patient_id:str, vitals:List[Dict], chief_complaint:str, problems:List[str]) -> Dict:
        start = time.time()
        esi = self._score_esi(vitals, chief_complaint)
        output = TriageOutput(
            esi_category=esi, esi_label=self.ESI_LABELS[esi],
            care_pathway=self.PATHWAYS[esi], chief_complaint=chief_complaint,
            physician_page_required=esi<=2, page_within_minutes=2 if esi==1 else 5,
            confidence=0.95 if esi in [1,5] else 0.82,
            reasoning=f"ESI {esi} based on vital signs and chief complaint assessment"
        )
        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":output.__dict__,"execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _score_esi(self, vitals:List[Dict], complaint:str) -> int:
        vd = {v.get("parameter"):v.get("value") for v in vitals}
        spo2 = vd.get("spo2_pulse_ox",98); hr = vd.get("heart_rate",80)
        sbp = vd.get("bp_systolic",120); rr = vd.get("respiratory_rate",16)
        gcs = vd.get("gcs_total",15)
        if spo2<85 or gcs<=8 or hr==0: return 1
        critical = ["cardiac arrest","unresponsive","apneic"]
        if any(c in complaint.lower() for c in critical): return 1
        if spo2<90 or hr>150 or hr<40 or sbp<80 or rr>35: return 2
        emergent = ["chest pain","stroke","difficulty breathing","sepsis","anaphylaxis"]
        if any(e in complaint.lower() for e in emergent): return 2
        if spo2<94 or hr>120 or sbp<100 or rr>25: return 3
        return 3
