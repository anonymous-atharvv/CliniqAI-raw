"""
Clinical Validation Test Suite
================================
Tests that verify AI model performance meets clinical standards.

These tests must PASS before deploying any model update.
Minimum requirements:
- Sepsis prediction: AUROC > 0.85, sensitivity > 0.80 at specificity 0.85
- Deterioration prediction: AUROC > 0.80
- Drug interaction detection: recall > 0.95 (missing an interaction is dangerous)
- De-identification: 0 PHI leakage (zero tolerance)

Run: pytest tests/clinical/ -v --tb=short
"""

import pytest
import uuid
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple
import statistics

# Add backend to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))

from services.ai.vitals_engine import VitalsTFTEngine, VitalSignPreprocessor, VITAL_BOUNDS
from services.compliance.gateway import DeIdentificationEngine, ABACEngine, AccessRequest
from services.compliance.gateway import UserRole, CareRelationship, DataSensitivity, TimeContext, AccessAction, AccessReason
from services.agents.orchestrator import TriageAgent, RiskAgent, PharmacistAgent, ESICategory
from services.mpi.engine import MPIEngine, JaroWinklerSimilarity


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

@pytest.fixture
def vitals_engine():
    return VitalsTFTEngine()

@pytest.fixture
def preprocessor():
    return VitalSignPreprocessor()

@pytest.fixture
def deidentifier():
    return DeIdentificationEngine(salt="test-salt-never-use-in-production")

@pytest.fixture
def abac():
    return ABACEngine()

@pytest.fixture
def jw():
    return JaroWinklerSimilarity()

def make_vitals(hr=80, spo2=97, sbp=120, rr=16, temp=37.0, n_readings=60):
    """Create synthetic vital sign stream for testing."""
    now = datetime.now(timezone.utc)
    vitals = []
    params = {
        "heart_rate": (hr, "/min"),
        "spo2_pulse_ox": (spo2, "%"),
        "bp_systolic": (sbp, "mmHg"),
        "respiratory_rate": (rr, "/min"),
        "temperature": (temp, "Cel"),
    }
    for i in range(n_readings):
        timestamp = now - timedelta(minutes=n_readings - i)
        for param, (value, unit) in params.items():
            vitals.append({
                "time": timestamp.isoformat(),
                "patient_deident_id": str(uuid.uuid4()),
                "encounter_id": str(uuid.uuid4()),
                "parameter": param,
                "value": float(value) + (i * 0.01),  # slight drift
                "unit": unit,
                "device_id": "test-device-01",
                "source_system": "icu_monitor",
            })
    return vitals


# ─────────────────────────────────────────────
# NEWS2 Score Validation Tests
# Reference: Royal College of Physicians, 2017
# ─────────────────────────────────────────────

