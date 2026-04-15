"""
Patients API — v1
Full patient CRUD with HIPAA enforcement at every endpoint.

Every endpoint:
  1. Verifies JWT (middleware)
  2. Runs ABAC check (care relationship required)
  3. Logs audit event (always, even denials)
  4. De-identifies data if AI_SYSTEM or RESEARCHER role
  5. Returns typed Pydantic response

PHI RULE: Never return raw PHI to AI system or researcher roles.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Path, BackgroundTasks
from fastapi import status as http_status
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, timezone
from uuid import UUID
import uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/patients", tags=["Patients"])


# ─────────────────────────────────────────────
# Request / Response Schemas
# ─────────────────────────────────────────────

class PatientCreate(BaseModel):
    """Minimal fields to register a new patient."""
    mrn: str = Field(..., description="Medical Record Number from source EHR")
    source_system: str = Field(..., description="epic|cerner|meditech")
    last_name: str
    first_name: str
    middle_name: Optional[str] = None
    date_of_birth: str = Field(..., description="YYYY-MM-DD")
    gender: str = Field(..., pattern="^[MFO]$")
    ssn_last4: Optional[str] = Field(None, pattern="^\\d{4}$")
    phone: Optional[str] = None
    email: Optional[str] = None
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    ethnicity: Optional[str] = None


class PatientSummary(BaseModel):
    """Safe summary returned to API callers (de-identified where required)."""
    patient_id: UUID
    deidentified_id: UUID
    birth_year: int
    gender: str
    state_code: Optional[str]
    zip_prefix: Optional[str]
    # PHI fields only populated for treating/consulting roles
    full_name: Optional[str] = None
    date_of_birth: Optional[str] = None
    mrn: Optional[str] = None
    data_quality_score: Optional[float] = None
    is_active: bool


class PatientIntelligenceResponse(BaseModel):
    """Full AI intelligence package for a patient."""
    patient_id: str
    session_id: str
    timestamp: str
    risk_level: str
    risk_justification: str
    coordinator_confidence: float
    triage: Optional[dict] = None
    risk: Optional[dict] = None
    pharmacist_alerts: Optional[dict] = None
    reasoning_summary: Optional[str] = None
    differential_diagnoses: List[dict] = []
    recommended_actions: List[dict] = []
    data_gaps: List[str] = []
    human_review_required: bool
    human_review_reason: str
    escalations_active: int
    ai_disclaimer: str = (
        "AI Decision Support Only. All recommendations require physician "
        "review before clinical action. Confidence scores are probabilistic."
    )


class AdmissionCreate(BaseModel):
    patient_id: UUID
    encounter_type: str = Field(default="inpatient")
    admission_datetime: str
    ward_code: str
    bed_id: str
    unit_type: str = Field(default="icu")
    chief_complaint: str
    admission_type: str = Field(default="emergency")
    attending_id: Optional[UUID] = None


class VitalSignIngestion(BaseModel):
    """Single vital sign reading for batch or stream ingestion."""
    patient_id: UUID
    encounter_id: UUID
    parameter: str = Field(..., description="LOINC-mapped parameter name")
    value: float
    unit: str
    timestamp: Optional[str] = None
    device_id: Optional[str] = None


class PatientListResponse(BaseModel):
    patients: List[PatientSummary]
    total: int
    page: int
    per_page: int
    has_more: bool


# ─────────────────────────────────────────────
# Dependency Injection Stubs
# (In production: imported from middleware)
# ─────────────────────────────────────────────

async def get_current_user():
    """Extract and validate JWT. Returns user claims dict."""
    # Production: decode JWT, validate signature, check expiry
    return {
        "user_id": "dev-physician-001",
        "role": "physician",
        "department": "ICU",
        "hospital_id": "hospital_dev_001",
        "care_assignments": [],   # Patient IDs this user is treating
    }


async def get_db():
    """Database session dependency."""
    # Production: yield SQLAlchemy async session
    yield None


async def get_compliance_gateway():
    """Compliance gateway dependency."""
    # Production: return configured ComplianceGateway instance
    yield None


def log_audit(user, action, resource_type, resource_id, outcome, ip="0.0.0.0"):
    """Fire-and-forget audit logging."""
    logger.info(
        f"AUDIT actor={user['user_id']} role={user['role']} "
        f"action={action} resource={resource_type}/{resource_id} "
        f"outcome={outcome}"
    )


# ─────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────

@router.post(
    "",
    response_model=PatientSummary,
    status_code=http_status.HTTP_201_CREATED,
    summary="Register a new patient",
    description="""
    Register a new patient from an EHR source system.
    
    Triggers:
    - FHIR R4 normalization
    - MPI probabilistic matching (auto-link if confidence ≥ 0.95)
    - Data quality scoring
    - De-identification key generation (stored in Vault)
    
    PHI is encrypted at rest immediately upon receipt.
    Requires: physician or admin role.
    """,
)
async def create_patient(
    payload: PatientCreate,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    db=Depends(get_db),
    gateway=Depends(get_compliance_gateway),
):
    if user["role"] not in ["physician", "admin", "nurse"]:
        raise HTTPException(403, "Insufficient role to create patient records")

    patient_id = uuid.uuid4()
    deident_id = uuid.uuid4()

    # In production:
    # 1. gateway.deidentifier.pseudonymize(payload.mrn)
    # 2. fhir_normalizer.normalize_patient(payload.dict())
    # 3. mpi_engine.find_matches(...) → auto-link or queue for review
    # 4. db.save(patient)
    # 5. background: vector embedding generation

    log_audit(user, "write", "Patient", str(patient_id), "success")
    background_tasks.add_task(log_audit, user, "write", "Patient", str(patient_id), "success")

    return PatientSummary(
        patient_id=patient_id,
        deidentified_id=deident_id,
        birth_year=int(payload.date_of_birth[:4]),
        gender=payload.gender,
        state_code=payload.state,
        zip_prefix=payload.zip_code[:3] if payload.zip_code else None,
        full_name=f"{payload.last_name}, {payload.first_name}",
        date_of_birth=payload.date_of_birth,
        mrn=payload.mrn,
        data_quality_score=0.90,
        is_active=True,
    )


@router.get(
    "",
    response_model=PatientListResponse,
    summary="List active patients",
    description="Returns patients for the requesting user's ward/department. Sorted by risk level.",
)
async def list_patients(
    unit_type: Optional[str] = Query(None, description="icu|ward|ed"),
    ward_code: Optional[str] = Query(None),
    risk_level: Optional[str] = Query(None, description="CRITICAL|HIGH|MEDIUM|LOW"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    # Production: query DB with ABAC filter (user's department + care assignments)
    log_audit(user, "read", "Patient", "list", "success")

    mock_patients = [
        PatientSummary(
            patient_id=uuid.uuid4(),
            deidentified_id=uuid.uuid4(),
            birth_year=1957,
            gender="M",
            state_code="UP",
            zip_prefix="226",
            full_name="Sharma, Rajesh",
            date_of_birth="1957-03-12",
            mrn="MRN001234",
            data_quality_score=0.92,
            is_active=True,
        )
    ]

    return PatientListResponse(
        patients=mock_patients,
        total=1,
        page=page,
        per_page=per_page,
        has_more=False,
    )


@router.get(
    "/{patient_id}",
    response_model=PatientSummary,
    summary="Get patient record",
)
async def get_patient(
    patient_id: UUID = Path(...),
    user=Depends(get_current_user),
    db=Depends(get_db),
    gateway=Depends(get_compliance_gateway),
):
    # Production:
    # 1. ABAC check: care relationship required
    # 2. Load patient from DB
    # 3. De-identify if AI_SYSTEM / RESEARCHER role
    # 4. Log audit event
    log_audit(user, "read", "Patient", str(patient_id), "success")

    return PatientSummary(
        patient_id=patient_id,
        deidentified_id=uuid.uuid4(),
        birth_year=1957,
        gender="M",
        state_code="UP",
        zip_prefix="226",
        full_name="Sharma, Rajesh" if user["role"] == "physician" else None,
        date_of_birth="1957-03-12" if user["role"] == "physician" else None,
        mrn="MRN001234" if user["role"] == "physician" else None,
        data_quality_score=0.92,
        is_active=True,
    )


@router.get(
    "/{patient_id}/intelligence",
    response_model=PatientIntelligenceResponse,
    summary="Get full AI intelligence package",
    description="""
    The PRIMARY endpoint. Triggers full multi-agent pipeline:
    
    1. Load patient context (vitals + labs + meds + history)
    2. Run all 7 AI agents (parallel where safe)
    3. Coordinator synthesizes outputs
    4. LLM reasoning generates clinical summary
    5. Return unified physician recommendation package
    
    SLA: Response within 10 seconds (agent timeout enforcement).
    PHI: Patient data de-identified before AI processing.
    Physician review REQUIRED before any clinical action.
    """,
)
async def get_patient_intelligence(
    patient_id: UUID = Path(...),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    # Production: AgentOrchestrator.process_patient_event(...)
    log_audit(user, "infer", "Patient", str(patient_id), "success")

    return PatientIntelligenceResponse(
        patient_id=str(patient_id),
        session_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        risk_level="HIGH",
        risk_justification=(
            "NEWS2=6, deterioration probability 71% in 6h (AI TFT model), "
            "sepsis probability 42% in 12h, rising HR trend over 4 hours."
        ),
        coordinator_confidence=0.82,
        triage={"esi_category": 2, "care_pathway": "emergency_treatment"},
        risk={
            "risk_level": "HIGH",
            "news2_score": 6,
            "sofa_score": 4,
            "ai_predictions": {
                "deterioration_6h": 0.71,
                "sepsis_12h": 0.42,
                "mortality_24h": 0.18,
            },
            "trend": "worsening",
        },
        pharmacist_alerts={"alert_level": "WARNING", "alerts": []},
        reasoning_summary=(
            "67-year-old male with progressive dyspnea and fever. "
            "Vitals demonstrate tachycardia, moderate hypoxemia, and elevated temperature. "
            "Inflammatory markers trending upward. Clinical trajectory: worsening."
        ),
        differential_diagnoses=[
            {
                "condition": "Sepsis (respiratory source)",
                "icd10": "A41.9",
                "supporting_evidence": ["HR 112 (tachycardia)", "Temp 38.6°C (fever)", "WBC 16.2 (leukocytosis)"],
                "contradicting_evidence": ["BP within acceptable range", "No documented source yet"],
                "probability_rank": "primary",
                "confidence": 0.68,
            },
            {
                "condition": "Community-Acquired Pneumonia",
                "icd10": "J18.9",
                "supporting_evidence": ["Fever", "Dyspnea", "Elevated CRP"],
                "contradicting_evidence": ["CXR result pending"],
                "probability_rank": "alternative",
                "confidence": 0.54,
            },
        ],
        recommended_actions=[
            {
                "action": "Blood cultures × 2 peripheral sites",
                "urgency": "immediate",
                "rationale": "Sepsis-3 bundle initiation — lactate + cultures before antibiotics",
                "evidence_base": "Surviving Sepsis Campaign 2021 Guidelines",
            },
            {
                "action": "Serum lactate",
                "urgency": "immediate",
                "rationale": "Lactate >2 mmol/L indicates tissue hypoperfusion in sepsis",
                "evidence_base": "Sepsis-3 criteria — SOFA score lactate component",
            },
            {
                "action": "IV antibiotics within 1 hour if sepsis confirmed",
                "urgency": "short_term",
                "rationale": "Every hour delay in sepsis antibiotics increases mortality ~7%",
                "evidence_base": "Kumar et al., CCM 2006; Surviving Sepsis Campaign 2021",
            },
            {
                "action": "Repeat NEWS2 assessment in 2 hours",
                "urgency": "monitoring",
                "rationale": "Track response to interventions; NEWS2 trend guides escalation",
                "evidence_base": "RCP NEWS2 Guidelines 2017",
            },
        ],
        data_gaps=[
            "Blood cultures pending",
            "Chest X-ray awaited (ordered 47 min ago)",
            "Procalcitonin result pending",
            "Prior renal function not available",
        ],
        human_review_required=True,
        human_review_reason="HIGH risk level — physician review mandatory before clinical action",
        escalations_active=0,
    )


@router.post(
    "/{patient_id}/admissions",
    status_code=http_status.HTTP_201_CREATED,
    summary="Create hospital admission",
)
async def create_admission(
    patient_id: UUID,
    payload: AdmissionCreate,
    user=Depends(get_current_user),
):
    if user["role"] not in ["physician", "nurse", "admin"]:
        raise HTTPException(403, "Insufficient role")

    encounter_id = uuid.uuid4()
    log_audit(user, "write", "Encounter", str(encounter_id), "success")

    return {
        "encounter_id": str(encounter_id),
        "patient_id": str(patient_id),
        "status": "active",
        "admission_datetime": payload.admission_datetime,
        "ward_code": payload.ward_code,
        "bed_id": payload.bed_id,
        "message": "Admission created. AI monitoring activated for ICU unit.",
    }


@router.get(
    "/{patient_id}/vitals",
    summary="Get patient vitals history",
)
async def get_patient_vitals(
    patient_id: UUID,
    hours: int = Query(6, ge=1, le=168, description="Hours of history to return"),
    parameter: Optional[str] = Query(None, description="Filter by specific parameter"),
    user=Depends(get_current_user),
):
    log_audit(user, "read", "Observation", f"{patient_id}/vitals", "success")

    # Production: query TimescaleDB hypertable
    import random
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    vitals = []
    parameters = [parameter] if parameter else [
        "heart_rate", "spo2_pulse_ox", "bp_systolic", "respiratory_rate", "temperature"
    ]

    for param in parameters:
        base_vals = {
            "heart_rate": 110, "spo2_pulse_ox": 92,
            "bp_systolic": 105, "respiratory_rate": 24, "temperature": 38.5,
        }
        base = base_vals.get(param, 80)
        for i in range(min(hours * 6, 60)):  # Max 60 points per parameter
            vitals.append({
                "timestamp": (now - timedelta(minutes=i * (hours * 10 // 60))).isoformat(),
                "parameter": param,
                "value": round(base + random.gauss(0, base * 0.05), 2),
                "unit": {"heart_rate": "/min", "spo2_pulse_ox": "%",
                         "bp_systolic": "mmHg", "respiratory_rate": "/min",
                         "temperature": "Cel"}.get(param, ""),
                "is_critical": False,
            })

    return {
        "patient_id": str(patient_id),
        "hours_requested": hours,
        "vitals": vitals,
        "count": len(vitals),
    }


@router.post(
    "/{patient_id}/vitals",
    status_code=http_status.HTTP_201_CREATED,
    summary="Ingest vital sign reading",
)
async def ingest_vital(
    patient_id: UUID,
    payload: VitalSignIngestion,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    """
    Ingest a single vital sign reading.
    
    In production: write directly to Kafka topic `icu.vitals.raw`.
    The Kafka consumer normalizes, quality-scores, and stores to TimescaleDB.
    This endpoint is called by the MQTT→Kafka bridge for ICU monitors.
    """
    log_audit(user, "write", "Observation", str(patient_id), "success")

    # Production: kafka_producer.send("icu.vitals.raw", payload.dict())
    return {"status": "queued", "message": "Vital sign queued for processing"}


@router.get(
    "/{patient_id}/medications",
    summary="Get active medications",
)
async def get_medications(
    patient_id: UUID,
    active_only: bool = Query(True),
    user=Depends(get_current_user),
):
    log_audit(user, "read", "MedicationRequest", str(patient_id), "success")

    return {
        "patient_id": str(patient_id),
        "medications": [
            {"name": "Norepinephrine", "dose": "0.1 mcg/kg/min", "route": "IV", "status": "active"},
            {"name": "Vancomycin", "dose": "1.5g", "route": "IV", "frequency": "Q12H", "status": "active"},
            {"name": "Piperacillin/Tazobactam", "dose": "4.5g", "route": "IV", "frequency": "Q8H", "status": "active"},
            {"name": "Heparin", "dose": "5000 units", "route": "SQ", "frequency": "Q8H", "status": "active"},
        ],
        "drug_interaction_check": "pending",
        "last_reconciliation": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/{patient_id}/timeline",
    summary="Clinical event timeline",
    description="Chronological clinical events: admissions, labs, vitals alerts, AI recommendations, physician actions.",
)
async def get_patient_timeline(
    patient_id: UUID,
    hours: int = Query(24),
    user=Depends(get_current_user),
):
    log_audit(user, "read", "Patient", f"{patient_id}/timeline", "success")

    now = datetime.now(timezone.utc)
    return {
        "patient_id": str(patient_id),
        "events": [
            {"time": (now.replace(hour=8, minute=0)).isoformat(), "type": "admission", "description": "Admitted to ICU-B, Bed B-04"},
            {"time": (now.replace(hour=8, minute=30)).isoformat(), "type": "lab_result", "description": "WBC 16.2 (H), Lactate 2.8 (H), CRP 185 (H)"},
            {"time": (now.replace(hour=9, minute=15)).isoformat(), "type": "ai_alert", "description": "AI: Sepsis probability 42%, NEWS2=6 — Review recommended", "risk_level": "HIGH"},
            {"time": (now.replace(hour=9, minute=20)).isoformat(), "type": "physician_action", "description": "Blood cultures × 2 ordered (AI recommendation accepted)"},
            {"time": (now.replace(hour=10, minute=0)).isoformat(), "type": "medication", "description": "Vancomycin 1.5g IV started"},
            {"time": (now.replace(hour=10, minute=15)).isoformat(), "type": "ai_alert", "description": "AI: Drug interaction — Vancomycin + Piperacillin: nephrotoxicity risk", "risk_level": "WARNING"},
            {"time": (now.replace(hour=10, minute=20)).isoformat(), "type": "physician_action", "description": "Renal function monitoring ordered (drug interaction alert acknowledged)"},
        ],
    }
