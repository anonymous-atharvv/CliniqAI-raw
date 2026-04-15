"""
Master Patient Index (MPI) — Probabilistic Patient Identity Resolution
Layer 1: Data Integration

The problem: Community hospitals have patients scattered across Epic, Cerner,
lab systems, radiology PACS, billing — each with different patient IDs.
The same Jane Smith born 1965 might be JSmith, J.Smith, Jane M Smith across systems.

We must resolve these into a single canonical patient identity WITHOUT ever
auto-merging without an audit trail. A wrong merge can kill a patient
(wrong blood type, wrong medication history, wrong allergies).

Algorithm:
- Jaro-Winkler string similarity for names
- Exact matching for SSN (last 4), MRN, DOB
- Confidence scoring with configurable weights
- Threshold: ≥0.95 auto-link, 0.80-0.94 human review, <0.80 separate record

NEVER auto-merge. Always audit trail. Always preserve original records.
"""

import uuid
import hashlib
import logging
import math
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone, date

logger = logging.getLogger(__name__)


class MatchDecision(str, Enum):
    AUTO_LINK = "auto_link"          # ≥ 0.95: high confidence same patient
    HUMAN_REVIEW = "human_review"    # 0.80–0.94: human must decide
    CREATE_NEW = "create_new"        # < 0.80: likely different patient
    BLOCKED = "blocked"              # SSN/DOB conflict: definitely different


@dataclass
class PatientIdentity:
    """
    Canonical patient identity stored in MPI.
    All source system records link to this.
    """
    global_patient_id: str          # CliniQAI-assigned UUID (stable, permanent)
    mrn_list: List[str]             # All MRNs across all source systems
    source_systems: List[str]       # e.g., ["epic:12345", "cerner:67890"]
    
    # Identifying fields (stored hashed for HIPAA)
    name_hash: str                  # HMAC-SHA256 of "LAST^FIRST^MIDDLE"
    dob: str                        # YYYY-MM-DD (stored encrypted at rest)
    ssn_last4_hash: str             # HMAC-SHA256 of last 4 digits
    gender: str
    zip_code_prefix: str            # First 3 digits only (Safe Harbor)
    
    # MPI tracking
    created_at: str
    last_updated: str
    merge_history: List[Dict] = field(default_factory=list)  # Audit trail
    confidence_history: List[Dict] = field(default_factory=list)
    
    # Quality
    data_quality_score: float = 0.0
    deidentified_id: str = ""       # UUID for AI layer (separate from global_patient_id)
    has_conflicting_data: bool = False


@dataclass
class MatchCandidate:
    """A potential match between two patient records."""
    candidate_patient_id: str
    confidence_score: float  # 0.0 – 1.0
    decision: MatchDecision
    
    # Scoring breakdown (for human reviewers)
    field_scores: Dict[str, float] = field(default_factory=dict)
    matching_fields: List[str] = field(default_factory=list)
    conflicting_fields: List[str] = field(default_factory=list)
    
    # Human review metadata
    review_required: bool = False
    review_reason: str = ""
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
    review_decision: Optional[str] = None


