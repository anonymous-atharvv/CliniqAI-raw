#!/usr/bin/env python3
"""
Synthetic Patient Data Generator for CliniQAI Development

Uses Synthea-style synthetic data generation.
NEVER use real patient data in development or testing.
NEVER commit this script with real data artifacts.

Generates:
- Realistic ICU patients with comorbidities
- Vital sign time series (1Hz for 72 hours)
- Medications with interactions
- Lab results trending appropriately
- Clinical notes (generated text, not real)
- FHIR R4 bundles

Run: python scripts/seed_synthea.py --patients 100 --icu 20
"""

import uuid
import json
import random
import argparse
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple
import sys
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Synthetic Patient Demographics
# ─────────────────────────────────────────────

FIRST_NAMES_M = ["Arjun", "Rajesh", "Vikram", "Suresh", "Mohan", "Sanjay", "Deepak",
                  "Anil", "Ramesh", "Kumar", "James", "Robert", "Michael", "David", "John"]
FIRST_NAMES_F = ["Priya", "Sunita", "Kavya", "Meena", "Asha", "Deepa", "Rekha",
                  "Suman", "Lakshmi", "Geeta", "Mary", "Patricia", "Jennifer", "Linda", "Sarah"]
LAST_NAMES =    ["Sharma", "Patel", "Singh", "Kumar", "Verma", "Gupta", "Joshi",
                  "Agarwal", "Yadav", "Mishra", "Smith", "Johnson", "Williams", "Brown", "Jones"]

STATES = ["UP", "MH", "DL", "KA", "TN", "GJ", "RJ", "WB", "MP", "AP"]
ZIP_PREFIXES = ["226", "400", "110", "560", "600", "380", "302", "700", "462", "500"]

# ─────────────────────────────────────────────
# Clinical Scenario Templates
# ─────────────────────────────────────────────

