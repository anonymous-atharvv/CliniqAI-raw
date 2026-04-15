"""
FHIR R4 Normalization Engine
Converts HL7 v2, CDA, and raw EHR data into FHIR R4 resources.

LOINC codes are non-negotiable — hospitals reject integrations that don't use them.
SMART on FHIR OAuth2 handles Epic/Cerner third-party app authentication.
"""

import uuid
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# LOINC Code Registry (Non-Negotiable)
# Source: LOINC.org, validated against HL7 FHIR R4
# ─────────────────────────────────────────────

LOINC_CODES = {
    # Vital Signs
    "heart_rate":           {"code": "8867-4",  "display": "Heart rate",                   "unit": "/min"},
    "spo2_arterial":        {"code": "2708-6",  "display": "Oxygen saturation in Arterial blood", "unit": "%"},
    "spo2_pulse_ox":        {"code": "59408-5", "display": "Oxygen saturation by Pulse oximetry", "unit": "%"},
    "bp_systolic":          {"code": "8480-6",  "display": "Systolic blood pressure",       "unit": "mm[Hg]"},
    "bp_diastolic":         {"code": "8462-4",  "display": "Diastolic blood pressure",      "unit": "mm[Hg]"},
    "bp_mean":              {"code": "8478-0",  "display": "Mean blood pressure",            "unit": "mm[Hg]"},
    "temperature":          {"code": "8310-5",  "display": "Body temperature",               "unit": "Cel"},
    "respiratory_rate":     {"code": "9279-1",  "display": "Respiratory rate",               "unit": "/min"},
    "gcs_total":            {"code": "9269-2",  "display": "Glasgow coma score total",       "unit": "{score}"},
    "weight":               {"code": "29463-7", "display": "Body weight",                    "unit": "kg"},
    "height":               {"code": "8302-2",  "display": "Body height",                    "unit": "cm"},
    "bmi":                  {"code": "39156-5", "display": "Body mass index",                "unit": "kg/m2"},
    
    # Laboratory (Common ICU)
    "lactate":              {"code": "2519-7",  "display": "Lactate [Moles/volume] in Blood", "unit": "mmol/L"},
    "creatinine":           {"code": "2160-0",  "display": "Creatinine [Mass/volume] in Serum", "unit": "mg/dL"},
    "wbc":                  {"code": "6690-2",  "display": "Leukocytes [#/volume] in Blood", "unit": "10*3/uL"},
    "hemoglobin":           {"code": "718-7",   "display": "Hemoglobin [Mass/volume] in Blood", "unit": "g/dL"},
    "platelets":            {"code": "777-3",   "display": "Platelets [#/volume] in Blood",  "unit": "10*3/uL"},
    "sodium":               {"code": "2951-2",  "display": "Sodium [Moles/volume] in Serum", "unit": "mmol/L"},
    "potassium":            {"code": "2823-3",  "display": "Potassium [Moles/volume] in Serum", "unit": "mmol/L"},
    "glucose":              {"code": "2345-7",  "display": "Glucose [Mass/volume] in Serum", "unit": "mg/dL"},
    "bilirubin_total":      {"code": "1975-2",  "display": "Bilirubin.total [Mass/volume] in Serum", "unit": "mg/dL"},
    "procalcitonin":        {"code": "75241-0", "display": "Procalcitonin [Mass/volume] in Serum", "unit": "ng/mL"},
    "crp":                  {"code": "1988-5",  "display": "C reactive protein [Mass/volume] in Serum", "unit": "mg/L"},
}

# Clinical Reference Ranges for Validity Scoring
CLINICAL_RANGES = {
    "heart_rate":        {"min": 20,   "max": 300,  "critical_low": 40,  "critical_high": 180},
    "spo2_pulse_ox":     {"min": 50,   "max": 100,  "critical_low": 85,  "critical_high": None},
    "bp_systolic":       {"min": 50,   "max": 300,  "critical_low": 70,  "critical_high": 220},
    "bp_diastolic":      {"min": 20,   "max": 200,  "critical_low": 40,  "critical_high": 140},
    "temperature":       {"min": 25.0, "max": 45.0, "critical_low": 32.0, "critical_high": 41.0},
    "respiratory_rate":  {"min": 4,    "max": 60,   "critical_low": 6,   "critical_high": 40},
    "glucose":           {"min": 20,   "max": 800,  "critical_low": 40,  "critical_high": 600},
}


