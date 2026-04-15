# CliniQAI — 9-Month Build Roadmap

**Principle: Ship incrementally. Validate at every stage. Never build what you can't validate.**

> Month 1 goal: Something real running  
> Month 3 goal: First synthetic data flowing end-to-end  
> Month 6 goal: Shadow-mode ready for first hospital  
> Month 9 goal: First hospital pilot signed and running

---

## Month 1 — Foundation Infrastructure

### Engineering (4-person team allocation)

**Backend Engineer 1 (Platform Lead):**
- [ ] AWS account setup + HIPAA-compliant architecture
- [ ] VPC with private subnets, NAT gateway, no public endpoints
- [ ] RDS PostgreSQL 16 + TimescaleDB provisioned (Multi-AZ staging)
- [ ] AWS KMS key creation (CMK for PHI encryption)
- [ ] Redis cluster provisioned
- [ ] S3 buckets: warm-path, archive, audit-log-worm
- [ ] AWS KMS + IAM roles configured per least-privilege

**Backend Engineer 2 (Data Layer):**
- [ ] Database schema implemented (`scripts/init_db.sql`)
- [ ] FastAPI application scaffolding
- [ ] Environment configuration system (`config/settings.py`)
- [ ] Docker Compose local dev stack working end-to-end
- [ ] CI/CD pipeline: GitHub Actions → AWS ECR → EKS (staging)

**Backend Engineer 3 (AI Lead):**
- [ ] Synthea data generator implemented (`scripts/seed_synthea.py`)
- [ ] 1,000 synthetic patients generated and loaded
- [ ] FHIR R4 normalizer for Patient + Observation resources
- [ ] Basic vital sign ingestion endpoint working

**Full-stack Engineer:**
- [ ] React app scaffold + routing
- [ ] JWT authentication flow (login, refresh, logout)
- [ ] Basic dashboard layout (no real data yet)
- [ ] Storybook component library started

### Deliverable: Local Stack Running
```
docker-compose up -d → all services start
python seed_synthea.py --patients 1000 → data loaded  
uvicorn main:app → API responds
npm run dev → dashboard renders (mock data)
```

---

## Month 2 — HIPAA Layer + FHIR Normalization

### Critical: HIPAA controls BEFORE any data flows

**Backend Engineer 1:**
- [ ] ABAC engine implemented and tested (`services/compliance/gateway.py`)
- [ ] JWT middleware: extract claims, validate role, enforce ABAC
- [ ] Audit logging middleware: every request → `cliniqai_audit.access_log`
- [ ] WORM audit log S3 sync (append-only)
- [ ] Breach detection service: real-time anomaly monitoring
- [ ] Rate limiting + DDoS protection (AWS WAF)

**Backend Engineer 2:**
- [ ] FHIR R4 normalizer: MedicationRequest, Condition, AllergyIntolerance
- [ ] LOINC code validation on all Observation ingestion
- [ ] De-identification engine (`services/compliance/gateway.py`)
  - All 18 Safe Harbor identifiers handled
  - Date shifting (±90 days, consistent per patient)
  - Pseudonymization via HMAC-SHA256
- [ ] Original↔pseudonym vault (HashiCorp Vault integration)
- [ ] Data quality scoring engine

**Backend Engineer 3:**
- [ ] Kafka setup: all 5 topics created
- [ ] ICU stream simulator: MQTT → Kafka bridge (Docker)
- [ ] Dead-letter queue with retry logic (exponential backoff)
- [ ] Consent management service

**Full-stack Engineer:**
- [ ] Login/logout with JWT
- [ ] Role-based UI routing (nurse sees different views than physician)
- [ ] Basic patient list page (read from API)

### Deliverable: HIPAA-Compliant Data Pipeline
```
ICU simulator → MQTT → Kafka → FHIR normalizer → PostgreSQL
API requests → ABAC check → audit log → response (de-identified)
```

### Security Review (End of Month 2)
- Internal threat model review
- OWASP Top 10 self-assessment
- No external pentest yet (too early), but document known risks

---

## Month 3 — Master Patient Index + Streaming

