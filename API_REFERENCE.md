# CliniQAI — API Reference

Complete endpoint documentation with curl examples for local and production.

**Local base URL:**      `http://localhost:8000`  
**Production base URL:** `https://your-app.up.railway.app`

Set a shell variable to switch between environments:
```bash
BASE=http://localhost:8000          # local
BASE=https://your-app.up.railway.app  # production
```

---

## Authentication

All endpoints (except `/health` and `/auth/login`) require a JWT Bearer token.

### POST /auth/login

Obtain an access token.

```bash
curl -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "physician_001",
    "password": "demo_password",
    "hospital_id": "demo_hospital_001"
  }'
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_in": 3600,
  "user_role": "physician",
  "hospital_id": "demo_hospital_001"
}
```

Save the token:
```bash
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"physician_001","password":"demo_password","hospital_id":"demo_hospital_001"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

Use it in all subsequent requests:
```bash
curl $BASE/api/v1/patients -H "Authorization: Bearer $TOKEN"
```

### POST /auth/refresh

Refresh an expired access token.

```bash
curl -X POST $BASE/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGci..."}'
```

### POST /auth/logout

Invalidate the current session.

```bash
curl -X POST $BASE/auth/logout \
  -H "Authorization: Bearer $TOKEN"
```

---

## System

### GET /health

Basic health check. No auth required. Used by load balancers.

```bash
curl $BASE/health
```
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "environment": "production",
  "hospital": "Your Hospital Name"
}
```

### GET /health/detailed

Detailed dependency and feature flag status.

```bash
curl $BASE/health/detailed
```
```json
{
  "status": "healthy",
  "services": { "database": "ok", "redis": "ok", "kafka": "ok" },
  "features": { "sepsis_prediction": true, "imaging_ai": false },
  "compliance": { "hipaa_mode": true, "fda_clearance_status": "pending_510k" }
}
```

---

## Patients

### GET /api/v1/patients

List active patients for the requesting user's ward.

```bash
curl "$BASE/api/v1/patients?unit_type=icu&risk_level=HIGH" \
  -H "Authorization: Bearer $TOKEN"
```

**Query params:**

| Param | Type | Description |
|-------|------|-------------|
| `unit_type` | string | `icu` \| `ward` \| `ed` |
| `risk_level` | string | `CRITICAL` \| `HIGH` \| `MEDIUM` \| `LOW` |
| `page` | int | Page number (default: 1) |
| `per_page` | int | Results per page (default: 20, max: 100) |

### GET /api/v1/patients/{patient_id}

Get a single patient's base record.

```bash
curl $BASE/api/v1/patients/550e8400-e29b-41d4-a716-446655440000 \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/patients/{patient_id}/intelligence ⭐ PRIMARY ENDPOINT

**The core endpoint.** Runs the full 7-agent pipeline and LLM reasoning engine.  
Returns risk assessment, differential diagnoses, recommended actions, and drug alerts.

```bash
curl $BASE/api/v1/patients/550e8400-e29b-41d4-a716-446655440000/intelligence \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "patient_id": "550e8400-e29b-41d4-a716-446655440000",
  "risk_level": "HIGH",
  "patient_state_summary": "72yo male, 36h post-op. HR 112 rising, SpO2 91% dropping, WBC 18.2. Concerning for early sepsis.",
  "differential_diagnoses": [
    {
      "condition": "Sepsis (post-operative)",
      "icd10": "A41.9",
      "probability_rank": "primary",
      "confidence": 0.74,
      "supporting_evidence": ["HR 112 bpm (tachycardia)", "SpO2 91% (hypoxemia)", "WBC 18.2 (leukocytosis)"],
      "contradicting_evidence": ["BP 118/72 (normotensive — no septic shock yet)"]
    }
  ],
  "recommended_actions": [
    {
      "action": "Blood cultures x2 before antibiotics, lactate level",
      "urgency": "immediate",
      "rationale": "Sepsis-3 bundle: cultures before antibiotics reduces culture yield loss",
      "evidence_base": "Surviving Sepsis Campaign 2021 guidelines"
    }
  ],
  "overall_confidence": "MEDIUM",
  "data_gaps": ["No recent lactate", "No blood cultures ordered yet"],
  "human_review_required": true,
  "human_review_reason": "HIGH risk with sepsis pattern — physician response within 1 hour",
  "disclaimer": "AI Decision Support Only. Physician review required before clinical action.",
  "processing_ms": 4820
}
```

**SLA:** < 10 seconds. HIPAA: Patient data de-identified before AI processing.

### GET /api/v1/patients/{patient_id}/vitals

Get vital sign history for a patient.

```bash
curl "$BASE/api/v1/patients/PATIENT_ID/vitals?hours=6" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/patients/{patient_id}/medications