class FHIRResourceType(str, Enum):
    PATIENT = "Patient"
    OBSERVATION = "Observation"
    MEDICATION_REQUEST = "MedicationRequest"
    CONDITION = "Condition"
    DOCUMENT_REFERENCE = "DocumentReference"
    DIAGNOSTIC_REPORT = "DiagnosticReport"
    ALLERGY_INTOLERANCE = "AllergyIntolerance"
    PROCEDURE = "Procedure"
    ENCOUNTER = "Encounter"


@dataclass
class NormalizationResult:
    """Result from normalization — always check success before using resource."""
    success: bool
    resource_type: FHIRResourceType
    fhir_resource: Dict[str, Any]
    source_system: str
    source_id: str
    normalized_at: str
    quality_score: float
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    @property
    def is_high_quality(self) -> bool:
        return self.quality_score >= 0.60


class FHIRNormalizer:
    """
    Production FHIR R4 Normalization Engine.
    
    Converts: HL7 v2 messages, CDA documents, raw EHR data → FHIR R4 resources.
    Validates LOINC codes, clinical ranges, required fields.
    
    Thread-safe. Stateless. Can be horizontally scaled.
    """
    
    def __init__(self, hospital_id: str, hospital_system: str):
        self.hospital_id = hospital_id
        self.hospital_system = hospital_system  # epic|cerner|meditech
        self.base_url = f"https://fhir.cliniqai.com/{hospital_id}"
    
    def normalize_patient(self, raw_data: Dict[str, Any], source: str = "EHR") -> NormalizationResult:
        """
        Convert raw patient demographics to FHIR R4 Patient resource.
        Required fields: identifier (MRN), name, birthDate.
        """
        warnings = []
        errors = []
        
        # Build required identifiers
        identifiers = []
        
        if mrn := raw_data.get("mrn"):
            identifiers.append({
                "use": "official",
                "type": {
                    "coding": [{
                        "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                        "code": "MR",
                        "display": "Medical Record Number"
                    }]
                },
                "system": f"urn:oid:{self.hospital_id}.mrn",
                "value": str(mrn)
            })
        else:
            errors.append("Missing required field: MRN")
        
        # Name normalization
        names = []
        if last_name := raw_data.get("last_name"):
            name_entry = {
                "use": "official",
                "family": last_name.upper().strip(),
                "given": []
            }
            if first_name := raw_data.get("first_name"):
                name_entry["given"].append(first_name.strip())
            if middle_name := raw_data.get("middle_name"):
                name_entry["given"].append(middle_name.strip())
            names.append(name_entry)
        else:
            errors.append("Missing required field: last_name")
        
        # Date of birth
        birth_date = None
        if dob := raw_data.get("date_of_birth"):
            try:
                if isinstance(dob, str):
                    # Handle multiple date formats from different EHR systems
                    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%d/%m/%Y"]:
                        try:
                            parsed = datetime.strptime(dob, fmt)
                            birth_date = parsed.strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue
                if not birth_date:
                    errors.append(f"Cannot parse date_of_birth: {dob}")
            except Exception as e:
                errors.append(f"Date parsing error: {e}")
        else:
            errors.append("Missing required field: date_of_birth")
        
        # Gender mapping (HL7 → FHIR)
        gender_map = {
            "M": "male", "F": "female", "U": "unknown",
            "MALE": "male", "FEMALE": "female", "OTHER": "other",
            "1": "male", "2": "female",  # HL7 v2 codes
        }
        gender = gender_map.get(
            str(raw_data.get("gender", "")).upper(), "unknown"
        )
        if gender == "unknown":
            warnings.append("Gender unknown or not mapped")
        
        # Contact information (optional but tracked for quality score)
        telecom = []
        if phone := raw_data.get("phone"):
            telecom.append({"system": "phone", "value": phone, "use": "home"})
        if email := raw_data.get("email"):
            telecom.append({"system": "email", "value": email})
        
        # Address
        address = []
        if raw_data.get("address_line1"):
            address.append({
                "use": "home",
                "line": [raw_data.get("address_line1"), raw_data.get("address_line2", "")],
                "city": raw_data.get("city", ""),
                "state": raw_data.get("state", ""),
                "postalCode": raw_data.get("zip", ""),
                "country": raw_data.get("country", "US"),
            })
        
        # Build FHIR R4 Patient resource
        patient_resource = {
            "resourceType": "Patient",
            "id": str(uuid.uuid4()),
            "meta": {
                "versionId": "1",
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
                "source": f"urn:{self.hospital_id}:{source}",
                "tag": [{
                    "system": "https://cliniqai.com/tags",
                    "code": "hospital-ingested"
                }]
            },
            "identifier": identifiers,
            "active": True,
            "name": names,
            "gender": gender,
            "birthDate": birth_date,
            "telecom": telecom if telecom else [],
            "address": address if address else [],
        }
        
        # Add race/ethnicity if present (for bias monitoring)
        if ethnicity := raw_data.get("ethnicity"):
            patient_resource["extension"] = [{
                "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
                "extension": [{"url": "text", "valueString": ethnicity}]
            }]
        
        quality_score = self._calculate_patient_quality(patient_resource, errors, warnings)
        
        return NormalizationResult(
            success=len(errors) == 0,
            resource_type=FHIRResourceType.PATIENT,
            fhir_resource=patient_resource,
            source_system=source,
            source_id=raw_data.get("mrn", "unknown"),
            normalized_at=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            warnings=warnings,
            errors=errors,
        )
    
    def normalize_vital_sign(
        self,
        patient_id: str,
        vital_type: str,
        value: float,
        unit: str,
        timestamp: datetime,
        device_id: Optional[str] = None,
        encounter_id: Optional[str] = None,
    ) -> NormalizationResult:
        """
        Convert ICU vital sign reading to FHIR R4 Observation with LOINC code.
        This is called at 1Hz per device — must be fast (<5ms).
        """
        warnings = []
        errors = []
        
        # Look up LOINC code
        loinc_entry = LOINC_CODES.get(vital_type)
        if not loinc_entry:
            errors.append(f"Unknown vital type: {vital_type}. Not in LOINC registry.")
        
        # Validate value against clinical range
        clinical_range = CLINICAL_RANGES.get(vital_type)
        status = "final"
        interpretation_code = None
        
        if clinical_range and not errors:
            if value < clinical_range["min"] or value > clinical_range["max"]:
                warnings.append(
                    f"Value {value} outside physiologically possible range "
                    f"[{clinical_range['min']}, {clinical_range['max']}]"
                )
                status = "entered-in-error"
            
            critical_low = clinical_range.get("critical_low")
            critical_high = clinical_range.get("critical_high")
            
            if critical_low and value < critical_low:
                interpretation_code = "LL"  # Critical Low
            elif critical_high and value > critical_high:
                interpretation_code = "HH"  # Critical High
            elif critical_low and value < (critical_low + (critical_high or 0 - critical_low) * 0.1):
                interpretation_code = "L"   # Low
            elif critical_high and value > (critical_high * 0.9):
                interpretation_code = "H"   # High
        
        # Build Observation resource
        observation = {
            "resourceType": "Observation",
            "id": str(uuid.uuid4()),
            "meta": {
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
                "source": f"urn:{self.hospital_id}:icu-monitor",
            },
            "status": status,
            "category": [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                    "code": "vital-signs",
                    "display": "Vital Signs"
                }]
            }],
            "code": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": loinc_entry["code"] if loinc_entry else "UNKNOWN",
                    "display": loinc_entry["display"] if loinc_entry else vital_type,
                }],
                "text": loinc_entry["display"] if loinc_entry else vital_type,
            },
            "subject": {
                "reference": f"Patient/{patient_id}"
            },
            "effectiveDateTime": timestamp.isoformat(),
            "issued": datetime.now(timezone.utc).isoformat(),
            "valueQuantity": {
                "value": value,
                "unit": unit,
                "system": "http://unitsofmeasure.org",
                "code": loinc_entry["unit"] if loinc_entry else unit,
            },
        }
        
        # Add interpretation if abnormal
        if interpretation_code:
            observation["interpretation"] = [{
                "coding": [{
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                    "code": interpretation_code,
                }]
            }]
        
        # Add device reference for ICU monitoring
        if device_id:
            observation["device"] = {"reference": f"Device/{device_id}"}
        
        # Add encounter reference
        if encounter_id:
            observation["encounter"] = {"reference": f"Encounter/{encounter_id}"}
        
        # Add data lineage extension
        observation["extension"] = [{
            "url": "https://cliniqai.com/extensions/data-lineage",
            "extension": [
                {"url": "ingested_at", "valueDateTime": datetime.now(timezone.utc).isoformat()},
                {"url": "hospital_id", "valueString": self.hospital_id},
                {"url": "pipeline", "valueString": "icu_stream"},
            ]
        }]
        
        quality_score = 0.95 if not errors and not warnings else (0.70 if not errors else 0.20)
        
        return NormalizationResult(
            success=len(errors) == 0,
            resource_type=FHIRResourceType.OBSERVATION,
            fhir_resource=observation,
            source_system="icu_monitor",
            source_id=f"{device_id}_{timestamp.isoformat()}",
            normalized_at=datetime.now(timezone.utc).isoformat(),
            quality_score=quality_score,
            warnings=warnings,
            errors=errors,
        )
    
    def normalize_medication_request(
        self,
        patient_id: str,
        medication_name: str,
        rxnorm_code: Optional[str],
        dosage_value: float,
        dosage_unit: str,
        frequency: str,
        route: str,
        prescriber_id: str,
        encounter_id: str,
        status: str = "active",
    ) -> NormalizationResult:
        """Convert medication order to FHIR R4 MedicationRequest."""
        warnings = []
        errors = []
        
        if not rxnorm_code:
            warnings.append(f"No RxNorm code for {medication_name}. Interoperability limited.")
        
        medication_resource = {
            "resourceType": "MedicationRequest",
            "id": str(uuid.uuid4()),
            "meta": {
                "lastUpdated": datetime.now(timezone.utc).isoformat(),
            },
            "status": status,  # active|on-hold|cancelled|completed
            "intent": "order",
            "medicationCodeableConcept": {
                "coding": [{
                    "system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                    "code": rxnorm_code or "UNKNOWN",
                    "display": medication_name,
                }],
                "text": medication_name,
            },
            "subject": {"reference": f"Patient/{patient_id}"},
            "encounter": {"reference": f"Encounter/{encounter_id}"},
            "requester": {"reference": f"Practitioner/{prescriber_id}"},
            "authoredOn": datetime.now(timezone.utc).isoformat(),
            "dosageInstruction": [{
                "text": f"{dosage_value} {dosage_unit} {frequency} {route}",
                "timing": {
                    "code": {
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/v3-GTSAbbreviation",
                            "code": frequency,
                        }]
                    }
                },
                "route": {
                    "coding": [{
                        "system": "http://snomed.info/sct",
                        "display": route,
                    }]
                },
                "doseAndRate": [{
                    "type": {
                        "coding": [{
                            "system": "http://terminology.hl7.org/CodeSystem/dose-rate-type",
                            "code": "ordered"
                        }]
                    },
                    "doseQuantity": {
                        "value": dosage_value,
                        "unit": dosage_unit,
                        "system": "http://unitsofmeasure.org",
                    }
                }]
            }]
        }
        
        return NormalizationResult(
            success=True,
            resource_type=FHIRResourceType.MEDICATION_REQUEST,
            fhir_resource=medication_resource,
            source_system="EHR",
            source_id=f"{patient_id}_{medication_name}",
            normalized_at=datetime.now(timezone.utc).isoformat(),
            quality_score=0.90 if rxnorm_code else 0.70,
            warnings=warnings,
            errors=errors,
        )
    
    def _calculate_patient_quality(
        self,
        resource: Dict[str, Any],
        errors: List[str],
        warnings: List[str],
    ) -> float:
        """
        Calculate data quality score for a Patient resource.
        Completeness × 0.30 + Timeliness × 0.25 + Consistency × 0.25 + Validity × 0.20
        """
        if errors:
            return 0.20  # Errors = floor at 20%
        
        # Completeness: % of key fields populated
        key_fields = ["name", "birthDate", "gender", "identifier", "telecom", "address"]
        populated = sum(1 for f in key_fields if resource.get(f))
        completeness = populated / len(key_fields)
        
        # Timeliness: always 1.0 for new ingestion
        timeliness = 1.0
        
        # Consistency: warnings indicate inconsistencies
        consistency = max(0.0, 1.0 - (len(warnings) * 0.15))
        
        # Validity: pass/fail based on data format
        validity = 0.90 if not warnings else 0.70
        
        quality = (
            0.30 * completeness +
            0.25 * timeliness +
            0.25 * consistency +
            0.20 * validity
        )
        
        return round(min(1.0, max(0.0, quality)), 4)
    
    def validate_fhir_r4(self, resource: Dict[str, Any]) -> tuple[bool, List[str]]:
        """
        Basic FHIR R4 structural validation.
        For full validation, use HAPI FHIR validator server.
        """
        errors = []
        
        if "resourceType" not in resource:
            errors.append("Missing resourceType")
            return False, errors
        
        if "id" not in resource:
            errors.append("Missing resource id")
        
        resource_type = resource.get("resourceType")
        
        # Resource-specific required fields
        required_fields = {
            "Patient": ["identifier", "name"],
            "Observation": ["status", "code", "subject", "effectiveDateTime"],
            "MedicationRequest": ["status", "intent", "medicationCodeableConcept", "subject"],
            "Condition": ["clinicalStatus", "code", "subject"],
        }
        
        if resource_type in required_fields:
            for field in required_fields[resource_type]:
                if not resource.get(field):
                    errors.append(f"Missing required field for {resource_type}: {field}")
        
        return len(errors) == 0, errors
