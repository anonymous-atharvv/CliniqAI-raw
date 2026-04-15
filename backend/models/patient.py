"""
Database Models — SQLAlchemy 2.0 Async
All models use UUID primary keys.
PHI fields are encrypted at the application layer before storage.
TimescaleDB hypertables for time-series data (vitals, predictions, audit).
"""

from sqlalchemy import (
    Column, String, Boolean, Integer, SmallInteger, Numeric, Date,
    DateTime, Text, JSON, ARRAY, ForeignKey, Index, UniqueConstraint,
    CheckConstraint, event
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, BYTEA, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship, validates
from sqlalchemy.sql import func
from datetime import datetime, timezone
import uuid


class Base(DeclarativeBase):
    pass


def utcnow():
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# PATIENT MODEL (PHI Schema)
# ─────────────────────────────────────────────

class Patient(Base):
    """
    Canonical patient identity.
    PHI fields stored as BYTEA (encrypted with AES-256-GCM via KMS).
    De-identified ID used in all AI/analytics tables.
    """
    __tablename__ = "patients"
    __table_args__ = (
        Index("idx_patients_hospital", "hospital_id"),
        Index("idx_patients_deidentified", "deidentified_id"),
        Index("idx_patients_birth_year", "birth_year"),
        CheckConstraint("data_quality_score BETWEEN 0 AND 1", name="chk_quality"),
        {"schema": "cliniqai_phi"},
    )

    # Primary key
    patient_id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(64), nullable=False)

    # PHI — stored encrypted (BYTEA = pgp_sym_encrypt output)
    first_name_enc = Column(BYTEA, nullable=True)
    last_name_enc  = Column(BYTEA, nullable=True)
    dob_enc        = Column(BYTEA, nullable=True)    # Full DOB encrypted
    ssn_last4_enc  = Column(BYTEA, nullable=True)
    phone_enc      = Column(BYTEA, nullable=True)
    email_enc      = Column(BYTEA, nullable=True)
    address_enc    = Column(BYTEA, nullable=True)    # JSON address object, encrypted

    # Non-PHI (Safe Harbor preservable)
    birth_year     = Column(SmallInteger, nullable=True)
    gender         = Column(String(1), nullable=True)       # M/F/O/U
    state_code     = Column(String(2), nullable=True)       # State only
    zip_prefix     = Column(String(3), nullable=True)       # First 3 digits
    ethnicity      = Column(String(64), nullable=True)      # For bias monitoring

    # De-identified reference (used everywhere in AI layer)
    deidentified_id = Column(PG_UUID(as_uuid=True), nullable=False, unique=True, default=uuid.uuid4)

    # Status flags
    is_active      = Column(Boolean, nullable=False, default=True)
    is_deceased    = Column(Boolean, nullable=False, default=False)
    deceased_date  = Column(Date, nullable=True)

    # Metadata
    created_at     = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at     = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    created_by     = Column(PG_UUID(as_uuid=True), nullable=True)
    data_quality_score = Column(Numeric(4, 3), nullable=True)

    # Relationships
    encounters     = relationship("Encounter", back_populates="patient", lazy="dynamic")
    medications    = relationship("Medication", back_populates="patient", lazy="dynamic")
    allergies      = relationship("Allergy", back_populates="patient", lazy="dynamic")

    def __repr__(self):
        return f"<Patient deident_id={self.deidentified_id} hospital={self.hospital_id}>"

    @property
    def is_high_quality(self) -> bool:
        return (self.data_quality_score or 0) >= 0.60


# ─────────────────────────────────────────────
# ENCOUNTER MODEL
# ─────────────────────────────────────────────

