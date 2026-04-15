"""
Pharmacist Agent — Drug Safety
CRITICAL alerts bypass all other agent outputs.
Response time: pharmacist + physician within 1 minute for CRITICAL.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional
import time
from datetime import datetime, timezone

@dataclass
class DrugAlert:
    alert_type: str       # drug_drug_interaction|renal_dose|allergy_cross_reactivity|dose_validation
    severity: str         # CRITICAL|WARNING|INFO
    drugs_involved: List[str]
    description: str
    recommendation: str
    evidence_source: str = "Micromedex/Clinical Pharmacology"

@dataclass
class PharmacistOutput:
    alert_level: str      # CRITICAL|WARNING|NONE
    alerts: List[DrugAlert]
    medications_reviewed: int
    bypass_coordinator: bool
    requires_immediate_action: bool

CRITICAL_DDI = {
    frozenset(["warfarin","aspirin"]): "Major bleeding risk — warfarin (anticoagulant) + aspirin (antiplatelet): combined use increases serious bleeding risk 3-4x",
    frozenset(["ssri","maoi"]): "LIFE-THREATENING: SSRI + MAOI — serotonin syndrome risk (potentially fatal)",
    frozenset(["metformin","contrast"]): "Lactic acidosis risk — metformin + contrast dye: hold metformin 48h pre/post procedure",
    frozenset(["vancomycin","piperacillin"]): "Nephrotoxicity risk — vancomycin + piperacillin/tazobactam: AKI incidence increased, monitor SCr daily",
    frozenset(["amiodarone","simvastatin"]): "Myopathy/rhabdomyolysis risk — amiodarone + simvastatin: CYP3A4 inhibition, reduce statin dose",
    frozenset(["clopidogrel","omeprazole"]): "Reduced antiplatelet effect — clopidogrel + omeprazole: CYP2C19 inhibition reduces clopidogrel efficacy, use pantoprazole",
    frozenset(["linezolid","ssri"]): "Serotonin syndrome risk — linezolid (weak MAOI) + SSRI: monitor for agitation, hyperthermia, tachycardia",
    frozenset(["quinolone","antacid"]): "Reduced absorption — quinolone (ciprofloxacin/levofloxacin) + antacid/calcium: chelation reduces antibiotic bioavailability by 50-90%",
}

RENALLY_CLEARED = ["vancomycin","gentamicin","metformin","digoxin","lisinopril",
                   "enoxaparin","ciprofloxacin","levofloxacin","penicillin","cephalexin"]

ALLERGY_CROSS = {
    "penicillin": ["amoxicillin","ampicillin","piperacillin","nafcillin"],
    "sulfa": ["sulfamethoxazole","sulfadiazine","furosemide","hydrochlorothiazide"],
    "cephalosporin": ["cephalexin","ceftriaxone","cefazolin","cefepime"],
    "nsaid": ["ibuprofen","naproxen","ketorolac","indomethacin"],
}

class PharmacistAgent:
    AGENT_ID = "pharmacist_agent"

    async def run(self, patient_id:str, current_medications:List[Dict],
                  new_medication:Optional[Dict]=None, patient_weight_kg:Optional[float]=None,
                  renal_function_gfr:Optional[float]=None, allergies:Optional[List[str]]=None,
                  hepatic_function:Optional[str]=None) -> Dict:
        start = time.time()
        all_meds = list(current_medications) + ([new_medication] if new_medication else [])
        alerts: List[DrugAlert] = []
        alerts.extend(self._check_ddi(all_meds))
        if new_medication and renal_function_gfr is not None:
            alerts.extend(self._check_renal(new_medication, renal_function_gfr))
        if new_medication and allergies:
            alerts.extend(self._check_allergy(new_medication, allergies))

        alert_level = ("CRITICAL" if any(a.severity=="CRITICAL" for a in alerts)
                       else "WARNING" if any(a.severity=="WARNING" for a in alerts) else "NONE")
        output = PharmacistOutput(
            alert_level=alert_level, alerts=alerts,
            medications_reviewed=len(all_meds),
            bypass_coordinator=alert_level=="CRITICAL",
            requires_immediate_action=alert_level=="CRITICAL")

        return {"agent_id":self.AGENT_ID,"patient_id":patient_id,"status":"completed",
                "output":{**output.__dict__,"alerts":[a.__dict__ for a in output.alerts]},
                "execution_ms":int((time.time()-start)*1000),
                "timestamp":datetime.now(timezone.utc).isoformat()}

    def _check_ddi(self, meds:List[Dict]) -> List[DrugAlert]:
        alerts = []
        names = [m.get("name","").lower() for m in meds]
        for drug_pair, desc in CRITICAL_DDI.items():
            drug_list = list(drug_pair)
            if all(any(d in name for name in names) for d in drug_list):
                alerts.append(DrugAlert(
                    alert_type="drug_drug_interaction",
                    severity="CRITICAL" if "LIFE-THREATENING" in desc else "WARNING",
                    drugs_involved=drug_list,
                    description=desc,
                    recommendation="Consult pharmacist and prescriber immediately. Consider alternative therapy."))
        return alerts

    def _check_renal(self, med:Dict, gfr:float) -> List[DrugAlert]:
        name = med.get("name","").lower()
        alerts = []
        if gfr < 30 and any(drug in name for drug in RENALLY_CLEARED):
            alerts.append(DrugAlert(
                alert_type="renal_dose_adjustment",
                severity="CRITICAL" if gfr<15 else "WARNING",
                drugs_involved=[med.get("name","")],
                description=f"GFR {gfr:.0f} mL/min — renally cleared drug requires dose adjustment",
                recommendation="Consult renal dosing guidelines or clinical pharmacist for adjusted dose/frequency"))
        return alerts

    def _check_allergy(self, med:Dict, allergies:List[str]) -> List[DrugAlert]:
        name = med.get("name","").lower()
        alerts = []
        for allergy in allergies:
            al = allergy.lower()
            for base, related in ALLERGY_CROSS.items():
                if base in al and any(r in name for r in related):
                    alerts.append(DrugAlert(
                        alert_type="allergy_cross_reactivity",
                        severity="CRITICAL",
                        drugs_involved=[allergy, med.get("name","")],
                        description=f"Cross-reactivity: Known {allergy} allergy + {med.get('name','')}",
                        recommendation="Verify allergy history (true allergy vs intolerance). Consider alternative agent."))
        return alerts
