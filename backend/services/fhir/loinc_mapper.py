"""
LOINC Code Mapper
Complete registry of LOINC codes used in CliniQAI.
Used by the FHIR normalizer to validate and look up codes.
All codes sourced from LOINC.org (freely available for use).
"""
from typing import Optional, Dict, List, Tuple


# ─── Master LOINC Registry ────────────────────────────────────────────────────

LOINC_REGISTRY: Dict[str, Dict] = {
    # ── Vital Signs ──────────────────────────────────────────────────────────
    "heart_rate":           {"code": "8867-4",  "display": "Heart rate",                       "unit": "/min",        "category": "vital-signs"},
    "spo2_arterial":        {"code": "2708-6",  "display": "Oxygen saturation in Arterial blood","unit": "%",          "category": "vital-signs"},
    "spo2_pulse_ox":        {"code": "59408-5", "display": "Oxygen saturation by Pulse ox",     "unit": "%",           "category": "vital-signs"},
    "bp_systolic":          {"code": "8480-6",  "display": "Systolic blood pressure",           "unit": "mm[Hg]",      "category": "vital-signs"},
    "bp_diastolic":         {"code": "8462-4",  "display": "Diastolic blood pressure",          "unit": "mm[Hg]",      "category": "vital-signs"},
    "bp_mean":              {"code": "8478-0",  "display": "Mean blood pressure",               "unit": "mm[Hg]",      "category": "vital-signs"},
    "temperature":          {"code": "8310-5",  "display": "Body temperature",                  "unit": "Cel",         "category": "vital-signs"},
    "respiratory_rate":     {"code": "9279-1",  "display": "Respiratory rate",                  "unit": "/min",        "category": "vital-signs"},
    "gcs_total":            {"code": "9269-2",  "display": "Glasgow coma score total",          "unit": "{score}",     "category": "vital-signs"},
    "weight":               {"code": "29463-7", "display": "Body weight",                       "unit": "kg",          "category": "vital-signs"},
    "height":               {"code": "8302-2",  "display": "Body height",                       "unit": "cm",          "category": "vital-signs"},
    "bmi":                  {"code": "39156-5", "display": "Body mass index",                   "unit": "kg/m2",       "category": "vital-signs"},
    "pain_score":           {"code": "72514-3", "display": "Pain severity 0-10 verbal",         "unit": "{score}",     "category": "vital-signs"},

    # ── Haematology ──────────────────────────────────────────────────────────
    "wbc":                  {"code": "6690-2",  "display": "Leukocytes [#/volume] in Blood",   "unit": "10*3/uL",     "category": "laboratory"},
    "hemoglobin":           {"code": "718-7",   "display": "Hemoglobin [Mass/volume] in Blood","unit": "g/dL",        "category": "laboratory"},
    "hematocrit":           {"code": "20570-8", "display": "Hematocrit [Volume Fraction]",     "unit": "%",           "category": "laboratory"},
    "platelets":            {"code": "777-3",   "display": "Platelets [#/volume] in Blood",    "unit": "10*3/uL",     "category": "laboratory"},
    "neutrophils":          {"code": "751-8",   "display": "Neutrophils [#/volume] in Blood",  "unit": "10*3/uL",     "category": "laboratory"},
    "lymphocytes":          {"code": "731-0",   "display": "Lymphocytes [#/volume] in Blood",  "unit": "10*3/uL",     "category": "laboratory"},

    # ── Chemistry ────────────────────────────────────────────────────────────
    "sodium":               {"code": "2951-2",  "display": "Sodium [Moles/volume] in Serum",   "unit": "mmol/L",      "category": "laboratory"},
    "potassium":            {"code": "2823-3",  "display": "Potassium [Moles/volume] in Serum","unit": "mmol/L",      "category": "laboratory"},
    "chloride":             {"code": "2075-0",  "display": "Chloride [Moles/volume] in Serum", "unit": "mmol/L",      "category": "laboratory"},
    "co2":                  {"code": "2028-9",  "display": "Carbon dioxide, total [Moles/volume]","unit": "mmol/L",   "category": "laboratory"},
    "bun":                  {"code": "3094-0",  "display": "Urea nitrogen [Mass/volume] in Serum","unit": "mg/dL",    "category": "laboratory"},
    "creatinine":           {"code": "2160-0",  "display": "Creatinine [Mass/volume] in Serum","unit": "mg/dL",       "category": "laboratory"},
    "glucose":              {"code": "2345-7",  "display": "Glucose [Mass/volume] in Serum",   "unit": "mg/dL",       "category": "laboratory"},
    "calcium":              {"code": "17861-6", "display": "Calcium [Mass/volume] in Serum",   "unit": "mg/dL",       "category": "laboratory"},
    "magnesium":            {"code": "2601-3",  "display": "Magnesium [Moles/volume] in Serum","unit": "mmol/L",      "category": "laboratory"},
    "phosphorus":           {"code": "2777-1",  "display": "Phosphate [Moles/volume] in Serum","unit": "mmol/L",      "category": "laboratory"},
    "albumin":              {"code": "1751-7",  "display": "Albumin [Mass/volume] in Serum",   "unit": "g/dL",        "category": "laboratory"},
    "total_protein":        {"code": "2885-2",  "display": "Protein [Mass/volume] in Serum",   "unit": "g/dL",        "category": "laboratory"},

    # ── Liver Function ───────────────────────────────────────────────────────
    "alt":                  {"code": "1742-6",  "display": "Alanine aminotransferase [Enzymatic activity/volume]","unit": "U/L","category": "laboratory"},
    "ast":                  {"code": "1920-8",  "display": "Aspartate aminotransferase [Enzymatic activity/volume]","unit": "U/L","category": "laboratory"},
    "bilirubin_total":      {"code": "1975-2",  "display": "Bilirubin.total [Mass/volume] in Serum","unit": "mg/dL", "category": "laboratory"},
    "bilirubin_direct":     {"code": "1968-7",  "display": "Bilirubin.direct [Mass/volume] in Serum","unit": "mg/dL","category": "laboratory"},
    "alk_phos":             {"code": "6768-6",  "display": "Alkaline phosphatase [Enzymatic activity/volume]","unit": "U/L","category": "laboratory"},
    "ggt":                  {"code": "2324-2",  "display": "Gamma glutamyl transferase [Enzymatic activity/volume]","unit": "U/L","category": "laboratory"},

    # ── Coagulation ──────────────────────────────────────────────────────────
    "pt":                   {"code": "5902-2",  "display": "Prothrombin time (PT)",             "unit": "s",           "category": "laboratory"},
    "inr":                  {"code": "6301-6",  "display": "INR in Platelet poor plasma",       "unit": "{INR}",       "category": "laboratory"},
    "ptt":                  {"code": "3173-2",  "display": "aPTT in Blood by Coagulation assay","unit": "s",           "category": "laboratory"},
    "d_dimer":              {"code": "48065-7", "display": "Fibrin D-dimer DDU [Mass/volume]",  "unit": "ug/mL{DDU}",  "category": "laboratory"},
    "fibrinogen":           {"code": "3255-7",  "display": "Fibrinogen [Mass/volume] in Platelet poor plasma","unit": "mg/dL","category": "laboratory"},

    # ── Inflammatory ─────────────────────────────────────────────────────────
    "crp":                  {"code": "1988-5",  "display": "C reactive protein [Mass/volume] in Serum","unit": "mg/L", "category": "laboratory"},
    "procalcitonin":        {"code": "75241-0", "display": "Procalcitonin [Mass/volume] in Serum","unit": "ng/mL",     "category": "laboratory"},
    "esr":                  {"code": "30341-2", "display": "Erythrocyte sedimentation rate",    "unit": "mm/h",        "category": "laboratory"},
    "ferritin":             {"code": "2276-4",  "display": "Ferritin [Mass/volume] in Serum",   "unit": "ng/mL",       "category": "laboratory"},
    "il6":                  {"code": "26881-3", "display": "Interleukin 6 [Mass/volume] in Serum","unit": "pg/mL",     "category": "laboratory"},

    # ── Cardiac ──────────────────────────────────────────────────────────────
    "troponin_i":           {"code": "10839-9", "display": "Troponin I.cardiac [Mass/volume] in Serum","unit": "ug/L", "category": "laboratory"},
    "troponin_t":           {"code": "6598-7",  "display": "Troponin T.cardiac [Mass/volume] in Serum","unit": "ug/L", "category": "laboratory"},
    "nt_probnp":            {"code": "33762-6", "display": "NT-proBNP [Mass/volume] in Serum",  "unit": "pg/mL",       "category": "laboratory"},
    "bnp":                  {"code": "42637-9", "display": "Natriuretic peptide B [Mass/volume]","unit": "pg/mL",      "category": "laboratory"},
    "ck_mb":                {"code": "13969-1", "display": "Creatine kinase.MB [Mass/volume] in Serum","unit": "ng/mL","category": "laboratory"},

    # ── Metabolic / Critical Care ─────────────────────────────────────────────
    "lactate":              {"code": "2519-7",  "display": "Lactate [Moles/volume] in Blood",  "unit": "mmol/L",      "category": "laboratory"},
    "lactate_venous":       {"code": "32693-4", "display": "Lactate [Moles/volume] in Venous blood","unit": "mmol/L", "category": "laboratory"},
    "ammonia":              {"code": "1845-7",  "display": "Ammonia [Moles/volume] in Blood",  "unit": "umol/L",      "category": "laboratory"},
    "hba1c":                {"code": "4548-4",  "display": "Hemoglobin A1c/Hemoglobin.total in Blood","unit": "%",    "category": "laboratory"},
    "lipase":               {"code": "3040-3",  "display": "Lipase [Enzymatic activity/volume] in Serum","unit": "U/L","category": "laboratory"},
    "amylase":              {"code": "1798-8",  "display": "Amylase [Enzymatic activity/volume] in Serum","unit": "U/L","category": "laboratory"},

    # ── Blood Gas ────────────────────────────────────────────────────────────
    "ph_arterial":          {"code": "2744-1",  "display": "pH of Arterial blood",             "unit": "[pH]",        "category": "laboratory"},
    "pao2":                 {"code": "2703-7",  "display": "Oxygen [Partial pressure] in Arterial blood","unit": "mm[Hg]","category": "laboratory"},
    "paco2":                {"code": "2019-8",  "display": "Carbon dioxide [Partial pressure] in Arterial blood","unit": "mm[Hg]","category": "laboratory"},
    "hco3_arterial":        {"code": "1960-4",  "display": "Bicarbonate [Moles/volume] in Arterial blood","unit": "mmol/L","category": "laboratory"},
    "fio2":                 {"code": "3150-0",  "display": "Inhaled oxygen concentration",     "unit": "%",           "category": "laboratory"},

    # ── Microbiology ─────────────────────────────────────────────────────────
    "blood_culture":        {"code": "600-7",   "display": "Bacteria identified in Blood by Culture","unit": None,    "category": "laboratory"},
    "urine_culture":        {"code": "630-4",   "display": "Bacteria identified in Urine by Culture","unit": None,   "category": "laboratory"},

    # ── Thyroid ──────────────────────────────────────────────────────────────
    "tsh":                  {"code": "3016-3",  "display": "Thyrotropin [Units/volume] in Serum","unit": "mU/L",     "category": "laboratory"},
    "free_t4":              {"code": "3024-7",  "display": "Thyroxine (T4) free [Mass/volume] in Serum","unit": "ng/dL","category": "laboratory"},
}