class Encounter(Base):
    """Hospital admission / encounter."""
    __tablename__ = "encounters"
    __table_args__ = (
        Index("idx_encounters_patient", "patient_id"),
        Index("idx_encounters_hospital", "hospital_id"),
        Index("idx_encounters_active", "status", postgresql_where="status = 'active'"),
        Index("idx_encounters_admission", "admission_datetime"),
        Index("idx_encounters_icd10", "primary_icd10"),
        CheckConstraint(
            "discharge_datetime IS NULL OR discharge_datetime >= admission_datetime",
            name="chk_encounter_dates"
        ),
        {"schema": "cliniqai_phi"},
    )

    encounter_id      = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id        = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.patients.patient_id"), nullable=False)
    hospital_id       = Column(String(64), nullable=False)

    # Encounter context
    encounter_type    = Column(String(32), nullable=False)       # inpatient|outpatient|emergency|icu
    admission_datetime = Column(DateTime(timezone=True), nullable=False)
    discharge_datetime = Column(DateTime(timezone=True), nullable=True)

    # Location
    ward_code         = Column(String(32), nullable=True)
    bed_id            = Column(String(32), nullable=True)
    unit_type         = Column(String(32), nullable=True)         # icu|ward|ed|step_down

    # Clinical
    chief_complaint   = Column(Text, nullable=True)
    admission_type    = Column(String(32), nullable=True)         # emergency|elective|urgent|transfer

    # Diagnoses (assigned at discharge)
    primary_icd10     = Column(String(10), nullable=True)
    secondary_icd10   = Column(ARRAY(String(10)), nullable=True)
    drg_code          = Column(String(8), nullable=True)
    drg_name          = Column(String(256), nullable=True)

    # Providers
    attending_id      = Column(PG_UUID(as_uuid=True), nullable=True)
    admitting_id      = Column(PG_UUID(as_uuid=True), nullable=True)

    # Status
    status            = Column(String(32), nullable=False, default="active")
    discharge_disposition = Column(String(64), nullable=True)    # home|snf|rehab|expired

    # Financial / LOS
    expected_los_days = Column(Numeric(5, 2), nullable=True)

    # Metadata
    created_at        = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at        = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    # Relationships
    patient           = relationship("Patient", back_populates="encounters")
    medications       = relationship("Medication", back_populates="encounter", lazy="dynamic")

    @property
    def actual_los_days(self) -> float | None:
        if self.discharge_datetime and self.admission_datetime:
            delta = self.discharge_datetime - self.admission_datetime
            return delta.total_seconds() / 86400
        return None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


# ─────────────────────────────────────────────
# OBSERVATION MODEL (Vitals + Labs)
# ─────────────────────────────────────────────

class Observation(Base):
    """
    FHIR R4 Observation — vitals and laboratory results.
    
    NOTE: For ICU vitals at 1Hz, the TimescaleDB hypertable
    `cliniqai_ai.vitals_timeseries` is used directly for performance.
    This model stores FHIR-normalized observations for the clinical record.
    """
    __tablename__ = "observations"
    __table_args__ = (
        Index("idx_obs_patient", "patient_id"),
        Index("idx_obs_encounter", "encounter_id"),
        Index("idx_obs_parameter", "parameter", "effective_datetime"),
        Index("idx_obs_category", "category"),
        {"schema": "cliniqai_phi"},
    )

    observation_id    = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id        = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.patients.patient_id"), nullable=False)
    encounter_id      = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.encounters.encounter_id"), nullable=True)

    # FHIR fields
    fhir_resource_id  = Column(PG_UUID(as_uuid=True), nullable=True, unique=True)
    status            = Column(String(32), nullable=False, default="final")
    category          = Column(String(64), nullable=False)       # vital-signs|laboratory
    loinc_code        = Column(String(20), nullable=True)
    loinc_display     = Column(String(256), nullable=True)
    parameter         = Column(String(64), nullable=False)       # Internal name: heart_rate, etc.
    effective_datetime = Column(DateTime(timezone=True), nullable=False)

    # Value
    value_quantity    = Column(Numeric(12, 4), nullable=True)
    value_unit        = Column(String(32), nullable=True)
    value_string      = Column(String(512), nullable=True)       # For text observations
    value_code        = Column(String(64), nullable=True)        # For coded observations

    # Reference range
    ref_low           = Column(Numeric(12, 4), nullable=True)
    ref_high          = Column(Numeric(12, 4), nullable=True)
    interpretation    = Column(String(8), nullable=True)         # L|H|LL|HH|N

    # Clinical flags
    is_critical       = Column(Boolean, nullable=False, default=False)
    is_anomaly        = Column(Boolean, nullable=False, default=False)
    anomaly_sigma     = Column(Numeric(6, 2), nullable=True)

    # Source
    device_id         = Column(String(64), nullable=True)
    source_system     = Column(String(64), nullable=False, default="EHR")

    # Quality
    quality_score     = Column(Numeric(4, 3), nullable=True)
    is_artifact       = Column(Boolean, nullable=False, default=False)

    # Lineage
    created_at        = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    ingested_at       = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    pipeline_version  = Column(String(32), nullable=True)