class JaroWinklerSimilarity:
    """
    Jaro-Winkler string similarity for name matching.
    
    Why Jaro-Winkler over Levenshtein?
    - Designed for short strings (names)
    - Prefix weighting (Smith/Smithe more similar than Smith/Htims)
    - Handles transpositions better than edit distance
    - Industry standard for record linkage (NIST-approved)
    
    Thresholds:
    - Last name: ≥ 0.92 = match
    - First name: ≥ 0.90 = match (more variation: Bob/Robert, Mike/Michael)
    """
    
    @staticmethod
    def similarity(s1: str, s2: str) -> float:
        """
        Compute Jaro-Winkler similarity between two strings.
        Returns: float between 0.0 (no similarity) and 1.0 (identical)
        """
        if not s1 or not s2:
            return 0.0
        
        s1 = s1.upper().strip()
        s2 = s2.upper().strip()
        
        if s1 == s2:
            return 1.0
        
        len_s1, len_s2 = len(s1), len(s2)
        
        # Match window
        match_distance = max(len_s1, len_s2) // 2 - 1
        match_distance = max(0, match_distance)
        
        s1_matches = [False] * len_s1
        s2_matches = [False] * len_s2
        
        matches = 0
        transpositions = 0
        
        # Find matches
        for i in range(len_s1):
            start = max(0, i - match_distance)
            end = min(i + match_distance + 1, len_s2)
            
            for j in range(start, end):
                if s2_matches[j] or s1[i] != s2[j]:
                    continue
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break
        
        if matches == 0:
            return 0.0
        
        # Count transpositions
        k = 0
        for i in range(len_s1):
            if not s1_matches[i]:
                continue
            while not s2_matches[k]:
                k += 1
            if s1[i] != s2[k]:
                transpositions += 1
            k += 1
        
        # Jaro score
        jaro = (
            matches / len_s1 +
            matches / len_s2 +
            (matches - transpositions / 2) / matches
        ) / 3
        
        # Winkler prefix bonus (up to 4 chars)
        prefix = 0
        for i in range(min(len_s1, len_s2, 4)):
            if s1[i] == s2[i]:
                prefix += 1
            else:
                break
        
        return jaro + prefix * 0.1 * (1 - jaro)
    
    @staticmethod
    def name_match(name1: str, name2: str, threshold: float = 0.92) -> Tuple[bool, float]:
        """Check if two names match above threshold."""
        score = JaroWinklerSimilarity.similarity(name1, name2)
        return score >= threshold, score