ICU_SCENARIOS = [
    {
        "name": "Septic Shock",
        "icd10": "A41.9",
        "drg": "872",
        "vital_profile": {
            "heart_rate": {"base": 118, "variance": 12, "trend": "up"},
            "bp_systolic": {"base": 88,  "variance": 10, "trend": "down"},
            "spo2_pulse_ox": {"base": 91, "variance": 2,  "trend": "stable"},
            "respiratory_rate": {"base": 26, "variance": 4, "trend": "up"},
            "temperature": {"base": 38.8, "variance": 0.4, "trend": "up"},
        },
        "news2_range": (6, 9),
        "sepsis_probability": (0.65, 0.85),
        "medications": ["Norepinephrine", "Vancomycin", "Piperacillin/Tazobactam",
                        "Meropenem", "Normal Saline 0.9%"],
        "labs": {"WBC": (14.5, 22.0), "Lactate": (2.8, 6.5), "Procalcitonin": (8.2, 45.0),
                 "Creatinine": (1.8, 4.2), "Glucose": (140, 220)},
    },
    {
        "name": "ARDS",
        "icd10": "J80",
        "drg": "189",
        "vital_profile": {
            "heart_rate": {"base": 105, "variance": 15, "trend": "stable"},
            "bp_systolic": {"base": 118, "variance": 12, "trend": "stable"},
            "spo2_pulse_ox": {"base": 87,  "variance": 3, "trend": "down"},
            "respiratory_rate": {"base": 32, "variance": 5, "trend": "up"},
            "temperature": {"base": 38.2, "variance": 0.5, "trend": "stable"},
        },
        "news2_range": (7, 10),
        "sepsis_probability": (0.30, 0.55),
        "medications": ["Propofol", "Fentanyl", "Midazolam", "Vecuronium",
                        "Heparin 5000 units SQ", "Famotidine"],
        "labs": {"PaO2": (52, 72), "WBC": (12, 18), "Lactate": (1.5, 3.5)},
    },
    {
        "name": "Acute Decompensated Heart Failure",
        "icd10": "I50.9",
        "drg": "291",
        "vital_profile": {
            "heart_rate": {"base": 98,  "variance": 10, "trend": "stable"},
            "bp_systolic": {"base": 148, "variance": 18, "trend": "down"},
            "spo2_pulse_ox": {"base": 91,  "variance": 2, "trend": "improving"},
            "respiratory_rate": {"base": 22, "variance": 4, "trend": "down"},
            "temperature": {"base": 37.1, "variance": 0.3, "trend": "stable"},
        },
        "news2_range": (4, 7),
        "sepsis_probability": (0.05, 0.15),
        "medications": ["Furosemide IV", "Metoprolol Succinate", "Lisinopril",
                        "Spironolactone", "Oxygen 2L/min NC"],
        "labs": {"BNP": (850, 3200), "Creatinine": (1.4, 2.8), "Sodium": (128, 134),
                 "Potassium": (3.2, 5.8)},
    },
    {
        "name": "Community-Acquired Pneumonia",
        "icd10": "J18.9",
        "drg": "194",
        "vital_profile": {
            "heart_rate": {"base": 104, "variance": 10, "trend": "down"},
            "bp_systolic": {"base": 118, "variance": 12, "trend": "stable"},
            "spo2_pulse_ox": {"base": 92,  "variance": 2, "trend": "improving"},
            "respiratory_rate": {"base": 24, "variance": 4, "trend": "down"},
            "temperature": {"base": 38.5, "variance": 0.5, "trend": "down"},
        },
        "news2_range": (3, 6),
        "sepsis_probability": (0.15, 0.35),
        "medications": ["Ceftriaxone 1g IV", "Azithromycin 500mg PO",
                        "Heparin 5000 SQ", "Acetaminophen 650mg PO Q6H"],
        "labs": {"WBC": (12.5, 18.0), "CRP": (85, 240), "Procalcitonin": (0.8, 4.5)},
    },
    {
        "name": "Post-Operative Monitoring",
        "icd10": "Z96.641",
        "drg": "470",
        "vital_profile": {
            "heart_rate": {"base": 82,  "variance": 8,  "trend": "stable"},
            "bp_systolic": {"base": 128, "variance": 12, "trend": "stable"},
            "spo2_pulse_ox": {"base": 96,  "variance": 1, "trend": "stable"},
            "respiratory_rate": {"base": 16, "variance": 3, "trend": "stable"},
            "temperature": {"base": 36.8, "variance": 0.3, "trend": "stable"},
        },
        "news2_range": (0, 3),
        "sepsis_probability": (0.02, 0.10),
        "medications": ["Enoxaparin 40mg SQ daily", "Oxycodone/Acetaminophen",
                        "Ketorolac 15mg IV Q6H", "Ondansetron 4mg IV Q8H PRN"],
        "labs": {"Hemoglobin": (9.5, 11.5), "Creatinine": (0.8, 1.4)},
    },
]

WARD_SCENARIOS = [
    {"name": "COPD Exacerbation",       "icd10": "J44.1", "drg": "177",
     "news2_range": (2, 5), "sepsis_probability": (0.05, 0.20)},
    {"name": "Urinary Tract Infection",  "icd10": "N39.0", "drg": "689",
     "news2_range": (1, 4), "sepsis_probability": (0.08, 0.25)},
    {"name": "GI Bleed",                 "icd10": "K92.1", "drg": "377",
     "news2_range": (2, 5), "sepsis_probability": (0.03, 0.12)},
    {"name": "Cellulitis",               "icd10": "L03.90", "drg": "602",
     "news2_range": (1, 3), "sepsis_probability": (0.04, 0.15)},
    {"name": "Acute Kidney Injury",      "icd10": "N17.9", "drg": "682",
     "news2_range": (2, 5), "sepsis_probability": (0.10, 0.25)},
]