# ─────────────────────────────────────────────
# MEDICATION MODEL
# ─────────────────────────────────────────────

class Medication(Base):
    """Active and historical medication orders."""
    __tablename__ = "medications"
    __table_args__ = (
        Index("idx_meds_patient", "patient_id"),
        Index("idx_meds_encounter", "encounter_id"),
        Index("idx_meds_active", "status", postgresql_where="status = 'active'"),
        Index("idx_meds_rxnorm", "rxnorm_code", postgresql_where="rxnorm_code IS NOT NULL"),
        {"schema": "cliniqai_phi"},
    )

    medication_id   = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id      = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.patients.patient_id"), nullable=False)
    encounter_id    = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.encounters.encounter_id"), nullable=True)

    # Drug identification
    medication_name = Column(String(256), nullable=False)
    rxnorm_code     = Column(String(20), nullable=True)
    ndc_code        = Column(String(20), nullable=True)
    generic_name    = Column(String(256), nullable=True)

    # Dosage
    dose_value      = Column(Numeric(10, 3), nullable=True)
    dose_unit       = Column(String(32), nullable=True)
    route           = Column(String(64), nullable=True)
    frequency       = Column(String(64), nullable=True)

    # Status
    status          = Column(String(32), nullable=False, default="active")
    start_datetime  = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    end_datetime    = Column(DateTime(timezone=True), nullable=True)

    # Ordering
    ordered_by      = Column(PG_UUID(as_uuid=True), nullable=True)
    ordered_at      = Column(DateTime(timezone=True), nullable=True)

    # FHIR link
    fhir_resource_id = Column(PG_UUID(as_uuid=True), nullable=True)

    # Metadata
    created_at      = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at      = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    # Relationships
    patient         = relationship("Patient", back_populates="medications")
    encounter       = relationship("Encounter", back_populates="medications")

    @property
    def is_renally_cleared(self) -> bool:
        """Flag medications that require renal dose adjustment."""
        renally_cleared = [
            "vancomycin", "gentamicin", "metformin", "digoxin",
            "lisinopril", "penicillin", "cephalexin", "enoxaparin",
        ]
        return any(drug in self.medication_name.lower() for drug in renally_cleared)


# ─────────────────────────────────────────────
# ALLERGY MODEL
# ─────────────────────────────────────────────

class Allergy(Base):
    """Patient allergy and adverse reaction records."""
    __tablename__ = "allergies"
    __table_args__ = (
        Index("idx_allergies_patient", "patient_id"),
        {"schema": "cliniqai_phi"},
    )

    allergy_id     = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id     = Column(PG_UUID(as_uuid=True), ForeignKey("cliniqai_phi.patients.patient_id"), nullable=False)

    allergen       = Column(String(256), nullable=False)
    allergen_type  = Column(String(64), nullable=True)       # drug|food|environmental|other
    reaction       = Column(String(256), nullable=True)
    severity       = Column(String(32), nullable=True)       # mild|moderate|severe|life_threatening
    status         = Column(String(32), nullable=False, default="active")

    recorded_at    = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    recorded_by    = Column(PG_UUID(as_uuid=True), nullable=True)

    # Relationship
    patient        = relationship("Patient", back_populates="allergies")

    @property
    def is_drug_allergy(self) -> bool:
        return self.allergen_type == "drug"

    @property
    def is_life_threatening(self) -> bool:
        return self.severity == "life_threatening"


# ─────────────────────────────────────────────
# AUDIT LOG MODEL (Append-only)
# ─────────────────────────────────────────────

