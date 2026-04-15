-- ============================================================
-- CliniQAI Database Schema
-- PostgreSQL 16 + TimescaleDB Extension
-- 
-- Design principles:
-- 1. PHI stored encrypted at rest (KMS-managed keys)
-- 2. De-identified IDs used in AI/analytics tables
-- 3. Audit trail on every sensitive table
-- 4. TimescaleDB hypertables for time-series vitals
-- 5. Row-level security enforced via ABAC
-- 6. Soft deletes only (never hard delete PHI)
-- ============================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "timescaledb";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";     -- For fuzzy name search in MPI

-- ============================================================
-- SCHEMA LAYOUT
-- cliniqai_phi    : Protected Health Information (encrypted)
-- cliniqai_ai     : De-identified data for AI processing
-- cliniqai_ops    : Operational data (no PHI)
-- cliniqai_audit  : Immutable audit logs
-- cliniqai_mpi    : Master Patient Index
-- ============================================================

CREATE SCHEMA IF NOT EXISTS cliniqai_phi;
CREATE SCHEMA IF NOT EXISTS cliniqai_ai;
CREATE SCHEMA IF NOT EXISTS cliniqai_ops;
CREATE SCHEMA IF NOT EXISTS cliniqai_audit;
CREATE SCHEMA IF NOT EXISTS cliniqai_mpi;

-- ============================================================
-- PHI SCHEMA — Protected Health Information
-- Access: treating_physician, nurse (assigned patients only)
-- De-identified before AI processing
-- ============================================================

-- Canonical patient record (PHI — encrypted at rest)
CREATE TABLE cliniqai_phi.patients (
    patient_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    hospital_id         VARCHAR(64) NOT NULL,
    
    -- Encrypted PHI fields (AES-256-GCM via pgp_sym_encrypt)
    -- In production: encrypt using KMS-managed key, not passphrase
    first_name_enc      BYTEA,           -- pgp_sym_encrypt(first_name, kms_key)
    last_name_enc       BYTEA,
    dob_enc             BYTEA,           -- Date of birth
    ssn_last4_enc       BYTEA,           -- Last 4 of SSN
    phone_enc           BYTEA,
    email_enc           BYTEA,
    address_enc         BYTEA,           -- JSON: {line1, city, state, zip}
    
    -- Non-PHI fields (stored plaintext — Safe Harbor preserves these)
    birth_year          SMALLINT,        -- Year only (DOB year extracted)
    gender              CHAR(1),         -- M/F/O/U
    state_code          CHAR(2),         -- State-level geography (Safe Harbor OK)
    zip_prefix          CHAR(3),         -- First 3 digits of ZIP
    ethnicity           VARCHAR(64),     -- For bias monitoring
    
    -- De-identified reference (used everywhere in AI layer)
    deidentified_id     UUID NOT NULL UNIQUE DEFAULT uuid_generate_v4(),
    
    -- Status
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    is_deceased         BOOLEAN NOT NULL DEFAULT FALSE,
    deceased_date       DATE,
    
    -- Metadata
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by          UUID,            -- User who created record
    data_quality_score  NUMERIC(4,3),    -- 0.000 to 1.000
    
    CONSTRAINT chk_quality CHECK (data_quality_score BETWEEN 0 AND 1)
);

CREATE INDEX idx_patients_hospital ON cliniqai_phi.patients(hospital_id);
CREATE INDEX idx_patients_deidentified ON cliniqai_phi.patients(deidentified_id);
CREATE INDEX idx_patients_birth_year ON cliniqai_phi.patients(birth_year);

-- Trigger: auto-update updated_at
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_patients_updated
    BEFORE UPDATE ON cliniqai_phi.patients
    FOR EACH ROW EXECUTE FUNCTION update_timestamp();