**Backend Engineer 1:**
- [ ] MPI probabilistic matching engine (`services/mpi/engine.py`)
- [ ] Jaro-Winkler similarity algorithm
- [ ] HMAC-SHA256 field hashing
- [ ] Human review queue API
- [ ] Blocking strategy (SSN hash, DOB, name prefix)
- [ ] Link source records to canonical identities with full audit trail

**Backend Engineer 2:**
- [ ] Kafka consumer for ICU vitals stream
- [ ] FHIR Observation creation at 1Hz (synthetic data)
- [ ] TimescaleDB hypertable compression + retention policy
- [ ] Batch ETL pipeline: Airflow DAG for nightly synthetic data ingestion
- [ ] Debezium CDC setup (on dev PostgreSQL)

**Backend Engineer 3:**
- [ ] SMART on FHIR OAuth2 flow implemented
- [ ] Epic sandbox integration (Epic FHIR Sandbox free account)
- [ ] Test: query synthetic patient from Epic sandbox → normalize → store
- [ ] Patient baseline computation service

**Full-stack Engineer:**
- [ ] ICU patient board with live vital signs (WebSocket)
- [ ] Risk badge component
- [ ] Alert notification system (toast notifications)

### Deliverable: End-to-End Data Flow
```
Synthetic Epic data → SMART on FHIR → FHIR normalizer → 
MPI matching → PostgreSQL + TimescaleDB → API → ICU board
```

---

## Month 4 — Vitals AI + Sepsis Detection

**Backend Engineer 3 (AI Lead, full focus):**
- [ ] TFT vitals model integration (PyTorch Forecasting or ONNX)
  - Download MIMIC-IV pretrained weights (physionet.org, free with credentialing)
  - Adapt input schema to our FHIR-normalized features
  - Monte Carlo Dropout wrapper for uncertainty quantification
- [ ] NEWS2, MEWS, SOFA scoring (rule-based, high reliability)
- [ ] Anomaly detection: Z-score against patient baseline
- [ ] Sepsis early warning: 12h prediction with calibrated probability
- [ ] Deterioration prediction: 6h prediction
- [ ] VitalsTFTEngine inference pipeline (<5 second target)

**Backend Engineer 1:**
- [ ] `/api/v1/predictions/{patient_id}` endpoint
- [ ] Prediction storage in TimescaleDB
- [ ] Background task: run predictions every 15 min per ICU patient
- [ ] Alert generation: NEWS2 ≥5, deterioration >0.70, sepsis >0.50
- [ ] Alert acknowledgment tracking

**Backend Engineer 2:**
- [ ] Outcome linkage pipeline (async job)
  - Link predictions to actual clinical events (synthetic for now)
  - Compute prediction accuracy metrics
- [ ] Model drift detector weekly snapshot

**Full-stack Engineer:**
- [ ] Live vitals chart with 60-minute history
- [ ] Sepsis probability gauge
- [ ] Risk distribution visualization
- [ ] Alert panel with acknowledge button

### Deliverable: Working AI Predictions
```
Patient vitals → TFT model → sepsis probability → alert →
physician sees "B-04: 72% sepsis probability, NEWS2=8, URGENT"
```

### Validation Gate (End of Month 4)
Before continuing: validate model performance on held-out synthetic data.
Target: AUROC >0.80 on synthetic data (real validation comes with hospital data).

---

## Month 5 — Multi-Agent System + LLM Reasoning

**Backend Engineer 3:**
- [ ] Triage agent (rule-based ESI scoring + LLM enhancement)
- [ ] Risk agent (15-min ICU polling loop)
- [ ] Pharmacist agent (drug-drug interaction database)
- [ ] Coordinator agent (confidence-weighted consensus)
- [ ] Escalation agent (SLA-enforced physician paging)
- [ ] Agent orchestrator with Redis state store
- [ ] Circuit breaker per agent (3 failures in 5 min → disable)
- [ ] 10-second hard timeout enforcement per agent

**Backend Engineer 1:**
- [ ] LLM reasoning engine
  - Azure OpenAI connection (HIPAA BAA in place)
  - 5-section prompt architecture
  - Output schema validation
  - JSON parse + retry logic (max 3 retries)
  - MedNLI consistency check (optional, Month 6+)
- [ ] Context prioritization: fill 52k token budget optimally
- [ ] Reasoning log table + explainability endpoints