class AuditLog(Base):
    """
    HIPAA-required immutable audit log.
    APPEND ONLY — never update or delete.
    Synced to S3 WORM bucket for long-term retention.
    Retained 6 years minimum (HIPAA 45 CFR §164.312(b)).
    """
    __tablename__ = "access_log"
    __table_args__ = (
        Index("idx_audit_actor", "actor", "event_timestamp"),
        Index("idx_audit_resource", "resource_type", "resource_id"),
        Index("idx_audit_phi", "event_timestamp", postgresql_where="phi_accessed = TRUE"),
        Index("idx_audit_denied", "event_timestamp", postgresql_where="outcome = 'denied'"),
        {"schema": "cliniqai_audit"},
    )

    event_id          = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_timestamp   = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    # Actor
    actor             = Column(String(128), nullable=False)    # User UUID or system ID
    actor_role        = Column(String(32), nullable=False)
    actor_department  = Column(String(64), nullable=True)
    session_id        = Column(String(128), nullable=True)

    # Action
    action            = Column(String(32), nullable=False)     # read|write|infer|export|delete
    resource_type     = Column(String(64), nullable=False)
    resource_id       = Column(String(128), nullable=False)    # De-identified ID only
    access_reason     = Column(String(32), nullable=True)      # treatment|operations|research
    api_endpoint      = Column(String(256), nullable=True)

    # Outcome
    outcome           = Column(String(16), nullable=False)     # success|denied
    denial_reason     = Column(Text, nullable=True)
    http_status       = Column(SmallInteger, nullable=True)
    duration_ms       = Column(Integer, nullable=True)

    # Request context (hashed for privacy)
    ip_hash           = Column(String(64), nullable=False)
    user_agent_hash   = Column(String(64), nullable=True)
    request_id        = Column(String(128), nullable=True)

    # HIPAA fields
    phi_accessed      = Column(Boolean, nullable=False, default=False)
    deidentified_data = Column(Boolean, nullable=False, default=False)
    data_sensitivity  = Column(String(32), nullable=True)      # standard|sensitive


# ─────────────────────────────────────────────
# FEEDBACK MODEL (AI Learning)
# ─────────────────────────────────────────────

class Feedback(Base):
    """
    Physician feedback on AI recommendations.
    The primary data source for model improvement (your moat).
    """
    __tablename__ = "feedback"
    __table_args__ = (
        Index("idx_feedback_patient", "patient_deident_id", "feedback_at"),
        Index("idx_feedback_training", "feedback_at", postgresql_where="is_valid_for_training = TRUE"),
        Index("idx_feedback_unlinked", "feedback_at",
              postgresql_where="outcome_linked = FALSE"),
        {"schema": "cliniqai_ai"},
    )

    feedback_id         = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Context
    patient_deident_id  = Column(PG_UUID(as_uuid=True), nullable=False)
    encounter_id        = Column(PG_UUID(as_uuid=True), nullable=False)
    ai_recommendation_id = Column(PG_UUID(as_uuid=True), nullable=True)
    ai_output_type      = Column(String(64), nullable=False)    # risk_alert|differential|action|drug_alert

    # The AI output being rated
    ai_prediction       = Column(JSONB, nullable=False)

    # Signal
    signal              = Column(String(32), nullable=False)    # accepted|modified|rejected|thumbs_up|thumbs_down
    ml_signal           = Column(Numeric(4, 3), nullable=True)  # -1.0 to +1.0

    # Actor
    actor_role          = Column(String(32), nullable=False)
    actor_department    = Column(String(64), nullable=True)
    is_treating_physician = Column(Boolean, nullable=False, default=True)

    # Details
    modification_details = Column(JSONB, nullable=True)
    free_text_reason    = Column(Text, nullable=True)

    # Quality filtering
    is_in_distribution  = Column(Boolean, nullable=False, default=True)
    is_valid_for_training = Column(Boolean, nullable=False, default=False)

    # Outcome linkage (async, filled weeks later)
    outcome_linked      = Column(Boolean, nullable=False, default=False)
    outcome_type        = Column(String(64), nullable=True)
    outcome_occurred    = Column(Boolean, nullable=True)
    outcome_linked_at   = Column(DateTime(timezone=True), nullable=True)

    # Timing
    feedback_at         = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def compute_ml_signal(self) -> float:
        """Convert feedback signal to ML training value."""
        signal_map = {
            "accepted": 1.0, "thumbs_up": 1.0,
            "modified": 0.5, "rejected": -0.5, "thumbs_down": -1.0,
        }
        base = signal_map.get(self.signal, 0.0)
        if self.outcome_linked and self.outcome_occurred is not None:
            if self.outcome_occurred and base > 0:
                return min(1.0, base * 1.3)
            elif not self.outcome_occurred and base < 0:
                return max(-1.0, base * 1.3)
        return base