-- ─────────────────────────────────────────────
-- Encounters (Hospital Admissions)
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_phi.encounters (
    encounter_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id          UUID NOT NULL REFERENCES cliniqai_phi.patients(patient_id),
    hospital_id         VARCHAR(64) NOT NULL,
    
    -- Encounter details
    encounter_type      VARCHAR(32) NOT NULL,  -- inpatient|outpatient|emergency|icu
    admission_datetime  TIMESTAMPTZ NOT NULL,
    discharge_datetime  TIMESTAMPTZ,
    
    -- Location
    ward_code           VARCHAR(32),
    bed_id              VARCHAR(32),
    unit_type           VARCHAR(32),           -- icu|ward|ed|step_down
    
    -- Clinical
    chief_complaint     TEXT,
    admission_type      VARCHAR(32),           -- emergency|elective|urgent|transfer
    
    -- ICD-10 Codes (assigned at discharge)
    primary_icd10       VARCHAR(10),
    secondary_icd10     VARCHAR(10)[],
    drg_code            VARCHAR(8),
    drg_name            VARCHAR(256),
    
    -- Providers
    attending_id        UUID,
    admitting_id        UUID,
    
    -- Status
    status              VARCHAR(32) NOT NULL DEFAULT 'active',  -- active|discharged|transferred
    discharge_disposition VARCHAR(64),        -- home|snf|rehab|expired
    
    -- Financial
    expected_los_days   NUMERIC(5,2),
    actual_los_days     NUMERIC(5,2) GENERATED ALWAYS AS (
        EXTRACT(EPOCH FROM (discharge_datetime - admission_datetime)) / 86400
    ) STORED,
    
    -- Metadata
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    CONSTRAINT chk_encounter_dates CHECK (
        discharge_datetime IS NULL OR discharge_datetime >= admission_datetime
    )
);

CREATE INDEX idx_encounters_patient ON cliniqai_phi.encounters(patient_id);
CREATE INDEX idx_encounters_hospital ON cliniqai_phi.encounters(hospital_id);
CREATE INDEX idx_encounters_status ON cliniqai_phi.encounters(status) WHERE status = 'active';
CREATE INDEX idx_encounters_admission ON cliniqai_phi.encounters(admission_datetime DESC);
CREATE INDEX idx_encounters_icd10 ON cliniqai_phi.encounters(primary_icd10);