**Backend Engineer 2:**
- [ ] `/api/v1/patients/{id}/intelligence` endpoint (main AI endpoint)
- [ ] WebSocket endpoint for real-time updates
- [ ] Agent status API
- [ ] Feedback capture API (`/api/v1/feedback`)

**Full-stack Engineer:**
- [ ] Full ICU command center (Dashboard.html fully functional)
- [ ] AI reasoning panel: differentials, recommended actions, data gaps
- [ ] Agent status panel
- [ ] Feedback thumbs up/down (connected to API)
- [ ] Clinical note: "AI Decision Support Only. Physician review required."

### Deliverable: Full AI Stack Working
```
Patient event → orchestrator → 5 agents run (parallel) →
coordinator synthesizes → LLM reasons → physician sees:
"Risk: HIGH | Sepsis 72% | Recommended: Blood cultures + lactate ASAP"
```

---

## Month 6 — Clinical Validation + Shadow Mode

### This month is about PROOF, not features

**All Engineers:**
- [ ] Retrospective validation on synthetic dataset
  - Test sepsis model against known outcomes in Synthea data
  - Compute AUROC, sensitivity, specificity
  - Document results (needed for FDA 510k)
- [ ] Shadow mode implementation
  - AI runs without physician visibility
  - Record AI recommendations vs actual clinical decisions
  - Measure: would AI have been helpful?
- [ ] Performance testing: 50,000 patient records load test
- [ ] Security: AWS penetration test (external vendor)
- [ ] SOC 2 Type II audit preparation

**Full-stack Engineer:**
- [ ] CFO Dashboard fully functional (CFODashboard.html)
- [ ] Compliance audit log viewer
- [ ] Model performance dashboard
- [ ] Admin patient management

**Business (Founder):**
- [ ] File pre-submission meeting request with FDA CDRH
- [ ] Target first hospital (warm introductions via medical advisor)
- [ ] Prepare pilot proposal document
- [ ] Engage HIPAA legal counsel for BAA template
- [ ] SOC 2 Type II engagement (audit firm)

### Deliverable: Shadow Mode Ready
```
Configure first hospital in system (even if not signed yet)
Upload their de-identified historical data
Run AI in shadow mode for 30 days
Show: "AI would have flagged these 12 sepsis cases 6 hours earlier"
```

---

## Month 7 — First Hospital Onboarding Preparation

**Backend Engineer 1 + 2 (Hospital Integration):**
- [ ] SMART on FHIR production flow (not just sandbox)
- [ ] Epic App Orchard submission prepared
- [ ] Hospital-specific threshold configuration UI
- [ ] Multi-hospital data isolation (RLS verified per hospital)
- [ ] Hospital admin onboarding flow
- [ ] Staff provisioning API (hospital IT provisions their users)

**Backend Engineer 3 (Model Refinement):**
- [ ] Clinical NLP pipeline: BioMedBERT NER
  - Entity extraction: diseases, medications, symptoms
  - Negation detection ("no fever" ≠ "fever")
  - Temporal reasoning: current vs historical events
- [ ] ICD-10 code suggestion from free text
- [ ] Semantic deduplication (cosine similarity >0.92 = duplicate)

**Full-stack Engineer:**
- [ ] Hospital admin panel
- [ ] User provisioning UI
- [ ] Epic-embedded component (for App Orchard submission)
- [ ] Mobile-responsive ICU board

**Business:**
- [ ] First hospital LOI (Letter of Intent) target
- [ ] Legal: finalize BAA template
- [ ] Hire first Customer Success Manager
- [ ] Medical advisor board: 2–3 hospitalist/ICU physicians

---

## Month 8 — Feedback Learning + Admin Intelligence

**Backend Engineer 3:**
- [ ] Feedback capture pipeline fully operational
  - Implicit: physician action tracking
  - Explicit: thumbs up/down collection
  - Outcome linkage: 30-day readmission tracking
- [ ] Model drift detector with alerting
- [ ] Weekly performance snapshot computation
- [ ] Bias monitoring: subgroup performance analysis

**Backend Engineer 2:**
- [ ] Admin intelligence layer
  - Readmission risk engine (LACE+ score)
  - LOS optimization (48h advance flags)
  - Bed management intelligence
  - CMS quality measure compliance tracking