Get active medications.

```bash
curl $BASE/api/v1/patients/PATIENT_ID/medications \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/patients/{patient_id}/timeline

Get 24-hour clinical event timeline.

```bash
curl "$BASE/api/v1/patients/PATIENT_ID/timeline?hours=24" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Vitals

### POST /api/v1/vitals/ingest

Ingest a single vital sign reading (from ICU monitor, manual entry, etc).

```bash
curl -X POST $BASE/api/v1/vitals/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_deident_id": "550e8400-e29b-41d4-a716-446655440000",
    "encounter_id": "encounter-uuid-here",
    "parameter": "heart_rate",
    "value": 112.0,
    "unit": "/min",
    "timestamp": "2026-04-15T09:30:00Z",
    "device_id": "philips-icu-b04",
    "source_system": "icu_monitor"
  }'
```

**Supported parameters:**

| Parameter | LOINC | Unit |
|-----------|-------|------|
| `heart_rate` | 8867-4 | `/min` |
| `spo2_pulse_ox` | 59408-5 | `%` |
| `bp_systolic` | 8480-6 | `mm[Hg]` |
| `bp_diastolic` | 8462-4 | `mm[Hg]` |
| `respiratory_rate` | 9279-1 | `/min` |
| `temperature` | 8310-5 | `Cel` |
| `gcs_total` | 9269-2 | `{score}` |

### POST /api/v1/vitals/ingest/batch

Ingest multiple readings at once (max 500 per batch).

```bash
curl -X POST $BASE/api/v1/vitals/ingest/batch \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "readings": [
      {"patient_deident_id":"UUID","parameter":"heart_rate","value":112.0,"unit":"/min","timestamp":"2026-04-15T09:30:00Z"},
      {"patient_deident_id":"UUID","parameter":"spo2_pulse_ox","value":91.0,"unit":"%","timestamp":"2026-04-15T09:30:00Z"}
    ]
  }'
```

### GET /api/v1/vitals/{patient_id}/ai-prediction

Get the latest TFT model predictions for a patient (NEWS2, sepsis probability, deterioration risk).

```bash
curl $BASE/api/v1/vitals/PATIENT_ID/ai-prediction \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
{
  "patient_deident_id": "...",
  "news2_score": 7,
  "sofa_score": 3,
  "deterioration_6h": 0.68,
  "sepsis_12h": 0.52,
  "mortality_24h": 0.21,
  "trend": "worsening",
  "anomalies": [
    { "parameter": "spo2_pulse_ox", "current_value": 91.0, "severity": "severe" }
  ],
  "active_alerts": ["NEWS2=7 — Urgent clinical response required"],
  "alert_priority": "HIGH"
}
```

### GET /api/v1/vitals/icu/{ward_code}/snapshot

Get all patients in an ICU ward with latest vitals and risk scores.

```bash
curl $BASE/api/v1/vitals/icu/ICU-B/snapshot \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/vitals/{patient_id}/trend

Get trending data for a specific vital parameter.

```bash
curl "$BASE/api/v1/vitals/PATIENT_ID/trend?parameter=heart_rate&hours=6" \
  -H "Authorization: Bearer $TOKEN"
```

### WebSocket: ws://{host}/api/v1/vitals/ws/ward/{ward_id}

Real-time ward vitals stream (1Hz updates).

```javascript
const ws = new WebSocket("wss://your-app.up.railway.app/api/v1/vitals/ws/ward/ICU-B?token=YOUR_JWT");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
// Receives: { type: "vitals_update", patients: [...], timestamp: "..." }
```

### WebSocket: ws://{host}/api/v1/vitals/ws/patient/{patient_id}

Single-patient vitals at 1Hz.

```javascript
const ws = new WebSocket("wss://your-app.up.railway.app/api/v1/vitals/ws/patient/PATIENT_ID?token=JWT");
```

---

## Clinical Inference

### POST /api/v1/inference/patient

Run the full AI inference pipeline for a patient. Alternative to the intelligence endpoint.

```bash
curl -X POST $BASE/api/v1/inference/patient \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_deident_id": "550e8400-e29b-41d4-a716-446655440000",
    "encounter_id": "encounter-uuid",
    "chief_complaint": "Progressive dyspnea and fever x 2 days",
    "urgency": "urgent",
    "include_imaging": false,
    "include_nlp": true,
    "include_vitals": true
  }'
