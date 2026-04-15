"""
Unit Tests — FHIR Normalizer
Tests every normalization function with known inputs and expected outputs.
All edge cases from real Epic/Cerner data included.
"""
import pytest, sys, os, uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from services.fhir.normalizer import FHIRNormalizer, LOINC_CODES, CLINICAL_RANGES
from services.fhir.loinc_mapper import LOINCMapper


@pytest.fixture
def normalizer():
    return FHIRNormalizer(hospital_id="test_hospital", hospital_system="epic")


# ─── Patient Normalization ────────────────────────────────────────────────────

class TestPatientNormalization:

    def test_complete_patient_normalizes_successfully(self, normalizer):
        raw = {"mrn": "MRN001", "last_name": "Sharma", "first_name": "Rajesh",
               "date_of_birth": "1965-08-15", "gender": "M",
               "phone": "9876543210", "state": "UP", "zip_code": "226001"}
        result = normalizer.normalize_patient(raw)
        assert result.success
        assert result.resource_type.value == "Patient"
        assert result.fhir_resource["resourceType"] == "Patient"
        assert result.quality_score >= 0.60

    def test_missing_mrn_produces_error(self, normalizer):
        result = normalizer.normalize_patient({"last_name": "Sharma", "first_name": "Raj", "date_of_birth": "1965-01-01", "gender": "M"})
        assert not result.success
        assert any("mrn" in e.lower() or "MRN" in e for e in result.errors)

    def test_missing_last_name_produces_error(self, normalizer):
        result = normalizer.normalize_patient({"mrn": "001", "date_of_birth": "1965-01-01", "gender": "M"})
        assert not result.success

    def test_gender_mapping_male(self, normalizer):
        result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": "1980-01-01", "gender": "M"})
        assert result.fhir_resource.get("gender") == "male"

    def test_gender_mapping_female(self, normalizer):
        result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": "1980-01-01", "gender": "F"})
        assert result.fhir_resource.get("gender") == "female"

    def test_gender_hl7_code_mapping(self, normalizer):
        """HL7 v2 uses numeric gender codes: 1=male, 2=female."""
        result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": "1980-01-01", "gender": "1"})
        assert result.fhir_resource.get("gender") == "male"

    def test_dob_various_formats(self, normalizer):
        for dob_input in ["1965-08-15", "08/15/1965", "19650815"]:
            result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": dob_input, "gender": "M"})
            dob_out = result.fhir_resource.get("birthDate", "")
            assert "1965" in dob_out, f"Failed for input '{dob_input}': got '{dob_out}'"

    def test_fhir_r4_structure(self, normalizer):
        result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": "1970-01-01", "gender": "M"})
        r = result.fhir_resource
        assert "resourceType" in r
        assert "id" in r
        assert "meta" in r
        assert "identifier" in r

    def test_quality_score_range(self, normalizer):
        result = normalizer.normalize_patient({"mrn": "001", "last_name": "Test", "date_of_birth": "1970-01-01", "gender": "M"})
        assert 0.0 <= result.quality_score <= 1.0

    def test_high_quality_complete_patient(self, normalizer):
        raw = {"mrn": "001", "last_name": "Test", "first_name": "User",
               "date_of_birth": "1970-06-15", "gender": "M",
               "phone": "1234567890", "email": "test@test.com",
               "address_line1": "123 Main St", "city": "Delhi", "state": "DL", "zip_code": "110001"}
        result = normalizer.normalize_patient(raw)
        assert result.quality_score >= 0.80


# ─── Observation Normalization ─────────────────────────────────────────────────