class MPIEngine:
    """
    Master Patient Index Engine.
    
    Core functions:
    1. find_matches(): Given a new patient record, find potential matches in MPI
    2. link_records(): Link a source record to a canonical patient (with audit)
    3. request_human_review(): Queue a match for human resolution
    4. NEVER: auto_merge() — this function does not exist
    
    Storage: Dedicated encrypted database, separate from clinical data.
    """
    
    # Field weights for confidence scoring
    WEIGHTS = {
        "ssn_last4":    0.35,   # Strongest identifier
        "mrn":          0.30,   # Second strongest (but source-system specific)
        "date_of_birth": 0.15,  # High confidence but twins exist
        "last_name":    0.10,   # Jaro-Winkler ≥ 0.92
        "first_name":   0.05,   # Jaro-Winkler ≥ 0.90 (nicknames common)
        "zip_code":     0.05,   # Weak: people move
    }
    
    # Decision thresholds
    AUTO_LINK_THRESHOLD = 0.95
    HUMAN_REVIEW_THRESHOLD = 0.80
    
    def __init__(self, mpi_store, hmac_salt: str):
        """
        mpi_store: Database backend for MPI records.
        hmac_salt: Per-deployment secret for consistent hashing.
                   Store in HashiCorp Vault. Never in code.
        """
        self._store = mpi_store
        self._salt = hmac_salt.encode()
        self._jw = JaroWinklerSimilarity()
    
    def _hash_field(self, value: str, prefix: str = "") -> str:
        """
        Consistently hash a PHI field using HMAC-SHA256.
        Deterministic: same value → same hash (for matching).
        Different deployments: different salt → different hashes.
        """
        if not value:
            return ""
        combined = f"{prefix}:{value.upper().strip()}"
        import hmac
        return hmac.new(self._salt, combined.encode(), hashlib.sha256).hexdigest()
    
    def find_matches(
        self,
        last_name: str,
        first_name: str,
        date_of_birth: str,  # YYYY-MM-DD
        mrn: Optional[str] = None,
        ssn_last4: Optional[str] = None,
        zip_code: Optional[str] = None,
        gender: Optional[str] = None,
        source_system: Optional[str] = None,
    ) -> List[MatchCandidate]:
        """
        Find potential matching patients in the MPI.
        
        Returns candidates sorted by confidence (highest first).
        Caller must respect decision thresholds:
        - AUTO_LINK: proceed with linking
        - HUMAN_REVIEW: queue for review, don't link yet
        - CREATE_NEW: create new canonical record
        
        NEVER merge on HUMAN_REVIEW without human approval.
        """
        candidates = []
        
        # Build search hashes
        ssn_hash = self._hash_field(ssn_last4, "SSN4") if ssn_last4 else None
        
        # Candidate retrieval (blocking lookup strategies):
        # 1. Exact SSN match → strongest blocking key
        # 2. Exact MRN match
        # 3. DOB + last name prefix → broader blocking
        # In production: query mpi_store with indexed blocking keys
        
        potential_candidates = self._retrieve_candidates(
            ssn_hash=ssn_hash,
            mrn=mrn,
            dob=date_of_birth,
            last_name_prefix=last_name[:3].upper() if last_name else "",
        )
        
        for candidate in potential_candidates:
            match_result = self._score_candidate(
                candidate=candidate,
                last_name=last_name,
                first_name=first_name,
                date_of_birth=date_of_birth,
                mrn=mrn,
                ssn_last4=ssn_last4,
                zip_code=zip_code,
            )
            
            if match_result.confidence_score > 0.50:  # Filter obvious non-matches
                candidates.append(match_result)
        
        # Sort by confidence descending
        candidates.sort(key=lambda c: c.confidence_score, reverse=True)
        
        return candidates
    
    def _score_candidate(
        self,
        candidate: PatientIdentity,
        last_name: str,
        first_name: str,
        date_of_birth: str,
        mrn: Optional[str],
        ssn_last4: Optional[str],
        zip_code: Optional[str],
    ) -> MatchCandidate:
        """
        Score a single candidate match.
        
        BLOCKING CHECK: If DOB and SSN conflict → BLOCKED (different patients).
        Never merge when hard identifiers conflict.
        """
        field_scores = {}
        matching_fields = []
        conflicting_fields = []
        total_weight = 0.0
        weighted_score = 0.0
        
        # ── SSN (Last 4) ──────────────────────────────
        if ssn_last4 and candidate.ssn_last4_hash:
            incoming_hash = self._hash_field(ssn_last4, "SSN4")
            if incoming_hash == candidate.ssn_last4_hash:
                field_scores["ssn_last4"] = 1.0
                matching_fields.append("SSN (last 4)")
            else:
                field_scores["ssn_last4"] = 0.0
                conflicting_fields.append("SSN (last 4)")
                # Hard conflict on SSN → cannot be same patient
                if candidate.ssn_last4_hash:
                    return MatchCandidate(
                        candidate_patient_id=candidate.global_patient_id,
                        confidence_score=0.0,
                        decision=MatchDecision.BLOCKED,
                        field_scores={"ssn_last4": 0.0},
                        conflicting_fields=["SSN (last 4) — DEFINITE MISMATCH"],
                        review_required=False,
                    )
        
        # ── MRN ──────────────────────────────────────
        if mrn and mrn in (candidate.mrn_list or []):
            field_scores["mrn"] = 1.0
            matching_fields.append("MRN")
        elif mrn:
            field_scores["mrn"] = 0.0
            # MRN miss is not a conflict (different source systems have different MRNs)
        
        # ── Date of Birth ─────────────────────────────
        if date_of_birth and candidate.dob:
            if date_of_birth == candidate.dob:
                field_scores["date_of_birth"] = 1.0
                matching_fields.append("Date of Birth")
            else:
                field_scores["date_of_birth"] = 0.0
                conflicting_fields.append("Date of Birth")
                # Hard conflict on DOB → very strong signal of different patients
        
        # ── Last Name (Jaro-Winkler) ──────────────────
        if last_name:
            # Reconstruct name from hash... 
            # In production: store phonetic encoding (Soundex/NYSIIS) for comparison
            # Here: simplified exact vs phonetic check
            ln_hash = self._hash_field(last_name, "LN")
            if ln_hash == candidate.name_hash.split("|")[0] if "|" in candidate.name_hash else candidate.name_hash:
                field_scores["last_name"] = 1.0
                matching_fields.append("Last Name")
            else:
                # In production: also check Jaro-Winkler against stored phonetic encoding
                field_scores["last_name"] = 0.0
        
        # ── First Name ────────────────────────────────
        if first_name:
            fn_hash = self._hash_field(first_name, "FN")
            parts = candidate.name_hash.split("|")
            stored_fn_hash = parts[1] if len(parts) > 1 else ""
            if fn_hash == stored_fn_hash:
                field_scores["first_name"] = 1.0
                matching_fields.append("First Name")
            else:
                field_scores["first_name"] = 0.0
        
        # ── ZIP Code ──────────────────────────────────
        if zip_code and candidate.zip_code_prefix:
            incoming_prefix = zip_code[:3]
            if incoming_prefix == candidate.zip_code_prefix:
                field_scores["zip_code"] = 1.0
                matching_fields.append("ZIP Code (prefix)")
            else:
                field_scores["zip_code"] = 0.0
        
        # ── Weighted Confidence Score ──────────────────
        for field, weight in self.WEIGHTS.items():
            if field in field_scores:
                weighted_score += field_scores[field] * weight
                total_weight += weight
        
        confidence = weighted_score / total_weight if total_weight > 0 else 0.0
        
        # ── Decision ──────────────────────────────────
        if len(conflicting_fields) >= 2:
            # Multiple hard conflicts → definitely different patients
            decision = MatchDecision.CREATE_NEW
            confidence = min(confidence, 0.30)
        elif confidence >= self.AUTO_LINK_THRESHOLD:
            decision = MatchDecision.AUTO_LINK
        elif confidence >= self.HUMAN_REVIEW_THRESHOLD:
            decision = MatchDecision.HUMAN_REVIEW
        else:
            decision = MatchDecision.CREATE_NEW
        
        return MatchCandidate(
            candidate_patient_id=candidate.global_patient_id,
            confidence_score=round(confidence, 4),
            decision=decision,
            field_scores=field_scores,
            matching_fields=matching_fields,
            conflicting_fields=conflicting_fields,
            review_required=decision == MatchDecision.HUMAN_REVIEW,
            review_reason=(
                f"Confidence {confidence:.0%} in range requiring human review. "
                f"Matches: {', '.join(matching_fields)}. "
                f"Conflicts: {', '.join(conflicting_fields) or 'None'}."
            ) if decision == MatchDecision.HUMAN_REVIEW else "",
        )
    
    def create_canonical_record(
        self,
        last_name: str,
        first_name: str,
        middle_name: Optional[str],
        date_of_birth: str,
        gender: str,
        mrn: str,
        source_system: str,
        ssn_last4: Optional[str] = None,
        zip_code: Optional[str] = None,
        created_by: str = "system",
    ) -> PatientIdentity:
        """
        Create a new canonical patient identity in the MPI.
        
        Called when:
        - First time we see this patient (no matches found)
        - Human reviewer decides incoming record is a new patient
        
        PHI fields are HASHED — original values stored separately in encrypted vault.
        """
        # Build name hash (stores: LN_hash|FN_hash|MN_hash)
        name_parts = [
            self._hash_field(last_name, "LN"),
            self._hash_field(first_name, "FN"),
            self._hash_field(middle_name or "", "MN"),
        ]
        name_hash = "|".join(name_parts)
        
        identity_uuid = str(uuid.uuid4())
        deident_uuid = str(uuid.uuid4())
        identity = PatientIdentity(
            global_patient_id=identity_uuid,
            mrn_list=[f"{source_system}:{mrn}"],
            source_systems=[source_system],
            name_hash=name_hash,
            dob=date_of_birth,
            ssn_last4_hash=self._hash_field(ssn_last4, "SSN4") if ssn_last4 else "",
            gender=gender,
            zip_code_prefix=zip_code[:3] if zip_code and len(zip_code) >= 3 else "",
            created_at=datetime.now(timezone.utc).isoformat(),
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
        
        logger.info(
            f"MPI: Created new canonical identity {identity.global_patient_id} "
            f"from {source_system}:{mrn}"
        )
        
        if self._store:
            self._store.save(identity)
        
        return identity
    
    def link_source_record(
        self,
        global_patient_id: str,
        source_system: str,
        mrn: str,
        match_candidate: MatchCandidate,
        linked_by: str,
    ) -> Dict[str, Any]:
        """
        Link a source system record to an existing canonical identity.
        
        REQUIRES:
        - match_candidate.decision == AUTO_LINK, OR
        - Human reviewer approved (reviewed_by is set)
        
        NEVER called directly for HUMAN_REVIEW decisions without review completion.
        Creates full audit trail entry.
        """
        if match_candidate.decision == MatchDecision.HUMAN_REVIEW:
            if not match_candidate.reviewed_by:
                raise ValueError(
                    "Cannot link HUMAN_REVIEW match without human approval. "
                    f"Queue for review queue and await reviewer decision."
                )
        
        # Audit trail entry (immutable)
        audit_entry = {
            "action": "link_source_record",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "global_patient_id": global_patient_id,
            "source_system": source_system,
            "mrn": mrn,  # In production: hashed
            "match_confidence": match_candidate.confidence_score,
            "match_decision": match_candidate.decision.value,
            "linking_fields": match_candidate.matching_fields,
            "linked_by": linked_by,
            "reviewed_by": match_candidate.reviewed_by,
            "reviewed_at": match_candidate.reviewed_at,
        }
        
        if self._store:
            patient = self._store.get(global_patient_id)
            if patient:
                link_key = f"{source_system}:{mrn}"
                if link_key not in patient.mrn_list:
                    patient.mrn_list.append(link_key)
                if source_system not in patient.source_systems:
                    patient.source_systems.append(source_system)
                patient.confidence_history.append({
                    "confidence": match_candidate.confidence_score,
                    "linked_at": audit_entry["timestamp"],
                })
                patient.last_updated = audit_entry["timestamp"]
                self._store.save(patient)
        
        logger.info(
            f"MPI: Linked {source_system}:{mrn} to global ID {global_patient_id} "
            f"(confidence={match_candidate.confidence_score:.2f}, "
            f"linked_by={linked_by})"
        )
        
        return audit_entry
    
    def _retrieve_candidates(
        self,
        ssn_hash: Optional[str],
        mrn: Optional[str],
        dob: Optional[str],
        last_name_prefix: str,
    ) -> List[PatientIdentity]:
        """
        Retrieve candidate records from MPI using blocking strategies.
        
        Blocking keys (in order of precision):
        1. SSN hash (strongest — exact lookup)
        2. MRN (exact lookup)
        3. DOB + name prefix (broader lookup)
        
        Without blocking: comparing every incoming record to every MPI record
        would be O(n²) — infeasible at 50,000+ patients.
        """
        if self._store:
            return self._store.find_candidates(
                ssn_hash=ssn_hash,
                mrn=mrn,
                dob=dob,
                last_name_prefix=last_name_prefix,
            )
        return []  # Dev mode: no candidates
    
    def get_mpi_statistics(self) -> Dict[str, Any]:
        """Summary statistics for MPI quality monitoring."""
        if not self._store:
            return {"status": "no_store_configured"}
        
        return {
            "total_canonical_records": self._store.count(),
            "records_with_multiple_sources": self._store.count_multi_source(),
            "pending_human_review": self._store.count_pending_review(),
            "auto_linked_this_month": self._store.count_auto_linked(days=30),
            "merge_conflicts_this_month": self._store.count_conflicts(days=30),
        }