-- ─────────────────────────────────────────────
-- Medications
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_phi.medications (
    medication_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id          UUID NOT NULL REFERENCES cliniqai_phi.patients(patient_id),
    encounter_id        UUID REFERENCES cliniqai_phi.encounters(encounter_id),
    
    -- Drug identification
    medication_name     VARCHAR(256) NOT NULL,
    rxnorm_code         VARCHAR(20),          -- RxNorm concept ID
    ndc_code            VARCHAR(20),          -- National Drug Code
    
    -- Dosage
    dose_value          NUMERIC(10,3),
    dose_unit           VARCHAR(32),
    route               VARCHAR(64),
    frequency           VARCHAR(64),
    
    -- Status
    status              VARCHAR(32) NOT NULL DEFAULT 'active',
    start_datetime      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    end_datetime        TIMESTAMPTZ,
    
    -- Order
    ordered_by          UUID,
    ordered_at          TIMESTAMPTZ,
    
    -- Metadata
    fhir_resource_id    UUID,               -- Link to FHIR MedicationRequest
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_medications_patient ON cliniqai_phi.medications(patient_id);
CREATE INDEX idx_medications_encounter ON cliniqai_phi.medications(encounter_id);
CREATE INDEX idx_medications_status ON cliniqai_phi.medications(status) WHERE status = 'active';
CREATE INDEX idx_medications_rxnorm ON cliniqai_phi.medications(rxnorm_code) WHERE rxnorm_code IS NOT NULL;

-- ─────────────────────────────────────────────
-- Allergies
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_phi.allergies (
    allergy_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_id          UUID NOT NULL REFERENCES cliniqai_phi.patients(patient_id),
    
    allergen            VARCHAR(256) NOT NULL,
    allergen_type       VARCHAR(64),          -- drug|food|environmental|other
    reaction            VARCHAR(256),
    severity            VARCHAR(32),          -- mild|moderate|severe|life_threatening
    status              VARCHAR(32) DEFAULT 'active',
    
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    recorded_by         UUID
);

CREATE INDEX idx_allergies_patient ON cliniqai_phi.allergies(patient_id);


-- ============================================================
-- AI SCHEMA — De-identified data for AI processing
-- NO PHI. All patient references are de-identified UUIDs.
-- ============================================================

-- ─────────────────────────────────────────────
-- ICU VITALS TIME-SERIES (TimescaleDB Hypertable)
-- 1Hz per device = ~86,400 readings/device/day
-- For 100 ICU devices: 8.64M rows/day
-- TimescaleDB chunks by time (1-day chunks) for performance
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_ai.vitals_timeseries (
    time                TIMESTAMPTZ NOT NULL,
    patient_deident_id  UUID NOT NULL,
    encounter_id        UUID NOT NULL,
    
    -- Vital parameters (use LOINC code as column label where possible)
    parameter           VARCHAR(64) NOT NULL,    -- LOINC-mapped: heart_rate, spo2_pulse_ox, etc.
    value               NUMERIC(10,3) NOT NULL,
    unit                VARCHAR(32) NOT NULL,
    
    -- Quality
    is_artifact         BOOLEAN NOT NULL DEFAULT FALSE,
    quality_score       NUMERIC(4,3) DEFAULT 1.0,
    
    -- Source
    device_id           VARCHAR(64),
    source_system       VARCHAR(64) NOT NULL DEFAULT 'icu_monitor',
    
    -- Clinical flags
    is_critical_low     BOOLEAN NOT NULL DEFAULT FALSE,
    is_critical_high    BOOLEAN NOT NULL DEFAULT FALSE,
    is_anomaly          BOOLEAN NOT NULL DEFAULT FALSE,
    anomaly_sigma       NUMERIC(6,2),           -- Standard deviations from baseline
    
    CONSTRAINT chk_vitals_quality CHECK (quality_score BETWEEN 0 AND 1)
);

-- Convert to TimescaleDB hypertable (partitioned by time)
SELECT create_hypertable(
    'cliniqai_ai.vitals_timeseries',
    'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Compression: compress chunks older than 7 days (vitals compress ~10x)
SELECT add_compression_policy('cliniqai_ai.vitals_timeseries', INTERVAL '7 days');

-- Retention: keep 90 days in hot path (older → S3 Parquet via Airflow)
SELECT add_retention_policy('cliniqai_ai.vitals_timeseries', INTERVAL '90 days');

-- Indexes
CREATE INDEX idx_vitals_patient_time ON cliniqai_ai.vitals_timeseries(patient_deident_id, time DESC);
CREATE INDEX idx_vitals_parameter_time ON cliniqai_ai.vitals_timeseries(parameter, time DESC);
CREATE INDEX idx_vitals_anomalies ON cliniqai_ai.vitals_timeseries(time DESC) WHERE is_anomaly = TRUE;

-- ─────────────────────────────────────────────
-- AI Predictions — stored per patient per run
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_ai.predictions (
    prediction_id       UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_deident_id  UUID NOT NULL,
    encounter_id        UUID NOT NULL,
    
    -- Timing
    predicted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    prediction_horizon  VARCHAR(32) NOT NULL,    -- 6h|12h|24h|72h
    
    -- Prediction type
    prediction_type     VARCHAR(64) NOT NULL,    -- deterioration|sepsis|mortality|readmission|los
    
    -- Scores
    probability         NUMERIC(6,4) NOT NULL,   -- 0.0000 to 1.0000
    uncertainty         NUMERIC(6,4),            -- Epistemic uncertainty from MC Dropout
    confidence_level    VARCHAR(8),              -- HIGH|MEDIUM|LOW
    
    -- Clinical scores at time of prediction
    news2_score         SMALLINT,
    sofa_score          SMALLINT,
    mews_score          SMALLINT,
    
    -- Risk level
    risk_level          VARCHAR(16) NOT NULL,    -- CRITICAL|HIGH|MEDIUM|LOW
    
    -- Model metadata
    model_version       VARCHAR(64) NOT NULL,
    model_type          VARCHAR(64) NOT NULL,    -- tft_vitals|llm_reasoning|imaging
    input_features      JSONB,                  -- Feature importance / context used
    
    -- Outcome linkage (filled async when outcome known)
    outcome_occurred    BOOLEAN,
    outcome_date        DATE,
    prediction_correct  BOOLEAN,
    validated_at        TIMESTAMPTZ,
    
    -- Alert generated
    alert_generated     BOOLEAN NOT NULL DEFAULT FALSE,
    alert_acknowledged  BOOLEAN NOT NULL DEFAULT FALSE,
    acknowledged_at     TIMESTAMPTZ,
    acknowledged_by     UUID,
    
    CONSTRAINT chk_probability CHECK (probability BETWEEN 0 AND 1),
    CONSTRAINT chk_uncertainty CHECK (uncertainty IS NULL OR uncertainty BETWEEN 0 AND 1)
);

SELECT create_hypertable(
    'cliniqai_ai.predictions',
    'predicted_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_predictions_patient ON cliniqai_ai.predictions(patient_deident_id, predicted_at DESC);
CREATE INDEX idx_predictions_type ON cliniqai_ai.predictions(prediction_type, predicted_at DESC);
CREATE INDEX idx_predictions_unvalidated ON cliniqai_ai.predictions(predicted_at)
    WHERE outcome_occurred IS NULL AND predicted_at < NOW() - INTERVAL '48 hours';

-- ─────────────────────────────────────────────
-- Agent Session State
-- Snapshot of multi-agent reasoning per patient
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_ai.agent_sessions (
    session_id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_deident_id  UUID NOT NULL,
    encounter_id        UUID NOT NULL,
    
    session_start       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    session_end         TIMESTAMPTZ,
    
    -- Agent outputs (JSONB for schema flexibility as agents evolve)
    triage_output       JSONB,
    risk_output         JSONB,
    diagnosis_output    JSONB,
    pharmacist_output   JSONB,
    documentation_output JSONB,
    coordinator_output  JSONB,
    escalation_output   JSONB,
    
    -- Summary
    final_risk_level    VARCHAR(16),
    coordinator_confidence NUMERIC(4,3),
    human_review_required BOOLEAN NOT NULL DEFAULT FALSE,
    human_review_reason TEXT,
    
    -- Escalations
    escalations_sent    SMALLINT DEFAULT 0,
    escalations_acked   SMALLINT DEFAULT 0,
    
    -- Performance metrics
    total_processing_ms INTEGER,
    agents_timed_out    VARCHAR(64)[],
    agents_failed       VARCHAR(64)[]
);

SELECT create_hypertable(
    'cliniqai_ai.agent_sessions',
    'session_start',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_agent_sessions_patient ON cliniqai_ai.agent_sessions(patient_deident_id, session_start DESC);

-- ─────────────────────────────────────────────
-- LLM Reasoning Log
-- Full reasoning trace for explainability and audit
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_ai.reasoning_log (
    reasoning_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_deident_id  UUID NOT NULL,
    session_id          UUID REFERENCES cliniqai_ai.agent_sessions(session_id),
    
    reasoned_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Input context (de-identified)
    context_token_count INTEGER,
    context_summary     TEXT,        -- Human-readable summary of what was fed in
    
    -- Output
    patient_state_summary TEXT,
    risk_level          VARCHAR(16),
    differentials       JSONB,       -- Array of differential diagnoses
    recommended_actions JSONB,       -- Array of recommended actions
    data_gaps           TEXT[],
    overall_confidence  VARCHAR(8),
    
    -- Model metadata
    model_name          VARCHAR(64) NOT NULL,
    model_version       VARCHAR(32),
    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    latency_ms          INTEGER,
    retry_count         SMALLINT DEFAULT 0,
    
    -- Validation
    output_valid        BOOLEAN NOT NULL DEFAULT TRUE,
    validation_errors   TEXT[]
);

SELECT create_hypertable(
    'cliniqai_ai.reasoning_log',
    'reasoned_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

-- ─────────────────────────────────────────────
-- Patient Baseline (Vital Sign Reference)
-- ─────────────────────────────────────────────
CREATE TABLE cliniqai_ai.patient_baselines (
    baseline_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    patient_deident_id  UUID NOT NULL,
    encounter_id        UUID NOT NULL,
    
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    observation_hours   INTEGER NOT NULL,
    is_mature           BOOLEAN GENERATED ALWAYS AS (observation_hours >= 24) STORED,
    
    -- Baseline statistics per vital parameter (JSONB)
    -- { "heart_rate": {"mean": 82.3, "std": 8.1, "median": 81, "p10": 72, "p90": 93, "n": 1440} }
    baselines           JSONB NOT NULL,
    
    UNIQUE(patient_deident_id, encounter_id)
);

CREATE INDEX idx_baselines_patient ON cliniqai_ai.patient_baselines(patient_deident_id);


-- ============================================================
-- FEEDBACK SCHEMA
-- ============================================================

CREATE TABLE cliniqai_ai.feedback (
    feedback_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Context
    patient_deident_id  UUID NOT NULL,
    encounter_id        UUID NOT NULL,
    ai_recommendation_id UUID,
    ai_output_type      VARCHAR(64) NOT NULL,   -- risk_alert|differential|action|drug_alert
    
    -- The AI output being rated (de-identified snapshot)
    ai_prediction       JSONB NOT NULL,
    
    -- Signal
    signal              VARCHAR(32) NOT NULL,   -- accepted|modified|rejected|thumbs_up|thumbs_down
    ml_signal           NUMERIC(4,3),           -- Computed: -1.0 to 1.0
    
    -- Actor (anonymized — department-level, not individual for privacy)
    actor_role          VARCHAR(32) NOT NULL,
    actor_department    VARCHAR(64),
    is_treating_physician BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Details
    modification_details JSONB,
    free_text_reason    TEXT,
    
    -- Quality filtering
    is_in_distribution  BOOLEAN NOT NULL DEFAULT TRUE,
    is_valid_for_training BOOLEAN GENERATED ALWAYS AS (
        is_treating_physician AND is_in_distribution AND
        NOT (signal = 'thumbs_down' AND free_text_reason IS NULL)
    ) STORED,
    
    -- Outcome linkage (async, filled weeks later)
    outcome_linked      BOOLEAN NOT NULL DEFAULT FALSE,
    outcome_type        VARCHAR(64),
    outcome_occurred    BOOLEAN,
    outcome_linked_at   TIMESTAMPTZ,
    
    -- Timing
    feedback_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

SELECT create_hypertable(
    'cliniqai_ai.feedback',
    'feedback_at',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX idx_feedback_patient ON cliniqai_ai.feedback(patient_deident_id, feedback_at DESC);
CREATE INDEX idx_feedback_training ON cliniqai_ai.feedback(feedback_at) WHERE is_valid_for_training = TRUE;
CREATE INDEX idx_feedback_unlinked ON cliniqai_ai.feedback(feedback_at)
    WHERE outcome_linked = FALSE AND feedback_at < NOW() - INTERVAL '30 days';


-- ============================================================
-- AUDIT SCHEMA — Immutable. Never delete. WORM semantics.
-- HIPAA requires 6-year retention minimum.
-- In production: this table is also replicated to S3 WORM bucket.
-- ============================================================

CREATE TABLE cliniqai_audit.access_log (
    event_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_timestamp     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Actor
    actor_id            VARCHAR(128) NOT NULL,  -- User or system ID
    actor_role          VARCHAR(32) NOT NULL,
    actor_department    VARCHAR(64),
    session_id          VARCHAR(128),
    
    -- Action
    action              VARCHAR(32) NOT NULL,   -- read|write|infer|export|delete
    resource_type       VARCHAR(64) NOT NULL,   -- Patient|Observation|MedicationRequest|etc.
    resource_id         VARCHAR(128) NOT NULL,  -- De-identified resource ID
    access_reason       VARCHAR(32) NOT NULL,   -- treatment|operations|research|payment
    
    -- Outcome
    outcome             VARCHAR(16) NOT NULL,   -- success|denied
    denial_reason       TEXT,
    
    -- Request context
    ip_hash             VARCHAR(64) NOT NULL,   -- SHA-256 hash of IP
    user_agent_hash     VARCHAR(64),
    request_id          VARCHAR(128),
    api_endpoint        VARCHAR(256),
    
    -- HIPAA fields
    data_sensitivity    VARCHAR(32),            -- standard|sensitive
    phi_accessed        BOOLEAN NOT NULL DEFAULT FALSE,
    deidentified_data   BOOLEAN NOT NULL DEFAULT FALSE
);

-- TimescaleDB for audit log (fast range queries on timestamp)
SELECT create_hypertable(
    'cliniqai_audit.access_log',
    'event_timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- Retention: HIPAA minimum 6 years
-- In production: retention policy keeps in DB 1 year, archives older to S3 WORM
SELECT add_retention_policy('cliniqai_audit.access_log', INTERVAL '6 years');

CREATE INDEX idx_audit_actor ON cliniqai_audit.access_log(actor_id, event_timestamp DESC);
CREATE INDEX idx_audit_resource ON cliniqai_audit.access_log(resource_type, resource_id);
CREATE INDEX idx_audit_phi ON cliniqai_audit.access_log(event_timestamp DESC) WHERE phi_accessed = TRUE;
CREATE INDEX idx_audit_denied ON cliniqai_audit.access_log(event_timestamp DESC) WHERE outcome = 'denied';

-- IMPORTANT: No UPDATE or DELETE allowed on audit log
-- Enforce via row security + database role (audit_writer = INSERT only)
CREATE ROLE audit_writer;
REVOKE ALL ON cliniqai_audit.access_log FROM audit_writer;
GRANT INSERT ON cliniqai_audit.access_log TO audit_writer;

-- Breach detection view (high-volume access by single actor)
CREATE VIEW cliniqai_audit.breach_candidates AS
SELECT
    actor_id,
    actor_department,
    DATE_TRUNC('hour', event_timestamp) AS hour_bucket,
    COUNT(*) AS access_count,
    COUNT(DISTINCT resource_id) AS distinct_patients,
    SUM(CASE WHEN phi_accessed THEN 1 ELSE 0 END) AS phi_access_count
FROM cliniqai_audit.access_log
WHERE event_timestamp > NOW() - INTERVAL '2 hours'
  AND outcome = 'success'
GROUP BY actor_id, actor_department, hour_bucket
HAVING COUNT(DISTINCT resource_id) > 30   -- > 30 distinct patients/hour = flag
ORDER BY access_count DESC;


-- ============================================================
-- MPI SCHEMA — Master Patient Index
-- ============================================================

CREATE TABLE cliniqai_mpi.canonical_patients (
    global_patient_id   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Hashed identifiers (HMAC-SHA256 with per-deployment salt)
    name_hash           VARCHAR(128) NOT NULL,  -- LN_hash|FN_hash|MN_hash
    dob                 DATE,                   -- Stored encrypted in production
    ssn_last4_hash      VARCHAR(64),
    
    -- Non-sensitive fields
    gender              CHAR(1),
    zip_prefix          CHAR(3),
    birth_year          SMALLINT,
    
    -- Source system links
    mrn_list            TEXT[] NOT NULL DEFAULT '{}',  -- ["epic:12345", "cerner:67890"]
    source_systems      TEXT[] NOT NULL DEFAULT '{}',
    
    -- Match history
    confidence_history  JSONB DEFAULT '[]',
    merge_history       JSONB DEFAULT '[]',       -- Audit of all links (append-only)
    
    -- Status
    is_merged           BOOLEAN NOT NULL DEFAULT FALSE,
    merged_into         UUID REFERENCES cliniqai_mpi.canonical_patients(global_patient_id),
    
    -- Quality
    data_quality_score  NUMERIC(4,3),
    has_conflicting_data BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- MPI de-identified ID (used for linking to AI schema)
    deidentified_id     UUID NOT NULL UNIQUE DEFAULT uuid_generate_v4(),
    
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mpi_ssn_hash ON cliniqai_mpi.canonical_patients(ssn_last4_hash) WHERE ssn_last4_hash IS NOT NULL;
CREATE INDEX idx_mpi_name_hash ON cliniqai_mpi.canonical_patients USING GIN(to_tsvector('simple', name_hash));
CREATE INDEX idx_mpi_dob ON cliniqai_mpi.canonical_patients(dob);
CREATE INDEX idx_mpi_mrn ON cliniqai_mpi.canonical_patients USING GIN(mrn_list);
CREATE INDEX idx_mpi_deidentified ON cliniqai_mpi.canonical_patients(deidentified_id);

-- Human review queue for low-confidence matches
CREATE TABLE cliniqai_mpi.review_queue (
    review_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- The match being reviewed
    incoming_record     JSONB NOT NULL,    -- Hashed incoming patient data
    candidate_id        UUID REFERENCES cliniqai_mpi.canonical_patients(global_patient_id),
    
    -- Match metadata
    confidence_score    NUMERIC(4,3) NOT NULL,
    matching_fields     TEXT[],
    conflicting_fields  TEXT[],
    match_rationale     TEXT,
    
    -- Status
    status              VARCHAR(32) NOT NULL DEFAULT 'pending',  -- pending|reviewing|resolved
    priority            VARCHAR(16) NOT NULL DEFAULT 'normal',  -- urgent|normal|low
    
    -- Resolution
    resolved_decision   VARCHAR(32),       -- link|create_new|needs_more_info
    resolved_by         UUID,
    resolved_at         TIMESTAMPTZ,
    resolution_notes    TEXT,
    
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_mpi_review_status ON cliniqai_mpi.review_queue(status, created_at);
CREATE INDEX idx_mpi_review_pending ON cliniqai_mpi.review_queue(created_at) WHERE status = 'pending';


-- ============================================================
-- OPERATIONAL SCHEMA
-- ============================================================

-- Hospitals / Deployments
CREATE TABLE cliniqai_ops.hospitals (
    hospital_id         VARCHAR(64) PRIMARY KEY,
    hospital_name       VARCHAR(256) NOT NULL,
    ehr_system          VARCHAR(32) NOT NULL,   -- epic|cerner|meditech
    bed_count           SMALLINT NOT NULL,
    icu_bed_count       SMALLINT,
    
    -- Configuration
    contract_start      DATE NOT NULL,
    contract_end        DATE,
    pilot_end           DATE,
    pricing_model       VARCHAR(32),            -- saas_per_bed|outcome_based|hybrid
    monthly_arr_usd     INTEGER,
    
    -- Feature flags
    features_enabled    JSONB NOT NULL DEFAULT '{}',
    alert_thresholds    JSONB,                 -- Hospital-specific thresholds
    
    -- Integration status
    epic_app_id         VARCHAR(128),
    fhir_base_url       VARCHAR(512),
    smart_auth_enabled  BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Compliance
    baa_signed          BOOLEAN NOT NULL DEFAULT FALSE,
    baa_date            DATE,
    hipaa_contact       VARCHAR(256),
    
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Bed Management
CREATE TABLE cliniqai_ops.beds (
    bed_id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    hospital_id         VARCHAR(64) NOT NULL,
    ward_code           VARCHAR(32) NOT NULL,
    bed_number          VARCHAR(16) NOT NULL,
    unit_type           VARCHAR(32) NOT NULL,   -- icu|ward|ed|step_down|isolation
    
    -- Current status
    status              VARCHAR(32) NOT NULL DEFAULT 'available',
    -- available|occupied|cleaning|maintenance|blocked
    
    current_encounter_id UUID,
    
    -- Physical attributes
    isolation_capable   BOOLEAN NOT NULL DEFAULT FALSE,
    isolation_type      VARCHAR(32),            -- contact|droplet|airborne
    
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(hospital_id, ward_code, bed_number)
);

CREATE INDEX idx_beds_hospital_status ON cliniqai_ops.beds(hospital_id, status);
CREATE INDEX idx_beds_available ON cliniqai_ops.beds(hospital_id) WHERE status = 'available';

-- Model Registry
CREATE TABLE cliniqai_ops.model_registry (
    model_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_name          VARCHAR(128) NOT NULL,
    model_type          VARCHAR(64) NOT NULL,   -- tft_vitals|llm_reasoning|imaging_classification
    version             VARCHAR(32) NOT NULL,
    
    -- Performance metrics at validation
    validation_auroc    NUMERIC(5,4),
    validation_sensitivity NUMERIC(5,4),
    validation_specificity NUMERIC(5,4),
    validation_dataset  VARCHAR(256),
    validation_date     DATE,
    
    -- Deployment
    is_deployed         BOOLEAN NOT NULL DEFAULT FALSE,
    deployed_at         TIMESTAMPTZ,
    deployed_by         UUID,
    deployment_hospitals TEXT[] DEFAULT '{}',
    
    -- Governance
    approved_by         VARCHAR(256),           -- Clinical review board sign-off
    approval_date       DATE,
    fda_cleared         BOOLEAN NOT NULL DEFAULT FALSE,
    fda_clearance_number VARCHAR(64),
    
    -- Baseline (for drift detection)
    baseline_metrics    JSONB,
    
    -- Artifact location
    model_artifact_uri  VARCHAR(512),           -- S3 path or model registry URL
    
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE(model_name, version)
);

-- Model Drift Snapshots
CREATE TABLE cliniqai_ops.drift_snapshots (
    snapshot_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    model_id            UUID REFERENCES cliniqai_ops.model_registry(model_id),
    hospital_id         VARCHAR(64),
    
    snapshot_week       DATE NOT NULL,          -- Week start date
    
    -- Performance metrics this week
    auroc               NUMERIC(5,4),
    acceptance_rate     NUMERIC(5,4),
    false_positive_rate NUMERIC(5,4),
    rejection_rate      NUMERIC(5,4),
    
    -- Vs baseline
    auroc_vs_baseline   NUMERIC(6,4),           -- Positive = improvement, negative = degradation
    drift_detected      BOOLEAN NOT NULL DEFAULT FALSE,
    drift_alerts        TEXT[],
    
    -- Actions taken
    auto_updates_frozen BOOLEAN NOT NULL DEFAULT FALSE,
    action_taken        VARCHAR(128),
    
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_drift_model_week ON cliniqai_ops.drift_snapshots(model_id, snapshot_week DESC);

-- ============================================================
-- USEFUL VIEWS FOR API AND ANALYTICS
-- ============================================================

-- Active high-risk patients for ICU board
CREATE VIEW cliniqai_ai.active_high_risk AS
SELECT
    p.patient_deident_id,
    p.encounter_id,
    p.risk_level,
    p.coordinator_confidence,
    p.human_review_required,
    p.session_start,
    p.escalations_sent,
    p.escalations_acked
FROM cliniqai_ai.agent_sessions p
WHERE p.session_end IS NULL
  AND p.final_risk_level IN ('CRITICAL', 'HIGH')
ORDER BY
    CASE p.final_risk_level WHEN 'CRITICAL' THEN 1 WHEN 'HIGH' THEN 2 END,
    p.session_start DESC;

-- Weekly feedback signal quality
CREATE VIEW cliniqai_ai.weekly_feedback_summary AS
SELECT
    DATE_TRUNC('week', feedback_at) AS week,
    COUNT(*) AS total_feedback,
    SUM(CASE WHEN is_valid_for_training THEN 1 ELSE 0 END) AS valid_for_training,
    ROUND(AVG(ml_signal::NUMERIC), 3) AS avg_signal,
    SUM(CASE WHEN signal = 'accepted' THEN 1 ELSE 0 END) AS accepted,
    SUM(CASE WHEN signal IN ('rejected','thumbs_down') THEN 1 ELSE 0 END) AS rejected,
    SUM(CASE WHEN outcome_linked AND outcome_occurred = TRUE THEN 1 ELSE 0 END) AS outcomes_confirmed,
    ROUND(
        SUM(CASE WHEN signal = 'accepted' THEN 1 ELSE 0 END)::NUMERIC /
        NULLIF(COUNT(*), 0), 3
    ) AS acceptance_rate
FROM cliniqai_ai.feedback
GROUP BY DATE_TRUNC('week', feedback_at)
ORDER BY week DESC;

-- ============================================================
-- ROW LEVEL SECURITY (RLS)
-- Enforce hospital-level data isolation
-- Each hospital can only see their own data
-- ============================================================

ALTER TABLE cliniqai_phi.patients ENABLE ROW LEVEL SECURITY;
ALTER TABLE cliniqai_phi.encounters ENABLE ROW LEVEL SECURITY;
ALTER TABLE cliniqai_ai.vitals_timeseries ENABLE ROW LEVEL SECURITY;

-- Hospital isolation policy (requires current_setting('app.hospital_id'))
CREATE POLICY hospital_isolation_patients ON cliniqai_phi.patients
    USING (hospital_id = current_setting('app.hospital_id', TRUE));

CREATE POLICY hospital_isolation_encounters ON cliniqai_phi.encounters
    USING (hospital_id = current_setting('app.hospital_id', TRUE));

-- ============================================================
-- SEED DATA FOR DEVELOPMENT
-- Uses Synthea-generated synthetic patient IDs (not real patients)
-- ============================================================

INSERT INTO cliniqai_ops.hospitals VALUES (
    'hospital_dev_001',
    'St. Mary Community Hospital (Dev)',
    'epic',
    300,
    28,
    '2025-01-01',
    '2026-12-31',
    '2025-04-01',
    'saas_per_bed',
    25000,
    '{"sepsis_prediction": true, "imaging_ai": true, "pharmacist_agent": true, "federated_learning": false}',
    '{"news2_high_alert": 5, "deterioration_6h_threshold": 0.70, "sepsis_12h_threshold": 0.50}',
    NULL,
    'https://fhir.smary.org/api/FHIR/R4',
    FALSE,
    TRUE,
    '2024-12-01',
    'privacy@smary.org',
    TRUE,
    NOW()
) ON CONFLICT DO NOTHING;

-- Sample model registry entry
INSERT INTO cliniqai_ops.model_registry (
    model_name, model_type, version, validation_auroc,
    validation_sensitivity, validation_specificity,
    validation_dataset, validation_date,
    is_deployed, deployed_at, approved_by, approval_date,
    baseline_metrics, model_artifact_uri
) VALUES (
    'cliniqai-sepsis-tft-v1',
    'tft_vitals',
    '1.0.0',
    0.878,
    0.812,
    0.861,
    'MIMIC-IV (n=52,847 ICU admissions)',
    '2024-10-15',
    TRUE,
    NOW(),
    'Clinical Review Board — Dr. Sharma, Dr. Mehta, Dr. Patel',
    '2024-11-01',
    '{"auroc": 0.878, "acceptance_rate": 0.72, "false_positive_rate": 0.139}',
    's3://cliniqai-models/sepsis-tft/v1.0.0/model.onnx'
) ON CONFLICT DO NOTHING;