```

**urgency:** `routine` | `urgent` | `stat`

### GET /api/v1/inference/{inference_id}

Retrieve a specific inference result by ID.

```bash
curl $BASE/api/v1/inference/INFERENCE_ID \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/inference/patient/{patient_id}/history

Get inference history for a patient.

```bash
curl "$BASE/api/v1/inference/patient/PATIENT_ID/history?hours=24" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/inference/{inference_id}/feedback

Submit physician feedback on an inference result (1-tap, <3s to complete).

```bash
# Thumbs up — accepted recommendation
curl -X POST "$BASE/api/v1/inference/INFERENCE_ID/feedback?signal=thumbs_up&is_helpful=true" \
  -H "Authorization: Bearer $TOKEN"

# Thumbs down with reason
curl -X POST "$BASE/api/v1/inference/INFERENCE_ID/feedback?signal=thumbs_down&is_helpful=false&reason=Patient+already+had+cultures" \
  -H "Authorization: Bearer $TOKEN"
```

**signal values:** `thumbs_up` | `thumbs_down` | `accepted` | `rejected` | `modified`

---

## Multi-Agent System

### GET /api/v1/agents/status

Get status of all 7 AI agents.

```bash
curl $BASE/api/v1/agents/status \
  -H "Authorization: Bearer $TOKEN"
```

**Response:**
```json
[
  { "agent_id": "triage_agent",        "status": "idle",    "avg_latency_ms": 145 },
  { "agent_id": "risk_agent",          "status": "running", "avg_latency_ms": 892 },
  { "agent_id": "diagnosis_agent",     "status": "idle",    "avg_latency_ms": 1240 },
  { "agent_id": "pharmacist_agent",    "status": "idle",    "avg_latency_ms": 203 },
  { "agent_id": "documentation_agent", "status": "idle",    "avg_latency_ms": 567 },
  { "agent_id": "coordinator_agent",   "status": "idle",    "avg_latency_ms": 321 },
  { "agent_id": "escalation_agent",    "status": "idle",    "avg_latency_ms": 88  }
]
```

### GET /api/v1/agents/sessions/{patient_id}

Get agent pipeline session for a patient.

```bash
curl $BASE/api/v1/agents/sessions/PATIENT_ID \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/agents/sessions/{patient_id}/trigger

Manually trigger agent pipeline for a patient.

```bash
curl -X POST "$BASE/api/v1/agents/sessions/PATIENT_ID/trigger?reason=Nurse+concern+about+deterioration" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/agents/escalations

Get active unacknowledged escalations.

```bash
curl "$BASE/api/v1/agents/escalations?ward_code=ICU-B" \
  -H "Authorization: Bearer $TOKEN"
```

### POST /api/v1/agents/escalations/{escalation_id}/acknowledge

Acknowledge an escalation alert.

```bash
curl -X POST $BASE/api/v1/agents/escalations/ESCALATION_ID/acknowledge \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"notes": "Reviewed — ordered repeat labs and increased monitoring"}'
```

### GET /api/v1/agents/metrics

Get agent performance metrics.

```bash
curl "$BASE/api/v1/agents/metrics?hours=24" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Admin Intelligence

> Requires `admin`, `cfo`, or `coo` role.

### GET /api/v1/admin/dashboard/cfo

CFO financial impact dashboard.

```bash
curl $BASE/api/v1/admin/dashboard/cfo \
  -H "Authorization: Bearer $TOKEN"
```

**Response includes:**
- `financial_impact.total_estimated_value_usd`
- `financial_impact.readmission_penalty_avoided_usd`
- `financial_impact.roi_vs_subscription` (e.g. `4.2` = 4.2× ROI)
- `clinical_quality.readmission_rate_pct`
- `ai_adoption.recommendation_acceptance_rate`
- `operational.current_census` / `total_beds`

