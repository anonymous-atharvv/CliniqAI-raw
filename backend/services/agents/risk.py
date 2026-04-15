"""Risk Agent — continuous monitoring every 15 min ICU / 60 min ward."""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import time
from datetime import datetime, timezone

@dataclass
class RiskOutput:
    risk_level: str
    news2_score: int
    sofa_estimate: int
    ai_predictions: Dict
    risk_factors: List[str]
    trend: str
    alert_required: bool
    alert_priority: str

class RiskAgent:
    AGENT_ID = "risk_agent"

    async def run(self, patient_id:str, vitals:List[Dict],
                  ai_predictions:Dict, medications:List[Dict],
                  comorbidities:List[str]) -> Dict:
        start = time.time()
        vd = {v.get("parameter"):v.get("value") for v in vitals}
        news2 = self._news2(vd); sofa = self._sofa(vd, medications)
        risk_level, factors, priority = self._classify(news2, sofa, ai_predictions)
        output = RiskOutput(
            risk_level=risk_level, news2_score=news2, sofa_estimate=sofa,
            ai_predictions=ai_predictions, risk_factors=factors,
            trend=self._trend(vitals), alert_required=risk_level in ["HIGH","CRITICAL"],
            alert_priority=priority)
        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":output.__dict__,"execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _news2(self, v:Dict) -> int:
        s=0; rr=v.get("respiratory_rate"); spo2=v.get("spo2_pulse_ox"); sbp=v.get("bp_systolic"); hr=v.get("heart_rate"); t=v.get("temperature")
        if rr: s+=3 if rr<=8 else(1 if rr<=11 else(0 if rr<=20 else(2 if rr<=24 else 3)))
        if spo2: s+=3 if spo2<=91 else(2 if spo2<=93 else(1 if spo2<=95 else 0))
        if sbp: s+=3 if sbp<=90 else(2 if sbp<=100 else(1 if sbp<=110 else(0 if sbp<=219 else 3)))
        if hr: s+=3 if hr<=40 else(1 if hr<=50 else(0 if hr<=90 else(1 if hr<=110 else(2 if hr<=130 else 3))))
        if t: s+=3 if t<=35 else(1 if t<=36 else(0 if t<=38 else(1 if t<=39 else 2)))
        return s

    def _sofa(self, v:Dict, meds:List[Dict]) -> int:
        s=0; spo2=v.get("spo2_pulse_ox",98); sbp=v.get("bp_systolic",120)
        if spo2<90:s+=3
        elif spo2<93:s+=2
        elif spo2<96:s+=1
        if sbp<90:s+=2
        vas=["norepinephrine","epinephrine","dopamine","vasopressin"]
        if any(any(x in m.get("name","").lower() for x in vas) for m in meds):s+=2
        return s

    def _classify(self, news2:int, sofa:int, preds:Dict):
        factors=[]; priority="NONE"
        if news2>=7:factors.append(f"NEWS2={news2} (≥7: urgent response)")
        elif news2>=5:factors.append(f"NEWS2={news2} (≥5: high alert)")
        if preds.get("deterioration_6h",0)>0.70:factors.append(f"AI deterioration {preds['deterioration_6h']:.0%}/6h")
        if preds.get("sepsis_12h",0)>0.50:factors.append(f"AI sepsis {preds['sepsis_12h']:.0%}/12h")
        if sofa>=6:factors.append(f"SOFA={sofa}")
        if news2>=7 or preds.get("mortality_24h",0)>0.6:return "CRITICAL",factors,"CRITICAL"
        if news2>=5 or preds.get("deterioration_6h",0)>0.70:return "HIGH",factors,"HIGH"
        if news2>=3:return "MEDIUM",factors,"MEDIUM"
        return "LOW",factors,"NONE"

    def _trend(self, vitals:List[Dict]) -> str:
        if len(vitals)<10:return "stable"
        third=len(vitals)//3
        early={v.get("parameter"):v.get("value") for v in vitals[:third] if v.get("parameter") and v.get("value") is not None}
        late={v.get("parameter"):v.get("value") for v in vitals[-third:] if v.get("parameter") and v.get("value") is not None}
        early_n2=self._news2(early); late_n2=self._news2(late)
        if late_n2-early_n2>=3:return "worsening"
        if early_n2-late_n2>=2:return "improving"
        return "stable"