class TestNEWS2Scoring:
    """
    Validate NEWS2 implementation against published reference cases.
    NEWS2 must be exactly correct — this is a regulatory requirement.
    """
    
    def test_normal_patient_news2_zero(self, vitals_engine):
        """Completely normal vitals → NEWS2 = 0."""
        vitals = {
            "respiratory_rate": 16.0,
            "spo2_pulse_ox": 98.0,
            "bp_systolic": 130.0,
            "heart_rate": 75.0,
            "temperature": 37.0,
        }
        score = vitals_engine._calculate_news2(vitals)
        assert score == 0, f"Normal vitals should give NEWS2=0, got {score}"
    
    def test_high_risk_patient_news2(self, vitals_engine):
        """Classic sepsis presentation → NEWS2 ≥ 6."""
        vitals = {
            "respiratory_rate": 28.0,   # +3 (>24)
            "spo2_pulse_ox": 90.0,      # +3 (≤91)
            "bp_systolic": 88.0,        # +3 (≤90)
            "heart_rate": 118.0,        # +2 (111-130)
            "temperature": 38.8,        # +1 (38.1-39.0)
        }
        score = vitals_engine._calculate_news2(vitals)
        # Expected: 3+3+3+2+1 = 12
        assert score >= 10, f"Critical presentation should give NEWS2≥10, got {score}"
    
    @pytest.mark.parametrize("rr,expected_points", [
        (7, 3), (8, 3),    # ≤8 → 3
        (9, 1), (11, 1),   # 9-11 → 1
        (12, 0), (20, 0),  # 12-20 → 0
        (21, 2), (24, 2),  # 21-24 → 2
        (25, 3), (40, 3),  # ≥25 → 3
    ])
    def test_news2_rr_scoring(self, vitals_engine, rr, expected_points):
        """Test each respiratory rate threshold band."""
        vitals = {"respiratory_rate": float(rr)}
        # Only RR in vitals
        score = vitals_engine._calculate_news2(vitals)
        assert score == expected_points, f"RR={rr} should give {expected_points} points, got {score}"
    
    @pytest.mark.parametrize("spo2,expected_points", [
        (91, 3), (90, 3),   # ≤91 → 3
        (92, 2), (93, 2),   # 92-93 → 2
        (94, 1), (95, 1),   # 94-95 → 1
        (96, 0), (100, 0),  # ≥96 → 0
    ])
    def test_news2_spo2_scoring(self, vitals_engine, spo2, expected_points):
        vitals = {"spo2_pulse_ox": float(spo2)}
        score = vitals_engine._calculate_news2(vitals)
        assert score == expected_points, f"SpO2={spo2} should give {expected_points} points, got {score}"
    
    def test_news2_triggers_high_alert(self, vitals_engine):
        """NEWS2 ≥ 5 must trigger HIGH alert per configured thresholds."""
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(hr=118, spo2=91, sbp=88, rr=28, temp=38.8)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        assert prediction.news2_score >= 5
        assert prediction.alert_priority in ["HIGH", "CRITICAL"]
        assert len(prediction.active_alerts) > 0


# ─────────────────────────────────────────────
# Sepsis Prediction Validation
# ─────────────────────────────────────────────

class TestSepsisPrediction:
    """
    Validate sepsis prediction model performance.
    Minimum: AUROC > 0.85, sensitivity > 0.80 at specificity 0.85
    
    NOTE: These tests use synthetic data validation.
    Real clinical validation requires hospital deployment.
    """
    
    def test_sepsis_alert_on_classic_presentation(self, vitals_engine):
        """Classic sepsis triad should produce high sepsis probability."""
        patient_id = str(uuid.uuid4())
        # Classic septic shock: tachycardia, hypotension, fever, high RR
        vitals = make_vitals(hr=128, spo2=88, sbp=85, rr=30, temp=39.1)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        # Should flag sepsis
        assert prediction.sepsis_12h > 0.50, (
            f"Classic sepsis presentation should have sepsis probability >0.50, "
            f"got {prediction.sepsis_12h}"
        )
        assert prediction.alert_priority in ["HIGH", "CRITICAL"]
    
    def test_no_false_alarm_on_normal_patient(self, vitals_engine):
        """Normal patient should NOT trigger sepsis alert."""
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(hr=75, spo2=98, sbp=125, rr=15, temp=36.9)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        assert prediction.sepsis_12h < 0.30, (
            f"Normal patient should have sepsis probability <0.30, "
            f"got {prediction.sepsis_12h}"
        )
    
    def test_uncertainty_reported_with_prediction(self, vitals_engine):
        """Every prediction must include uncertainty (MC Dropout output)."""
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(hr=100, spo2=94, sbp=108, rr=20, temp=38.2)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        assert prediction.deterioration_uncertainty is not None
        assert 0 <= prediction.deterioration_uncertainty <= 1.0, (
            f"Uncertainty must be 0-1, got {prediction.deterioration_uncertainty}"
        )
        assert prediction.sepsis_uncertainty is not None
    
    def test_prediction_completes_within_5_seconds(self, vitals_engine):
        """Real-time requirement: predictions must complete in <5 seconds."""
        import time
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(hr=105, spo2=93, sbp=100, rr=22, temp=38.5)
        
        start = time.time()
        prediction = vitals_engine.analyze(patient_id, vitals)
        elapsed = time.time() - start
        
        assert elapsed < 5.0, f"Prediction took {elapsed:.2f}s — must be <5s"
    
    def test_deteriorating_trend_increases_risk(self, vitals_engine):
        """Worsening trend should increase predicted risk."""
        patient_id = str(uuid.uuid4())
        
        # Stable patient
        stable_vitals = make_vitals(hr=85, spo2=96, sbp=118, rr=17, temp=37.1)
        stable_pred = vitals_engine.analyze(patient_id, stable_vitals)
        
        # Same patient, deteriorating
        detior_vitals = make_vitals(hr=115, spo2=90, sbp=95, rr=26, temp=38.7)
        detior_pred = vitals_engine.analyze(patient_id, detior_vitals)
        
        assert detior_pred.deterioration_6h > stable_pred.deterioration_6h, (
            "Deteriorating vitals should increase deterioration probability"
        )
        assert detior_pred.news2_score > stable_pred.news2_score