### GET /api/v1/admin/dashboard/coo

COO operational dashboard.

```bash
curl $BASE/api/v1/admin/dashboard/coo \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/beds/snapshot

Real-time bed management snapshot.

```bash
curl $BASE/api/v1/admin/beds/snapshot \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/beds/predictions/discharges

AI-predicted discharges in next N hours.

```bash
curl "$BASE/api/v1/admin/beds/predictions/discharges?hours=24" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/readmissions/risk-list

Patients at high readmission risk (for proactive intervention).

```bash
curl "$BASE/api/v1/admin/readmissions/risk-list?risk_threshold=0.25" \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/quality/core-measures

CMS core quality measures compliance.

```bash
curl $BASE/api/v1/admin/quality/core-measures \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/models/registry

AI model registry (versions, AUROC, FDA status).

```bash
curl $BASE/api/v1/admin/models/registry \
  -H "Authorization: Bearer $TOKEN"
```

### GET /api/v1/admin/models/drift

Weekly model drift monitoring data.

```bash
curl "$BASE/api/v1/admin/models/drift?model_name=sepsis_tft_v1" \
  -H "Authorization: Bearer $TOKEN"
```

---

## Error Responses

All errors return a consistent JSON structure:

```json
{
  "error": "Human-readable error message",
  "request_id": "550e8400-e29b-41d4-a716-446655440000",
  "timestamp": 1718400000.0
}
```

| HTTP Status | Meaning |
|-------------|---------|
| `200` | Success |
| `201` | Created |
| `204` | Success (no content) |
| `400` | Bad request (invalid payload) |
| `401` | Unauthorized (missing or expired JWT) |
| `403` | Forbidden (insufficient role/relationship) |
| `404` | Resource not found |
| `422` | Validation error (invalid field values) |
| `429` | Rate limited (100 req/min per IP) |
| `500` | Internal server error |

---

## Rate Limits

| Endpoint group | Limit |
|----------------|-------|
| All API endpoints | 100 req/min per IP |
| `POST /auth/login` | 10 req/min per IP |
| `POST /api/v1/inference/patient` | 30 req/min per user |
| WebSocket connections | 10 concurrent per IP |

---

## How to Use This Project — Quick Start

### Demo login credentials (dev only)

```
Username: physician_001    Password: demo_password    Role: physician
Username: nurse_001        Password: demo_password    Role: nurse
Username: admin_001        Password: demo_password    Role: admin
```

### Typical physician workflow

```bash
# 1. Login
TOKEN=$(curl -s -X POST $BASE/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"physician_001","password":"demo_password","hospital_id":"demo_hospital_001"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. View ICU ward
curl "$BASE/api/v1/vitals/icu/ICU-B/snapshot" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 3. Get AI intelligence for a high-risk patient
curl "$BASE/api/v1/patients/PATIENT_ID/intelligence" -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# 4. Submit feedback on the recommendation
curl -X POST "$BASE/api/v1/inference/INFERENCE_ID/feedback?signal=thumbs_up&is_helpful=true" \
  -H "Authorization: Bearer $TOKEN"
```

### Ingest test vitals

```bash
# Send a critical SpO2 reading to trigger an alert
curl -X POST $BASE/api/v1/vitals/ingest \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_deident_id": "YOUR-PATIENT-UUID",
    "encounter_id": "YOUR-ENCOUNTER-UUID",
    "parameter": "spo2_pulse_ox",
    "value": 87.0,
    "unit": "%",
    "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }'
```

### Run pharmacy DDI check

```bash
curl -X POST $BASE/api/v1/inference/patient \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "patient_deident_id": "YOUR-PATIENT-UUID",
    "encounter_id": "YOUR-ENCOUNTER-UUID",
    "chief_complaint": "Check for drug interactions",
    "urgency": "routine"
  }'
```

---

## OpenAPI Spec

The full OpenAPI 3.1 spec is at `docs/api/openapi.yaml`.

In development, interactive Swagger UI is at:
- **Local:** `http://localhost:8000/docs`
- **Production:** disabled (returns 404 — security requirement)

To generate a client SDK:
```bash
npx openapi-typescript-codegen \
  --input http://localhost:8000/openapi.json \
  --output frontend/src/generated \
  --client axios
```