class TestObservationNormalization:

    def test_heart_rate_gets_correct_loinc(self, normalizer):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="heart_rate",
            value=82.0, unit="/min", timestamp=datetime.now(timezone.utc)
        )
        assert result.success
        codings = result.fhir_resource["code"]["coding"]
        loinc = next((c for c in codings if c.get("system") == "http://loinc.org"), None)
        assert loinc is not None
        assert loinc["code"] == "8867-4"

    def test_spo2_gets_correct_loinc(self, normalizer):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="spo2_pulse_ox",
            value=97.0, unit="%", timestamp=datetime.now(timezone.utc)
        )
        assert result.success
        codings = result.fhir_resource["code"]["coding"]
        loinc = next((c for c in codings if c.get("system") == "http://loinc.org"), None)
        assert loinc["code"] == "59408-5"

    def test_unknown_vital_type_produces_error(self, normalizer):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="unknown_param",
            value=50.0, unit="units", timestamp=datetime.now(timezone.utc)
        )
        assert not result.success

    def test_critical_spo2_gets_ll_interpretation(self, normalizer):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="spo2_pulse_ox",
            value=82.0, unit="%", timestamp=datetime.now(timezone.utc)
        )
        assert result.success
        interp = result.fhir_resource.get("interpretation", [{}])[0]
        codings = interp.get("coding", [{}])
        codes = [c.get("code") for c in codings]
        assert "LL" in codes or "L" in codes

    @pytest.mark.parametrize("param,value,should_be_artifact", [
        ("heart_rate",    0.0,   True),
        ("heart_rate",    80.0,  False),
        ("heart_rate",    301.0, True),
        ("spo2_pulse_ox", 101.0, True),
        ("spo2_pulse_ox", 97.0,  False),
        ("spo2_pulse_ox", 49.0,  True),
    ])
    def test_artifact_detection(self, normalizer, param, value, should_be_artifact):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type=param,
            value=value, unit="units", timestamp=datetime.now(timezone.utc)
        )
        if should_be_artifact:
            # Artifacts are rejected (success=False) or flagged
            assert not result.success or "entered-in-error" in result.fhir_resource.get("status", "")
        else:
            assert result.success

    def test_observation_has_effective_datetime(self, normalizer):
        ts = datetime.now(timezone.utc)
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="heart_rate",
            value=80.0, unit="/min", timestamp=ts
        )
        assert result.fhir_resource.get("effectiveDateTime") is not None

    def test_device_id_included_when_provided(self, normalizer):
        result = normalizer.normalize_vital_sign(
            patient_id=str(uuid.uuid4()), vital_type="heart_rate",
            value=80.0, unit="/min", timestamp=datetime.now(timezone.utc),
            device_id="monitor-B04"
        )
        assert result.fhir_resource.get("device") == {"reference": "Device/monitor-B04"}


# ─── LOINC Mapper ────────────────────────────────────────────────────────────

class TestLOINCMapper:

    def test_all_mandatory_vitals_present(self):
        """The 5 mandatory vitals from the clinical spec must all be present."""
        mandatory = ["heart_rate", "spo2_pulse_ox", "bp_systolic", "bp_diastolic", "temperature", "respiratory_rate"]
        for param in mandatory:
            code = LOINCMapper.get_code(param)
            assert code is not None, f"Missing LOINC code for mandatory vital: {param}"

    def test_heart_rate_code(self):
        assert LOINCMapper.get_code("heart_rate") == "8867-4"

    def test_spo2_pulse_ox_code(self):
        assert LOINCMapper.get_code("spo2_pulse_ox") == "59408-5"

    def test_bp_systolic_code(self):
        assert LOINCMapper.get_code("bp_systolic") == "8480-6"

    def test_bp_diastolic_code(self):
        assert LOINCMapper.get_code("bp_diastolic") == "8462-4"

    def test_temperature_code(self):
        assert LOINCMapper.get_code("temperature") == "8310-5"

    def test_respiratory_rate_code(self):
        assert LOINCMapper.get_code("respiratory_rate") == "9279-1"

    def test_reverse_lookup(self):
        assert LOINCMapper.from_code("8867-4") == "heart_rate"

    def test_build_fhir_coding_structure(self):
        coding = LOINCMapper.build_fhir_coding("heart_rate")
        assert coding is not None
        assert coding["coding"][0]["system"] == "http://loinc.org"
        assert coding["coding"][0]["code"] == "8867-4"

    def test_vital_signs_category(self):
        assert LOINCMapper.is_vital_sign("heart_rate")
        assert not LOINCMapper.is_vital_sign("wbc")

    def test_laboratory_category(self):
        assert LOINCMapper.is_laboratory("wbc")
        assert not LOINCMapper.is_laboratory("heart_rate")

    def test_search_by_display(self):
        results = LOINCMapper.search("lactate")
        assert any("lactate" in name.lower() for name, _ in results)

    def test_unit_normalization(self):
        assert LOINCMapper.normalize_unit("heart_rate", "bpm") == "/min"
        assert LOINCMapper.normalize_unit("bp_systolic", "mmhg") == "mm[Hg]"

    def test_invalid_code_returns_false(self):
        assert not LOINCMapper.is_valid_code("9999-9")
        assert not LOINCMapper.is_valid_code("")

    def test_unknown_parameter_returns_none(self):
        assert LOINCMapper.get("unknown_parameter") is None
        assert LOINCMapper.get_code("unknown_parameter") is None
