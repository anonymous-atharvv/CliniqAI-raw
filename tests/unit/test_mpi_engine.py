"""Unit Tests — MPI Engine"""
import pytest, sys, os, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'backend'))
from services.mpi.engine import MPIEngine, JaroWinklerSimilarity, MatchDecision


@pytest.fixture
def jw():
    return JaroWinklerSimilarity()

@pytest.fixture
def mpi():
    return MPIEngine(mpi_store=None, hmac_salt="test-salt-never-production")


class TestJaroWinkler:
    def test_identical_strings(self, jw):
        assert jw.similarity("SHARMA", "SHARMA") == 1.0

    def test_empty_string(self, jw):
        assert jw.similarity("", "SHARMA") == 0.0
        assert jw.similarity("SHARMA", "") == 0.0

    def test_completely_different(self, jw):
        assert jw.similarity("SHARMA", "JOHNSON") < 0.70

    def test_similar_with_typo(self, jw):
        assert jw.similarity("SHARMA", "SHARME") > 0.90

    def test_case_insensitive(self, jw):
        s1 = jw.similarity("sharma", "SHARMA")
        s2 = jw.similarity("SHARMA", "SHARMA")
        assert abs(s1 - s2) < 0.001

    @pytest.mark.parametrize("n1,n2,expected_match", [
        ("SHARMA", "SHARMA", True),
        ("PATEL",  "PATEL",  True),
        ("SHARMA", "SHARME", True),
        ("SHARMA", "JOHNSON", False),
        ("SINGH",  "SINGHANIA", False),
        ("GUPTA",  "GUPTA",  True),
    ])
    def test_name_match_threshold(self, jw, n1, n2, expected_match):
        is_match, score = jw.name_match(n1, n2, threshold=0.92)
        assert is_match == expected_match, f"'{n1}' vs '{n2}': got {is_match} (score={score:.4f})"

    def test_symmetric(self, jw):
        s1 = jw.similarity("SHARMA", "PATEL")
        s2 = jw.similarity("PATEL", "SHARMA")
        assert abs(s1 - s2) < 0.001


class TestMPIEngine:
    def test_pseudonymize_deterministic(self, mpi):
        p1 = mpi._hash_field("MRN001", "MRN")
        p2 = mpi._hash_field("MRN001", "MRN")
        assert p1 == p2

    def test_pseudonymize_different_inputs(self, mpi):
        p1 = mpi._hash_field("MRN001", "MRN")
        p2 = mpi._hash_field("MRN002", "MRN")
        assert p1 != p2

    def test_pseudonymize_prefix_included(self, mpi):
        p_mrn  = mpi._hash_field("12345", "MRN")
        p_ssn4 = mpi._hash_field("12345", "SSN4")
        assert p_mrn != p_ssn4

    def test_find_matches_returns_empty_without_store(self, mpi):
        candidates = mpi.find_matches(
            last_name="Sharma", first_name="Rajesh",
            date_of_birth="1965-08-15", mrn="MRN001",
        )
        assert isinstance(candidates, list)

    def test_create_canonical_record(self, mpi):
        identity = mpi.create_canonical_record(
            last_name="Sharma", first_name="Rajesh", middle_name=None,
            date_of_birth="1965-08-15", gender="M",
            mrn="MRN001", source_system="epic",
        )
        assert identity.global_patient_id is not None
        assert identity.deidentified_id is not None
        assert "epic:MRN001" in identity.mrn_list
        assert "epic" in identity.source_systems
        assert identity.dob == "1965-08-15"
        assert identity.gender == "M"

    def test_name_hash_structure(self, mpi):
        identity = mpi.create_canonical_record(
            last_name="Sharma", first_name="Rajesh", middle_name="Kumar",
            date_of_birth="1965-08-15", gender="M", mrn="001", source_system="epic",
        )
        assert "|" in identity.name_hash
        parts = identity.name_hash.split("|")
        assert len(parts) == 3

    def test_confidential_deidentified_id_different_from_global(self, mpi):
        identity = mpi.create_canonical_record(
            last_name="Test", first_name="User", middle_name=None,
            date_of_birth="1980-01-01", gender="F", mrn="002", source_system="cerner",
        )
        assert identity.global_patient_id != str(identity.deidentified_id)
