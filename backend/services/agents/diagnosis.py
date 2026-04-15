"""
Diagnosis Agent
Differential diagnosis generation. MUST cite specific evidence for each hypothesis.
Trigger: on physician request or when Triage scores ESI 1-2.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict
import time, logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class Differential:
    condition: str
    icd10: str
    supporting_evidence: List[str]
    contradicting_evidence: List[str]
    probability_rank: str  # primary|alternative|rule_out
    confidence: float

@dataclass
class DiagnosisOutput:
    differentials: List[Differential]
    primary_hypothesis: Optional[Differential]
    reasoning_chain: str
    additional_workup_needed: List[str]
    confidence_overall: float

class DiagnosisAgent:
    AGENT_ID = "diagnosis_agent"

    async def run(self, patient_id:str, vitals:List[Dict], labs:List[Dict],
                  medications:List[Dict], conditions:List[str], chief_complaint:str,
                  nlp_entities:List[Dict]=None) -> Dict:
        start = time.time()
        differentials = self._generate_differentials(vitals, labs, conditions, chief_complaint)
        primary = next((d for d in differentials if d.probability_rank=="primary"), None)
        output = DiagnosisOutput(
            differentials=differentials, primary_hypothesis=primary,
            reasoning_chain=self._build_reasoning(differentials, chief_complaint),
            additional_workup_needed=self._suggest_workup(differentials),
            confidence_overall=primary.confidence if primary else 0.5
        )
        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":{**output.__dict__,"differentials":[d.__dict__ for d in output.differentials],
                          "primary_hypothesis":primary.__dict__ if primary else None},
                "execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _generate_differentials(self, vitals, labs, conditions, complaint) -> List[Differential]:
        vd = {v.get("parameter"):v.get("value") for v in vitals}
        ld = {l.get("test","").lower():l.get("value") for l in labs}
        differentials = []
        hr = vd.get("heart_rate",80); temp = vd.get("temperature",37.0)
        spo2 = vd.get("spo2_pulse_ox",97); sbp = vd.get("bp_systolic",120)
        fever = temp>38.0; tachy = hr>100; hypox = spo2<94
        wbc_high = ld.get("wbc",10)>12; lactate_high = ld.get("lactate",1.0)>2.0

        if fever and tachy and wbc_high:
            evidence = []
            if fever: evidence.append(f"Fever {temp}°C (>38.0°C)")
            if tachy: evidence.append(f"Tachycardia HR {hr} bpm")
            if wbc_high: evidence.append(f"Leukocytosis WBC {ld.get('wbc')}")
            if lactate_high: evidence.append(f"Elevated lactate {ld.get('lactate')} mmol/L")
            differentials.append(Differential(
                condition="Sepsis", icd10="A41.9", supporting_evidence=evidence,
                contradicting_evidence=["Source not confirmed — cultures pending"] if not lactate_high else [],
                probability_rank="primary", confidence=0.72 if lactate_high else 0.58))

        if hypox and ("dyspnea" in complaint.lower() or "breathing" in complaint.lower()):
            evidence = [f"SpO₂ {spo2}% (hypoxemia)"]
            if fever: evidence.append(f"Fever {temp}°C")
            differentials.append(Differential(
                condition="Community-Acquired Pneumonia", icd10="J18.9",
                supporting_evidence=evidence,
                contradicting_evidence=["CXR pending — cannot confirm infiltrate"],
                probability_rank="primary" if not differentials else "alternative",
                confidence=0.61))

        if not differentials:
            differentials.append(Differential(
                condition="Undifferentiated presentation — further workup required",
                icd10="R69", supporting_evidence=[chief_complaint],
                contradicting_evidence=[], probability_rank="primary", confidence=0.40))
        return differentials

    def _build_reasoning(self, differentials, complaint) -> str:
        if not differentials: return "Insufficient data for differential generation."
        primary = next((d for d in differentials if d.probability_rank=="primary"), differentials[0])
        return (f"Chief complaint: {complaint}. Leading hypothesis: {primary.condition} "
                f"supported by {', '.join(primary.supporting_evidence[:2])}. "
                f"{len(differentials)} differential(s) considered.")

    def _suggest_workup(self, differentials) -> List[str]:
        workup = []
        conditions = [d.condition.lower() for d in differentials]
        if any("sepsis" in c for c in conditions):
            workup.extend(["Blood cultures × 2", "Serum lactate", "Procalcitonin"])
        if any("pneumonia" in c for c in conditions):
            workup.extend(["Chest X-ray", "Sputum culture if productive cough"])
        if not workup:
            workup.append("Basic metabolic panel + CBC with differential")
        return list(set(workup))