# ── Reverse lookup: LOINC code → parameter name ───────────────────────────────
CODE_TO_PARAM: Dict[str, str] = {v["code"]: k for k, v in LOINC_REGISTRY.items()}


class LOINCMapper:
    """
    LOINC code lookup, validation, and mapping utilities.
    Used by FHIR normalizer to ensure all observations have valid LOINC codes.
    """

    @staticmethod
    def get(parameter: str) -> Optional[Dict]:
        """Get LOINC entry for a parameter name."""
        return LOINC_REGISTRY.get(parameter)

    @staticmethod
    def get_code(parameter: str) -> Optional[str]:
        """Get LOINC code string for a parameter."""
        entry = LOINC_REGISTRY.get(parameter)
        return entry["code"] if entry else None

    @staticmethod
    def get_unit(parameter: str) -> Optional[str]:
        """Get canonical unit for a parameter."""
        entry = LOINC_REGISTRY.get(parameter)
        return entry["unit"] if entry else None

    @staticmethod
    def from_code(loinc_code: str) -> Optional[str]:
        """Reverse lookup: LOINC code → parameter name."""
        return CODE_TO_PARAM.get(loinc_code)

    @staticmethod
    def is_valid_code(loinc_code: str) -> bool:
        """Check if a LOINC code is in our registry."""
        return loinc_code in CODE_TO_PARAM

    @staticmethod
    def is_vital_sign(parameter: str) -> bool:
        entry = LOINC_REGISTRY.get(parameter)
        return entry.get("category") == "vital-signs" if entry else False

    @staticmethod
    def is_laboratory(parameter: str) -> bool:
        entry = LOINC_REGISTRY.get(parameter)
        return entry.get("category") == "laboratory" if entry else False

    @staticmethod
    def normalize_unit(parameter: str, input_unit: str) -> str:
        """
        Normalize unit to UCUM standard.
        Hospitals use many unit formats: bpm, /min, beats/min → /min
        """
        UNIT_ALIASES = {
            "bpm": "/min", "beats/min": "/min", "breaths/min": "/min",
            "°C": "Cel", "°F": "[degF]", "degC": "Cel",
            "mmHg": "mm[Hg]", "mmhg": "mm[Hg]",
            "K/uL": "10*3/uL", "thou/uL": "10*3/uL",
            "M/uL": "10*6/uL", "mil/uL": "10*6/uL",
            "mg/dl": "mg/dL", "g/dl": "g/dL", "mEq/L": "mmol/L",
            "IU/L": "U/L", "u/L": "U/L",
        }
        normalized = UNIT_ALIASES.get(input_unit, input_unit)
        canonical = LOINCMapper.get_unit(parameter)
        return canonical or normalized

    @staticmethod
    def get_vital_signs() -> List[str]:
        return [k for k, v in LOINC_REGISTRY.items() if v["category"] == "vital-signs"]

    @staticmethod
    def get_laboratory_tests() -> List[str]:
        return [k for k, v in LOINC_REGISTRY.items() if v["category"] == "laboratory"]

    @staticmethod
    def search(query: str) -> List[Tuple[str, Dict]]:
        """Search registry by parameter name or display text."""
        q = query.lower()
        return [
            (name, entry)
            for name, entry in LOINC_REGISTRY.items()
            if q in name.lower() or q in entry["display"].lower()
        ]

    @staticmethod
    def build_fhir_coding(parameter: str) -> Optional[Dict]:
        """Build a FHIR CodeableConcept coding element for a parameter."""
        entry = LOINC_REGISTRY.get(parameter)
        if not entry:
            return None
        return {
            "coding": [{
                "system": "http://loinc.org",
                "code": entry["code"],
                "display": entry["display"],
            }],
            "text": entry["display"],
        }