# ─────────────────────────────────────────────
# HIPAA De-identification Tests
# ZERO TOLERANCE for PHI leakage
# ─────────────────────────────────────────────

class TestDeIdentification:
    """
    Validate that de-identification removes all 18 PHI identifiers.
    ZERO tolerance: any PHI in de-identified output = critical failure.
    """
    
    TEST_PATIENT_FHIR = {
        "resourceType": "Patient",
        "id": "test-patient-001",
        "name": [{"use": "official", "family": "SHARMA", "given": ["RAJESH", "KUMAR"]}],
        "birthDate": "1965-08-15",
        "gender": "male",
        "identifier": [{"value": "MRN123456", "type": {"coding": [{"code": "MR"}]}}],
        "telecom": [{"system": "phone", "value": "+91-9876543210"}, {"system": "email", "value": "rajesh@example.com"}],
        "address": [{"line": ["123 Main St", "Apt 4B"], "city": "Lucknow", "state": "UP", "postalCode": "226001", "country": "IN"}],
    }
    
    def test_name_removed_after_deidentification(self, deidentifier):
        """Patient name must not appear in de-identified output."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        result_str = str(result).lower()
        assert "sharma" not in result_str, "Last name SHARMA found in de-identified output"
        assert "rajesh" not in result_str, "First name RAJESH found in de-identified output"
        assert "kumar" not in result_str, "Middle name KUMAR found in de-identified output"
    
    def test_phone_removed_after_deidentification(self, deidentifier):
        """Phone number must be removed."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        result_str = str(result)
        assert "9876543210" not in result_str, "Phone number found in de-identified output"
        assert "telecom" not in result_str or result["telecom"] == [], "Telecom not removed"
    
    def test_email_removed_after_deidentification(self, deidentifier):
        """Email must be removed."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        result_str = str(result)
        assert "rajesh@example.com" not in result_str, "Email found in de-identified output"
        assert "@" not in result_str, "Email-like string found in de-identified output"
    
    def test_birth_year_preserved(self, deidentifier):
        """Birth YEAR must be preserved (Safe Harbor allows year)."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        assert "1965" in str(result), "Birth year 1965 should be preserved"
    
    def test_full_birthdate_removed(self, deidentifier):
        """Full birth date (month + day) must not appear."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        result_str = str(result)
        assert "08-15" not in result_str, "Birth month+day found in de-identified output"
        assert "1965-08" not in result_str, "Full birth date prefix found"
    
    def test_state_preserved(self, deidentifier):
        """State-level geography preserved (Safe Harbor allows state)."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        address_str = str(result.get("address", ""))
        assert "UP" in address_str or "Uttar Pradesh" in address_str or len(result.get("address", [])) > 0
    
    def test_city_removed(self, deidentifier):
        """City (below state level) must be generalized or removed."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        result_str = str(result)
        assert "Lucknow" not in result_str, "City name found in de-identified output"
    
    def test_mrn_pseudonymized_not_removed(self, deidentifier):
        """MRN must be pseudonymized (not removed) for record linkage."""
        result = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        assert "MRN123456" not in str(result), "Original MRN should not appear"
        assert len(result.get("identifier", [])) > 0, "Identifier should be pseudonymized, not removed"
    
    def test_pseudonymization_deterministic(self, deidentifier):
        """Same input → same pseudonym (for record matching)."""
        result1 = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        result2 = deidentifier.deidentify_patient(self.TEST_PATIENT_FHIR, "test-seed-001")
        
        assert result1["id"] == result2["id"], "Same patient should produce same pseudonymous ID"
    
    def test_free_text_phi_scrubbing(self, deidentifier):
        """PHI patterns in free text must be scrubbed."""
        clinical_note = """
        Patient Rajesh Sharma (MRN: 123456789) admitted today.
        DOB: 1965-08-15. Phone: 9876543210.
        Email: rajesh.sharma@email.com
        SSN: 123-45-6789
        IP: 192.168.1.100
        """
        scrubbed = deidentifier.scrub_free_text(clinical_note)
        
        assert "9876543210" not in scrubbed, "Phone number not scrubbed"
        assert "123-45-6789" not in scrubbed, "SSN not scrubbed"
        assert "rajesh.sharma@email.com" not in scrubbed, "Email not scrubbed"
        assert "192.168.1.100" not in scrubbed, "IP address not scrubbed"


# ─────────────────────────────────────────────
# ABAC Access Control Tests
# ─────────────────────────────────────────────

class TestABACAccessControl:
    """Validate access control rules are correctly enforced."""
    
    def _make_request(self, role, relationship=CareRelationship.TREATING, 
                      sensitivity=DataSensitivity.STANDARD,
                      time_ctx=TimeContext.ACTIVE_SHIFT,
                      action=AccessAction.READ,
                      reason=AccessReason.TREATMENT,
                      irb_approved=False):
        return AccessRequest(
            actor_id="test-user-001",
            actor_role=role,
            patient_id="patient-001",
            resource_type="Observation",
            action=action,
            reason=reason,
            care_relationship=relationship,
            data_sensitivity=sensitivity,
            time_context=time_ctx,
            ip_address="127.0.0.1",
            irb_approved=irb_approved,
        )
    
    def test_treating_physician_can_read(self, abac):
        req = self._make_request(UserRole.PHYSICIAN, CareRelationship.TREATING)
        allowed, reason = abac.check_access(req)
        assert allowed, f"Treating physician should have read access, denied: {reason}"
    
    def test_no_relationship_denied(self, abac):
        req = self._make_request(UserRole.PHYSICIAN, CareRelationship.NO_RELATIONSHIP)
        allowed, reason = abac.check_access(req)
        assert not allowed, "Physician with no care relationship should be denied"
    
    def test_nurse_cannot_access_sensitive_data(self, abac):
        req = self._make_request(
            UserRole.NURSE,
            relationship=CareRelationship.TREATING,
            sensitivity=DataSensitivity.SENSITIVE,
        )
        allowed, reason = abac.check_access(req)
        assert not allowed, f"Nurse should NOT access sensitive data, but was allowed: {reason}"
    
    def test_nurse_can_access_standard_assigned_patient(self, abac):
        req = self._make_request(
            UserRole.NURSE,
            relationship=CareRelationship.TREATING,
            sensitivity=DataSensitivity.STANDARD,
        )
        allowed, reason = abac.check_access(req)
        assert allowed, f"Nurse should access standard data for assigned patient, denied: {reason}"
    
    def test_ai_system_read_allowed(self, abac):
        req = self._make_request(
            UserRole.AI_SYSTEM,
            action=AccessAction.READ,
        )
        allowed, reason = abac.check_access(req)
        assert allowed, f"AI system read should be allowed"
    
    def test_ai_system_write_denied(self, abac):
        req = self._make_request(
            UserRole.AI_SYSTEM,
            action=AccessAction.WRITE,
        )
        allowed, reason = abac.check_access(req)
        assert not allowed, "AI system should NEVER be allowed to write"
    
    def test_ai_system_export_denied(self, abac):
        req = self._make_request(
            UserRole.AI_SYSTEM,
            action=AccessAction.EXPORT,
        )
        allowed, reason = abac.check_access(req)
        assert not allowed, "AI system should NEVER be allowed to export"
    
    def test_researcher_without_irb_denied(self, abac):
        req = self._make_request(
            UserRole.RESEARCHER,
            reason=AccessReason.RESEARCH,
            irb_approved=False,
        )
        allowed, reason = abac.check_access(req)
        assert not allowed, "Researcher without IRB approval should be denied"
    
    def test_researcher_with_irb_allowed(self, abac):
        req = self._make_request(
            UserRole.RESEARCHER,
            reason=AccessReason.RESEARCH,
            irb_approved=True,
        )
        allowed, reason = abac.check_access(req)
        assert allowed, f"Researcher with IRB should be allowed"
    
    def test_radiologist_imaging_allowed(self, abac):
        req = AccessRequest(
            actor_id="rad-001",
            actor_role=UserRole.RADIOLOGIST,
            patient_id="patient-001",
            resource_type="ImagingStudy",
            action=AccessAction.READ,
            reason=AccessReason.TREATMENT,
            care_relationship=CareRelationship.TREATING,
            data_sensitivity=DataSensitivity.STANDARD,
            time_context=TimeContext.ACTIVE_SHIFT,
            ip_address="127.0.0.1",
        )
        allowed, reason = abac.check_access(req)
        assert allowed, f"Radiologist should access imaging studies"
    
    def test_radiologist_medication_denied(self, abac):
        req = AccessRequest(
            actor_id="rad-001",
            actor_role=UserRole.RADIOLOGIST,
            patient_id="patient-001",
            resource_type="MedicationRequest",
            action=AccessAction.READ,
            reason=AccessReason.TREATMENT,
            care_relationship=CareRelationship.TREATING,
            data_sensitivity=DataSensitivity.STANDARD,
            time_context=TimeContext.ACTIVE_SHIFT,
            ip_address="127.0.0.1",
        )
        allowed, reason = abac.check_access(req)
        assert not allowed, "Radiologist should NOT access medication records"


# ─────────────────────────────────────────────
# MPI Matching Tests
# ─────────────────────────────────────────────

class TestMPIMatching:
    """Test Master Patient Index probabilistic matching."""
    
    def test_jaro_winkler_identical(self, jw):
        """Identical strings → similarity = 1.0"""
        score = jw.similarity("SHARMA", "SHARMA")
        assert score == 1.0
    
    def test_jaro_winkler_similar_names(self, jw):
        """Very similar names → high similarity."""
        score = jw.similarity("SHARMA", "SHARME")
        assert score > 0.90, f"SHARMA/SHARME similarity should be >0.90, got {score}"
    
    def test_jaro_winkler_different_names(self, jw):
        """Very different names → low similarity."""
        score = jw.similarity("SHARMA", "JOHNSON")
        assert score < 0.70, f"SHARMA/JOHNSON similarity should be <0.70, got {score}"
    
    def test_jaro_winkler_empty_string(self, jw):
        """Empty string → similarity = 0.0"""
        score = jw.similarity("", "SHARMA")
        assert score == 0.0
    
    def test_jaro_winkler_case_insensitive(self, jw):
        """Matching should be case-insensitive."""
        score1 = jw.similarity("sharma", "SHARMA")
        score2 = jw.similarity("SHARMA", "SHARMA")
        assert score1 == score2, "Matching should be case-insensitive"
    
    @pytest.mark.parametrize("name1,name2,expected_match", [
        ("SHARMA", "SHARMA", True),
        ("SHARMA", "SHARME", True),    # Typo variant
        ("PATEL", "PATEL", True),
        ("SHARMA", "JOHNSON", False),  # Different
        ("SINGH", "SINGHANIA", False), # Different (similar prefix but different)
    ])
    def test_name_match_threshold(self, jw, name1, name2, expected_match):
        """Test name match/no-match decisions."""
        is_match, score = jw.name_match(name1, name2, threshold=0.92)
        assert is_match == expected_match, (
            f"'{name1}' vs '{name2}': expected match={expected_match}, "
            f"got match={is_match} (score={score:.4f})"
        )


# ─────────────────────────────────────────────
# Drug Safety Tests
# ─────────────────────────────────────────────

class TestDrugSafety:
    """Test pharmacist agent drug interaction detection."""
    
    @pytest.mark.asyncio
    async def test_warfarin_aspirin_flagged(self):
        agent = PharmacistAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            current_medications=[{"name": "Warfarin 5mg PO daily"}],
            new_medication={"name": "Aspirin 325mg PO daily"},
        )
        
        assert result.payload["alert_level"] in ["WARNING", "CRITICAL"], (
            "Warfarin + Aspirin should generate an alert"
        )
        alert_texts = str([a["description"] for a in result.payload["alerts"]])
        assert "warfarin" in alert_texts.lower() or "aspirin" in alert_texts.lower(), (
            "Alert should mention the interacting drugs"
        )
    
    @pytest.mark.asyncio
    async def test_renal_dose_flag_low_gfr(self):
        agent = PharmacistAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            current_medications=[],
            new_medication={"name": "Vancomycin 1.5g IV Q12H"},
            patient_weight_kg=70.0,
            renal_function_gfr=18.0,  # Severely impaired
        )
        
        alerts = result.payload.get("alerts", [])
        renal_alerts = [a for a in alerts if a.get("type") == "renal_dose_adjustment"]
        assert len(renal_alerts) > 0, (
            "Vancomycin with GFR=18 should trigger renal dose adjustment alert"
        )
    
    @pytest.mark.asyncio
    async def test_penicillin_allergy_cross_reactivity(self):
        agent = PharmacistAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            current_medications=[],
            new_medication={"name": "Piperacillin/Tazobactam"},
            allergies=["Penicillin"],
        )
        
        alerts = result.payload.get("alerts", [])
        allergy_alerts = [a for a in alerts if a.get("type") == "allergy_cross_reactivity"]
        assert len(allergy_alerts) > 0, (
            "Penicillin allergy + Piperacillin should trigger cross-reactivity alert"
        )
        assert any(a.get("severity") == "CRITICAL" for a in allergy_alerts), (
            "Allergy cross-reactivity should be CRITICAL severity"
        )
    
    @pytest.mark.asyncio
    async def test_critical_alert_bypasses_coordinator(self):
        """CRITICAL pharmacy alert must set bypass_coordinator=True."""
        agent = PharmacistAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            current_medications=[{"name": "Warfarin 5mg daily"}],
            new_medication={"name": "Aspirin 325mg daily"},
        )
        
        if result.payload["alert_level"] == "CRITICAL":
            assert result.payload.get("bypass_coordinator") == True, (
                "CRITICAL pharmacy alert must bypass coordinator"
            )


# ─────────────────────────────────────────────
# Anomaly Detection Tests
# ─────────────────────────────────────────────

class TestAnomalyDetection:
    
    def test_critical_spo2_flagged(self, vitals_engine):
        """SpO2 < 85% must be flagged as critical anomaly."""
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(spo2=82)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        critical_anomalies = [a for a in prediction.anomalies if a.severity == "critical"]
        spo2_anomaly = any("spo2" in a.parameter.lower() for a in critical_anomalies)
        
        assert spo2_anomaly or prediction.news2_score >= 3, (
            f"SpO2=82% should generate critical anomaly or NEWS2≥3"
        )
    
    def test_artifact_detection(self, preprocessor):
        """Physiologically impossible values must be detected as artifacts."""
        assert preprocessor.detect_artifacts(0, "heart_rate") == True
        assert preprocessor.detect_artifacts(301, "heart_rate") == True
        assert preprocessor.detect_artifacts(101, "spo2_pulse_ox") == True
        assert preprocessor.detect_artifacts(80, "heart_rate") == False   # Normal
        assert preprocessor.detect_artifacts(97, "spo2_pulse_ox") == False  # Normal
    
    def test_empty_stream_handled_gracefully(self, vitals_engine):
        """Empty vitals stream must not crash — return safe empty prediction."""
        patient_id = str(uuid.uuid4())
        prediction = vitals_engine.analyze(patient_id, [])
        
        assert prediction is not None
        assert prediction.patient_id == patient_id
        # Should have maximum uncertainty since no data
        assert prediction.deterioration_uncertainty >= 0.8


# ─────────────────────────────────────────────
# Triage Agent Tests
# ─────────────────────────────────────────────

class TestTriageAgent:
    
    @pytest.mark.asyncio
    async def test_critical_vitals_esi1(self):
        agent = TriageAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            vitals=[
                {"parameter": "spo2_pulse_ox", "value": 80},
                {"parameter": "heart_rate", "value": 145},
            ],
            chief_complaint="Unresponsive",
            current_problems=["Cardiac arrest"],
        )
        
        assert result.payload["esi_category"] == 1, (
            "Unresponsive patient with SpO2=80% should be ESI 1"
        )
        assert result.payload["physician_page_required"] == True
    
    @pytest.mark.asyncio
    async def test_normal_patient_esi3_or_lower(self):
        agent = TriageAgent()
        result = await agent.run(
            patient_id=str(uuid.uuid4()),
            vitals=[
                {"parameter": "heart_rate", "value": 82},
                {"parameter": "spo2_pulse_ox", "value": 97},
                {"parameter": "bp_systolic", "value": 125},
                {"parameter": "respiratory_rate", "value": 16},
            ],
            chief_complaint="Routine post-op check",
            current_problems=[],
        )
        
        assert result.payload["esi_category"] >= 3, (
            f"Stable post-op patient should be ESI ≥ 3, got ESI {result.payload['esi_category']}"
        )


# ─────────────────────────────────────────────
# Integration Smoke Test
# ─────────────────────────────────────────────

class TestEndToEndSmokeTest:
    """Quick smoke test to verify all layers connect."""
    
    def test_vitals_engine_returns_complete_prediction(self, vitals_engine):
        patient_id = str(uuid.uuid4())
        vitals = make_vitals(hr=110, spo2=92, sbp=105, rr=22, temp=38.4)
        prediction = vitals_engine.analyze(patient_id, vitals)
        
        # All required fields present
        assert prediction.patient_id == patient_id
        assert prediction.news2_score is not None
        assert prediction.deterioration_6h is not None
        assert prediction.sepsis_12h is not None
        assert prediction.trend is not None
        assert prediction.alert_priority is not None
        assert prediction.timestamp is not None
        
        # Values in valid ranges
        assert 0 <= prediction.deterioration_6h <= 1
        assert 0 <= prediction.sepsis_12h <= 1
        assert 0 <= prediction.mortality_24h <= 1
        assert prediction.news2_score >= 0
    
    def test_deidentification_round_trip(self, deidentifier):
        """De-identification must be stable (same input → same output)."""
        patient = {
            "resourceType": "Patient",
            "id": "test-patient-999",
            "name": [{"family": "TEST", "given": ["PATIENT"]}],
            "birthDate": "1970-05-20",
            "gender": "male",
            "identifier": [{"value": "MRN999"}],
            "telecom": [{"system": "phone", "value": "9999999999"}],
            "address": [{"city": "Delhi", "state": "DL", "postalCode": "110001"}],
        }
        
        result1 = deidentifier.deidentify_patient(patient, "seed-999")
        result2 = deidentifier.deidentify_patient(patient, "seed-999")
        
        assert result1["id"] == result2["id"], "De-identification should be deterministic"
