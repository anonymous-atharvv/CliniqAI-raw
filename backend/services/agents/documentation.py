"""Documentation Agent — SOAP note generation and ICD-10 coding."""
from typing import List, Dict, Optional
from datetime import datetime, timezone
import time, uuid

class DocumentationAgent:
    AGENT_ID = "documentation_agent"

    async def run(self, patient_id:str, agent_outputs:Dict,
                  physician_input:Optional[str]=None, doc_type:str="progress_note") -> Dict:
        start = time.time()
        risk = agent_outputs.get("risk_agent",{}).get("output",{})
        dx = agent_outputs.get("diagnosis_agent",{}).get("output",{})
        differentials = dx.get("differentials",[])
        primary_dx = next((d["condition"] for d in differentials if d.get("probability_rank")=="primary"),"Undifferentiated") if differentials else "Undifferentiated"
        news2 = risk.get("news2_score",0); risk_level = risk.get("risk_level","LOW")
        icd10 = next((d["icd10"] for d in differentials if d.get("probability_rank")=="primary"),"R69") if differentials else "R69"

        soap = {
            "note_id": str(uuid.uuid4()),
            "document_type": doc_type,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "DRAFT — Physician review and signature required",
            "subjective": physician_input or "Patient reported symptoms as per nursing notes. See clinical chart for details.",
            "objective": (f"Vitals: NEWS2={news2} ({risk_level} risk). "
                         f"AI assessment: {risk_level} risk level. "
                         f"See nursing flowsheet for complete vital signs."),
            "assessment": (f"Primary working diagnosis: {primary_dx}. "
                          f"{len(differentials)} differential(s) considered by AI clinical reasoning. "
                          f"Clinical risk level: {risk_level}."),
            "plan": self._generate_plan(agent_outputs),
            "suggested_icd10": [{"code":icd10,"description":primary_dx,"rank":"primary"}],
            "documentation_gaps": self._check_gaps(agent_outputs),
            "physician_signature_required": True,
            "ai_disclaimer": "This is an AI-generated draft. Physician must review, modify as needed, and sign before this constitutes a clinical note.",
        }
        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":soap,"execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _generate_plan(self, outputs:Dict) -> str:
        actions = []
        risk_out = outputs.get("risk_agent",{}).get("output",{})
        pharm_out = outputs.get("pharmacist_agent",{}).get("output",{})
        if risk_out.get("alert_required"):
            actions.append("1. Continue close monitoring with NEWS2 q2h")
        if pharm_out.get("alerts"):
            actions.append(f"2. Review {len(pharm_out['alerts'])} pharmacy alert(s) with prescriber")
        if not actions: actions.append("1. Continue current management plan as per attending physician")
        return " | ".join(actions)

    def _check_gaps(self, outputs:Dict) -> List[str]:
        gaps = []
        if not outputs.get("diagnosis_agent"): gaps.append("Differential diagnosis not documented")
        if not outputs.get("pharmacist_agent"): gaps.append("Medication reconciliation pending")
        return gaps


"""Coordinator Agent — synthesizes all agent outputs, resolves conflicts."""
class CoordinatorAgent:
    AGENT_ID = "coordinator_agent"
    RISK_RANK = {"CRITICAL":4,"HIGH":3,"MEDIUM":2,"LOW":1}

    async def run(self, patient_id:str, agent_outputs:Dict) -> Dict:
        start = time.time()
        pharm = agent_outputs.get("pharmacist_agent",{}).get("output",{})
        if pharm.get("bypass_coordinator") and pharm.get("alert_level")=="CRITICAL":
            return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                    "output":{"recommendation_type":"pharmacy_critical_override",
                              "primary_concern":pharm,"coordinator_confidence":1.0,
                              "requires_physician":True,"response_time_minutes":1},
                    "execution_ms":int((time.time()-start)*1000),
                    "timestamp":datetime.now(timezone.utc).isoformat()}

        max_risk="LOW"; confidences=[]
        for name,out in agent_outputs.items():
            o = out.get("output",{})
            rl = o.get("risk_level") or o.get("alert_level","LOW")
            if self.RISK_RANK.get(rl,0)>self.RISK_RANK.get(max_risk,0): max_risk=rl
            confidences.append(out.get("confidence",0.8))
        avg_conf=sum(confidences)/len(confidences) if confidences else 0.5
        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":{"recommendation_type":"consensus","unified_risk":max_risk,
                          "coordinator_confidence":round(avg_conf,3),
                          "contributing_agents":list(agent_outputs.keys()),
                          "requires_human_arbitration":avg_conf<0.6},
                "execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}


"""Escalation Agent — SLA-enforced critical alert routing."""
import uuid as _uuid
class EscalationAgent:
    AGENT_ID = "escalation_agent"
    SLA = {"ESI_1":2,"CRITICAL_RISK":5,"PHARMACY_CRITICAL":1,"SEPSIS_BUNDLE":5}

    def __init__(self, notifier=None): self._notifier=notifier

    async def run(self, patient_id:str, coordinator_output:Dict,
                  triage_output:Optional[Dict]=None, risk_output:Optional[Dict]=None,
                  pharmacist_output:Optional[Dict]=None) -> Dict:
        start = time.time(); escalations=[]
        coord = coordinator_output.get("output",{})
        risk_level = coord.get("unified_risk","LOW")
        triage_esi = (triage_output or {}).get("output",{}).get("esi_category",5)
        pharm_level = (pharmacist_output or {}).get("output",{}).get("alert_level","NONE")
        risk_preds = (risk_output or {}).get("output",{}).get("ai_predictions",{})

        if triage_esi==1: escalations.append(self._make(patient_id,"ESI_1",["physician_attending","charge_nurse"],"RESUSCITATION: ESI 1"))
        if risk_level=="CRITICAL": escalations.append(self._make(patient_id,"CRITICAL_RISK",["physician_attending","charge_nurse"],"CRITICAL risk level"))
        if pharm_level=="CRITICAL": escalations.append(self._make(patient_id,"PHARMACY_CRITICAL",["pharmacist_oncall","physician_attending"],"CRITICAL drug alert"))
        if risk_preds.get("sepsis_12h",0)>0.7: escalations.append(self._make(patient_id,"SEPSIS_BUNDLE",["physician_attending","charge_nurse","rapid_response"],"Sepsis probability >70%"))

        for e in escalations:
            if self._notifier:
                try: await self._notifier.send(e)
                except: pass

        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":{"escalations_sent":len(escalations),"escalations":escalations},
                "execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _make(self, patient_id:str, etype:str, recipients:List[str], msg:str) -> Dict:
        from datetime import timedelta
        sla = self.SLA.get(etype,5)
        deadline = (datetime.now(timezone.utc)+timedelta(minutes=sla)).isoformat()
        return {"escalation_id":str(_uuid.uuid4()),"patient_id":patient_id,
                "type":etype,"recipients":recipients,"message":msg,
                "created_at":datetime.now(timezone.utc).isoformat(),
                "sla_deadline":deadline,"acknowledged":False}