class SyntheticPatientGenerator:
    """
    Generates realistic synthetic patient data for development testing.
    
    All data is mathematically generated — no real patient information.
    The vital sign patterns follow clinical physiology principles.
    """
    
    def __init__(self, hospital_id: str = "hospital_dev_001", seed: int = 42):
        random.seed(seed)
        self.hospital_id = hospital_id
        self.generated_patients = []
        self.generated_vitals = []
    
    def generate_patient(
        self,
        unit_type: str = "icu",
        scenario: Optional[Dict] = None,
    ) -> Dict:
        """Generate a single synthetic patient with full clinical context."""
        
        patient_id = str(uuid.uuid4())
        deident_id = str(uuid.uuid4())
        encounter_id = str(uuid.uuid4())
        
        # Demographics
        gender = random.choice(["M", "F"])
        first_names = FIRST_NAMES_M if gender == "M" else FIRST_NAMES_F
        first_name = random.choice(first_names)
        last_name = random.choice(LAST_NAMES)
        
        age = random.randint(45, 85)
        birth_year = datetime.now().year - age
        
        state_idx = random.randint(0, len(STATES) - 1)
        
        # Select clinical scenario
        if scenario is None:
            if unit_type == "icu":
                scenario = random.choice(ICU_SCENARIOS)
            else:
                scenario = random.choice(WARD_SCENARIOS)
        
        # Admission timing
        admission_hours_ago = random.randint(4, 120)
        admission_time = datetime.now(timezone.utc) - timedelta(hours=admission_hours_ago)
        
        # Comorbidities
        possible_comorbidities = [
            "Type 2 Diabetes", "Hypertension", "Chronic Kidney Disease Stage 3",
            "Coronary Artery Disease", "COPD", "Heart Failure EF 35%",
            "Atrial Fibrillation", "Obesity BMI 34", "Hypothyroidism",
            "Prior CVA", "Peripheral Vascular Disease"
        ]
        comorbidities = random.sample(possible_comorbidities, k=random.randint(1, 4))
        charlson_score = len(comorbidities) + random.randint(0, 3)
        
        # Allergies
        possible_allergies = ["Penicillin", "Sulfa", "Aspirin", "Codeine", "Latex", "NKDA"]
        allergy = random.choice(possible_allergies)
        
        patient = {
            "patient_id": patient_id,
            "deidentified_id": deident_id,
            "encounter_id": encounter_id,
            "hospital_id": self.hospital_id,
            
            # PHI (in dev only — never stored like this in production)
            "first_name": first_name,
            "last_name": last_name,
            "gender": gender,
            "birth_year": birth_year,
            "age": age,
            "state": STATES[state_idx],
            "zip_prefix": ZIP_PREFIXES[state_idx],
            
            # Clinical
            "unit_type": unit_type,
            "ward_code": f"ICU-B" if unit_type == "icu" else "WARD-C",
            "bed_id": f"{'B' if unit_type == 'icu' else 'W'}-{random.randint(1, 28):02d}",
            "scenario_name": scenario["name"],
            "primary_icd10": scenario["icd10"],
            "drg_code": scenario.get("drg", "999"),
            "admission_time": admission_time.isoformat(),
            "chief_complaint": f"Admitted for {scenario['name'].lower()}",
            
            # Risk indicators
            "news2_score": random.randint(*scenario["news2_range"]),
            "sepsis_probability": round(random.uniform(*scenario["sepsis_probability"]), 3),
            "risk_level": self._compute_risk_level(scenario["news2_range"]),
            
            # Clinical context
            "comorbidities": comorbidities,
            "charlson_score": charlson_score,
            "allergies": [allergy] if allergy != "NKDA" else [],
            "medications": scenario.get("medications", []),
            "current_labs": self._generate_labs(scenario.get("labs", {})),
            
            # Weight/renal function
            "weight_kg": random.uniform(55, 105),
            "height_cm": random.uniform(155, 185),
            "gfr": random.uniform(25, 90),
        }
        
        return patient
    
    def generate_vitals_stream(
        self,
        patient: Dict,
        hours: int = 6,
        frequency_seconds: int = 60,  # 1 reading per minute (1Hz = too many rows for dev)
    ) -> List[Dict]:
        """
        Generate realistic vital sign time series for a patient.
        
        In production: actual 1Hz per ICU device.
        For development: 1/min is sufficient for testing AI pipeline.
        """
        vitals = []
        scenario_name = patient["scenario_name"]
        
        # Find matching scenario
        scenario = next(
            (s for s in ICU_SCENARIOS + WARD_SCENARIOS if s["name"] == scenario_name),
            ICU_SCENARIOS[0]
        )
        
        vital_profile = scenario.get("vital_profile", {
            "heart_rate": {"base": 82, "variance": 8, "trend": "stable"},
            "spo2_pulse_ox": {"base": 96, "variance": 1.5, "trend": "stable"},
            "bp_systolic": {"base": 122, "variance": 12, "trend": "stable"},
            "respiratory_rate": {"base": 16, "variance": 3, "trend": "stable"},
            "temperature": {"base": 37.0, "variance": 0.3, "trend": "stable"},
        })
        
        n_readings = (hours * 3600) // frequency_seconds
        start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        # Current values (drift from base over time)
        current_values = {param: profile["base"] for param, profile in vital_profile.items()}
        
        for i in range(n_readings):
            timestamp = start_time + timedelta(seconds=i * frequency_seconds)
            progress = i / n_readings  # 0.0 to 1.0
            
            for param, profile in vital_profile.items():
                base = profile["base"]
                variance = profile["variance"]
                trend = profile["trend"]
                
                # Apply trend
                if trend == "up":
                    drift = base * 0.15 * progress
                elif trend == "down":
                    drift = -base * 0.12 * progress
                elif trend == "improving":
                    drift = base * 0.10 * progress  # Improving = moving toward normal
                else:
                    drift = 0
                
                # Random walk component
                noise = random.gauss(0, variance * 0.3)
                current_values[param] = base + drift + noise
                
                # Physiological clamps
                clamps = {
                    "heart_rate":       (30, 200),
                    "spo2_pulse_ox":    (70, 100),
                    "bp_systolic":      (50, 250),
                    "respiratory_rate": (4, 50),
                    "temperature":      (33.0, 42.0),
                }
                if param in clamps:
                    lo, hi = clamps[param]
                    current_values[param] = max(lo, min(hi, current_values[param]))
                
                # Determine if critical
                critical_thresholds = {
                    "heart_rate":       {"low": 40, "high": 150},
                    "spo2_pulse_ox":    {"low": 85, "high": None},
                    "bp_systolic":      {"low": 80, "high": None},
                    "respiratory_rate": {"low": 6, "high": 35},
                    "temperature":      {"low": 34.0, "high": 39.5},
                }
                thresh = critical_thresholds.get(param, {})
                value = round(current_values[param], 2)
                is_crit_low = thresh.get("low") and value < thresh["low"]
                is_crit_high = thresh.get("high") and value > thresh["high"]
                
                vitals.append({
                    "time": timestamp.isoformat(),
                    "patient_deident_id": patient["deidentified_id"],
                    "encounter_id": patient["encounter_id"],
                    "parameter": param,
                    "value": value,
                    "unit": self._get_unit(param),
                    "is_artifact": False,
                    "quality_score": random.uniform(0.90, 1.00),
                    "device_id": f"monitor-{patient['bed_id']}",
                    "source_system": "icu_monitor",
                    "is_critical_low": bool(is_crit_low),
                    "is_critical_high": bool(is_crit_high),
                })
        
        return vitals
    
    def generate_fhir_bundle(self, patient: Dict) -> Dict:
        """
        Generate FHIR R4 Bundle for a synthetic patient.
        Ready for ingestion into the FHIR normalization engine.
        """
        patient_resource = {
            "resourceType": "Patient",
            "id": patient["patient_id"],
            "identifier": [
                {
                    "use": "official",
                    "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v2-0203", "code": "MR"}]},
                    "system": f"urn:oid:{self.hospital_id}.mrn",
                    "value": f"MRN{patient['patient_id'][:8].upper()}"
                }
            ],
            "name": [{"use": "official", "family": patient["last_name"], "given": [patient["first_name"]]}],
            "gender": "male" if patient["gender"] == "M" else "female",
            "birthDate": f"{patient['birth_year']}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "address": [{"state": patient["state"], "postalCode": f"{patient['zip_prefix']}001", "country": "IN"}],
        }
        
        encounter_resource = {
            "resourceType": "Encounter",
            "id": patient["encounter_id"],
            "status": "in-progress",
            "class": {"code": "IMP", "display": "inpatient encounter"},
            "subject": {"reference": f"Patient/{patient['patient_id']}"},
            "period": {"start": patient["admission_time"]},
            "reasonCode": [{"coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": patient["primary_icd10"]}]}],
            "location": [{"location": {"display": patient["ward_code"]}, "physicalType": {"text": patient["unit_type"]}}],
        }
        
        # Medication resources
        medication_resources = []
        for med_name in patient.get("medications", []):
            medication_resources.append({
                "resourceType": "MedicationRequest",
                "id": str(uuid.uuid4()),
                "status": "active",
                "intent": "order",
                "medicationCodeableConcept": {"text": med_name},
                "subject": {"reference": f"Patient/{patient['patient_id']}"},
                "encounter": {"reference": f"Encounter/{patient['encounter_id']}"},
                "authoredOn": patient["admission_time"],
            })
        
        # Condition resources
        condition_resources = [{
            "resourceType": "Condition",
            "id": str(uuid.uuid4()),
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-category", "code": "encounter-diagnosis"}]}],
            "code": {"coding": [{"system": "http://hl7.org/fhir/sid/icd-10", "code": patient["primary_icd10"]}], "text": patient["scenario_name"]},
            "subject": {"reference": f"Patient/{patient['patient_id']}"},
        }]
        
        for comorbidity in patient.get("comorbidities", []):
            condition_resources.append({
                "resourceType": "Condition",
                "id": str(uuid.uuid4()),
                "clinicalStatus": {"coding": [{"code": "active"}]},
                "category": [{"coding": [{"code": "problem-list-item"}]}],
                "code": {"text": comorbidity},
                "subject": {"reference": f"Patient/{patient['patient_id']}"},
            })
        
        bundle = {
            "resourceType": "Bundle",
            "id": str(uuid.uuid4()),
            "type": "collection",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "entry": [
                {"resource": patient_resource},
                {"resource": encounter_resource},
                *[{"resource": r} for r in medication_resources],
                *[{"resource": r} for r in condition_resources],
            ]
        }
        
        return bundle
    
    def _generate_labs(self, lab_ranges: Dict) -> Dict:
        result = {}
        for test, (lo, hi) in lab_ranges.items():
            result[test] = round(random.uniform(lo, hi), 2)
        return result
    
    def _get_unit(self, param: str) -> str:
        units = {
            "heart_rate": "/min", "spo2_pulse_ox": "%",
            "bp_systolic": "mmHg", "bp_diastolic": "mmHg", "bp_mean": "mmHg",
            "respiratory_rate": "/min", "temperature": "Cel",
            "gcs_total": "{score}",
        }
        return units.get(param, "")
    
    def _compute_risk_level(self, news2_range: Tuple[int, int]) -> str:
        max_news2 = news2_range[1]
        if max_news2 >= 7:   return "CRITICAL"
        elif max_news2 >= 5: return "HIGH"
        elif max_news2 >= 3: return "MEDIUM"
        else:                return "LOW"
    
    def generate_dataset(
        self,
        n_icu_patients: int = 20,
        n_ward_patients: int = 50,
        vitals_hours: int = 6,
    ) -> Dict:
        """Generate a complete synthetic dataset."""
        logger.info(f"Generating {n_icu_patients} ICU + {n_ward_patients} ward patients...")
        
        all_patients = []
        all_vitals = []
        all_bundles = []
        
        # ICU patients
        for i in range(n_icu_patients):
            patient = self.generate_patient(unit_type="icu")
            vitals = self.generate_vitals_stream(patient, hours=vitals_hours)
            bundle = self.generate_fhir_bundle(patient)
            
            all_patients.append(patient)
            all_vitals.extend(vitals)
            all_bundles.append(bundle)
            
            if (i + 1) % 5 == 0:
                logger.info(f"  ICU: {i+1}/{n_icu_patients} patients")
        
        # Ward patients (no vitals stream — monitored less frequently)
        for i in range(n_ward_patients):
            patient = self.generate_patient(unit_type="ward")
            bundle = self.generate_fhir_bundle(patient)
            
            all_patients.append(patient)
            all_bundles.append(bundle)
        
        logger.info(f"Generated {len(all_patients)} patients, {len(all_vitals)} vital readings")
        
        return {
            "patients": all_patients,
            "vitals": all_vitals,
            "fhir_bundles": all_bundles,
            "summary": {
                "total_patients": len(all_patients),
                "icu_patients": n_icu_patients,
                "ward_patients": n_ward_patients,
                "vital_readings": len(all_vitals),
                "critical_patients": sum(1 for p in all_patients if p["risk_level"] == "CRITICAL"),
                "high_risk_patients": sum(1 for p in all_patients if p["risk_level"] == "HIGH"),
            }
        }


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic patient data for CliniQAI")
    parser.add_argument("--patients", type=int, default=70, help="Total patients to generate")
    parser.add_argument("--icu", type=int, default=20, help="ICU patients (subset of --patients)")
    parser.add_argument("--hours", type=int, default=6, help="Hours of vital sign history per ICU patient")
    parser.add_argument("--output", type=str, default="dev_data", help="Output directory")
    parser.add_argument("--format", choices=["json", "fhir", "sql"], default="json", help="Output format")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()
    
    ward_patients = args.patients - args.icu
    if ward_patients < 0:
        logger.error("--icu cannot exceed --patients")
        sys.exit(1)
    
    logger.info("=" * 60)
    logger.info("CliniQAI Synthetic Data Generator")
    logger.info("IMPORTANT: This generates SYNTHETIC data only.")
    logger.info("NEVER use real patient data in development.")
    logger.info("=" * 60)
    
    generator = SyntheticPatientGenerator(seed=args.seed)
    dataset = generator.generate_dataset(
        n_icu_patients=args.icu,
        n_ward_patients=ward_patients,
        vitals_hours=args.hours,
    )
    
    # Create output directory
    os.makedirs(args.output, exist_ok=True)
    
    # Write patients
    patients_file = os.path.join(args.output, "patients.json")
    with open(patients_file, "w") as f:
        json.dump(dataset["patients"], f, indent=2, default=str)
    logger.info(f"Written: {patients_file}")
    
    # Write vitals
    vitals_file = os.path.join(args.output, "vitals.json")
    with open(vitals_file, "w") as f:
        json.dump(dataset["vitals"], f, default=str)
    logger.info(f"Written: {vitals_file} ({len(dataset['vitals'])} readings)")
    
    # Write FHIR bundles
    fhir_file = os.path.join(args.output, "fhir_bundles.json")
    with open(fhir_file, "w") as f:
        json.dump(dataset["fhir_bundles"], f, indent=2, default=str)
    logger.info(f"Written: {fhir_file}")
    
    # Write summary
    summary_file = os.path.join(args.output, "summary.json")
    with open(summary_file, "w") as f:
        json.dump(dataset["summary"], f, indent=2)
    
    logger.info("\n" + "=" * 60)
    logger.info("DATASET SUMMARY:")
    for k, v in dataset["summary"].items():
        logger.info(f"  {k}: {v}")
    logger.info("=" * 60)
    logger.info("\nNext steps:")
    logger.info("  1. docker-compose up -d  (start all services)")
    logger.info("  2. python scripts/migrate_db.py  (run migrations)")
    logger.info("  3. python scripts/load_dev_data.py --dir dev_data  (load this data)")
    logger.info("  4. uvicorn backend.main:app --reload  (start API)")
    logger.info("\nDev credentials: admin / dev-password-change-in-production")


if __name__ == "__main__":
    main()