- [ ] Financial impact reporting engine
- [ ] Auto-generated board report (PDF via WeasyPrint)

**Backend Engineer 1:**
- [ ] Model governance workflow
  - Approval workflow for model updates
  - Clinical validation test suite
  - Shadow mode for new model versions
- [ ] Federated learning foundation (NVIDIA FLARE setup for Month 12+)

**Full-stack Engineer:**
- [ ] Feedback history and analytics
- [ ] Readmission risk flags in discharge workflow
- [ ] LOS optimization alerts
- [ ] Quality measure compliance dashboard
- [ ] "Pending review" queue for human decisions

---

## Month 9 — Pilot Launch

**All hands: make the pilot successful**

**Week 1-2: Technical Onboarding**
- [ ] FHIR endpoint configured with hospital's Epic instance
- [ ] SMART on FHIR OAuth2 tested with hospital IT
- [ ] Staff accounts provisioned (20 ICU physicians + nurses)
- [ ] Alert thresholds calibrated to hospital protocols
- [ ] Integration smoke tests

**Week 3-4: Shadow Mode at Hospital**
- [ ] AI running in shadow (no physician visibility)
- [ ] Data quality monitoring: catch any FHIR normalization issues
- [ ] Performance monitoring: latency, accuracy, false positives
- [ ] Calibrate baselines on real patient population

**Week 5-12: Active Pilot**
- [ ] Physician-visible AI in ICU
- [ ] Weekly check-in: CMO + 2 ICU physicians + us
- [ ] Capture every feedback signal
- [ ] Track alert acknowledgment rates
- [ ] Incident response: any model errors get root cause in 24h

**Month 9 End Goals:**
```
✅ First hospital pilot running (live patients)
✅ FDA pre-submission meeting completed
✅ SOC 2 Type II certification received
✅ Second hospital in late-stage negotiation
✅ $300K ARR signed
✅ Series A preparation: pitch deck + financial model done
```

---

## Engineering Team Structure

```
4-person core team:
├── Backend Lead (Platform + Security)
│   Focus: AWS infra, HIPAA controls, API gateway, authentication
├── Backend Engineer 2 (Data Layer)
│   Focus: FHIR normalization, Kafka pipelines, database, ETL
├── AI/ML Engineer (AI Lead)
│   Focus: TFT model, agent system, LLM integration, validation
└── Full-stack Engineer
    Focus: Physician UI, Epic integration, dashboards
```

**Hiring at Month 6 (when funding allows):**
- Clinical Informaticist (MD or RN with informatics background)
  - Validates AI outputs, physician champion, FDA submission
- DevOps/SRE Engineer
  - Kubernetes production deployment, observability, on-call
- Customer Success Manager
  - Hospital onboarding, pilot management, renewal

---

## Definition of Done — Per Feature

Every feature must meet ALL of these before marking done:

1. **Code**: Implemented, reviewed, merged to main
2. **Tests**: Unit tests (>80% coverage), integration test
3. **HIPAA check**: ABAC verified, audit log verified, no PHI leakage
4. **Documentation**: Updated in relevant .md file
5. **Observability**: Prometheus metrics + Grafana dashboard
6. **Security**: No hardcoded secrets, no debug endpoints in prod

---

## Technical Debt Register

Things we're knowingly deferring (track these, don't lose them):

| Item | Deferred to | Reason |
|------|------------|--------|
| Full MedNLI consistency check | Month 7 | Model download/fine-tuning takes time |
| Federated learning | Month 12+ | Need 3+ hospitals first |
| Voice documentation | Phase 2 | Low pilot priority, high complexity |
| Imaging AI (BioViL-T) | Month 8 | Need DICOM integration first |
| Full Debezium CDC | Month 4 | Start with batch ETL, migrate to CDC |
| Real Micromedex API | Month 8 | Using internal DDI database in pilot |
| NVIDIA FLARE federated | Month 12 | Need multi-hospital first |

---

*This roadmap is reviewed bi-weekly in engineering sync. Dates shift based on hospital timeline.*  
*Owner: CTO + Founders*  
*Last updated: Q1 2026*
