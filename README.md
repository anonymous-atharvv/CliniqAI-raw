# 🏥 CliniQAI — Hospital Intelligence Platform

> **The self-improving clinical intelligence network.**  
> Not features. Not a chatbot. A compounding data moat that gets smarter with every patient, every outcome, every physician interaction.

---

## 🧭 What This Is

CliniQAI is a production-grade, HIPAA-compliant, FDA SaMD-aware hospital AI platform targeting **community hospitals (200–400 beds)**. It is built as a **9-layer intelligent system** — not a dashboard with AI bolted on.

**The real product**: An outcome-validated, continuously-improving clinical intelligence network that compounds in value with every hospital added.

---

## 🏗️ System Architecture (9 Layers)

```
┌─────────────────────────────────────────────────────────────┐
│  LAYER 7: Admin Intelligence + Business (CFO Dashboard)     │
│  LAYER 8: Trust, Safety + Model Governance (FDA SaMD)       │
├─────────────────────────────────────────────────────────────┤
│  LAYER 6: Feedback Learning System (YOUR MOAT)              │
├─────────────────────────────────────────────────────────────┤
│  LAYER 5: Multi-Agent Orchestration (LangGraph)             │
│  LAYER 4: LLM Reasoning Engine (Claude / GPT-4o)            │
├─────────────────────────────────────────────────────────────┤
│  LAYER 3: Multi-Modal AI (Imaging + NLP + Time-Series)      │
├─────────────────────────────────────────────────────────────┤
│  LAYER 2: Compliance + Security Gateway (HIPAA/ABAC)        │
├─────────────────────────────────────────────────────────────┤
│  LAYER 1: Data Integration Layer (FHIR R4 + CDC)            │
└─────────────────────────────────────────────────────────────┘
```

---

## 📁 Repository Structure

```
cliniqai/
├── README.md                    ← You are here
├── ARCHITECTURE.md              ← Full ADR (Architecture Decision Record)
├── ROADMAP.md                   ← 9-month build sequence
├── COMPLIANCE.md                ← HIPAA + FDA SaMD compliance guide
│
├── backend/
│   ├── api/                     ← FastAPI REST + WebSocket endpoints
│   │   ├── v1/
│   │   │   ├── patients.py
│   │   │   ├── vitals.py
│   │   │   ├── inference.py
│   │   │   ├── agents.py
│   │   │   └── admin.py
│   │   └── middleware/
│   │       ├── auth.py          ← JWT + ABAC enforcement
│   │       ├── audit.py         ← Immutable audit logging
│   │       └── compliance.py    ← PHI de-identification gateway
│   │
│   ├── services/
│   │   ├── fhir/                ← FHIR R4 normalization engine
│   │   │   ├── normalizer.py
│   │   │   ├── loinc_mapper.py
│   │   │   └── smart_auth.py    ← SMART on FHIR OAuth2
│   │   │
│   │   ├── ingestion/           ← Data integration layer
│   │   │   ├── batch_etl.py     ← Nightly HL7/CDA pipeline
│   │   │   ├── stream_icu.py    ← Real-time MQTT/Kafka
│   │   │   ├── mpi.py           ← Master Patient Index
│   │   │   └── quality_score.py ← Data quality scoring
│   │   │
│   │   ├── compliance/          ← Security gateway
│   │   │   ├── deidentify.py    ← Safe Harbor 18 PHI identifiers
│   │   │   ├── abac.py          ← Attribute-based access control
│   │   │   ├── consent.py       ← Consent state management
│   │   │   └── breach_detect.py ← Anomaly monitoring
│   │   │
│   │   ├── ai/                  ← Multi-modal AI layer
│   │   │   ├── imaging.py       ← MONAI + BioViL-T pipeline
│   │   │   ├── nlp.py           ← BioMedBERT NER + temporal
│   │   │   ├── vitals_tft.py    ← Temporal Fusion Transformer
│   │   │   └── fusion.py        ← Cross-modal output fusion
│   │   │
│   │   ├── reasoning/           ← LLM reasoning engine
│   │   │   ├── engine.py        ← Claude/GPT-4o orchestration
│   │   │   ├── prompt_builder.py ← 5-section prompt architecture
│   │   │   ├── validator.py     ← Output schema validation
│   │   │   └── context_mgr.py   ← Token budget management
│   │   │
│   │   ├── agents/              ← Multi-agent system (LangGraph)
│   │   │   ├── triage.py
│   │   │   ├── diagnosis.py
│   │   │   ├── risk.py
│   │   │   ├── pharmacist.py    ← Drug-drug interaction
│   │   │   ├── documentation.py ← SOAP note generation
│   │   │   ├── coordinator.py   ← Conflict resolution
│   │   │   └── escalation.py    ← Critical alert routing
│   │   │
│   │   ├── feedback/            ← Learning system (YOUR MOAT)
│   │   │   ├── capture.py       ← Implicit + explicit signals
│   │   │   ├── outcome_linker.py ← Outcome validation pipeline
│   │   │   ├── drift_detector.py ← Model performance monitoring
│   │   │   └── federated.py     ← NVIDIA FLARE integration
│   │   │
│   │   └── admin/               ← Business intelligence layer
│   │       ├── readmission.py   ← CMS HRRP tracking
│   │       ├── los_optimizer.py ← Length-of-stay prediction
│   │       ├── bed_mgmt.py      ← Real-time bed intelligence
│   │       └── cost_analytics.py ← CFO-level reporting
│   │
│   ├── models/                  ← Database models (SQLAlchemy)
│   │   ├── patient.py
│   │   ├── encounter.py
│   │   ├── observation.py
│   │   ├── audit_log.py
│   │   └── feedback.py
│   │
│   └── config/
│       ├── settings.py          ← Environment configuration
│       ├── database.py          ← Multi-DB connection (PG + Redis + Qdrant)
│       └── security.py          ← KMS + encryption config
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Dashboard.jsx    ← Physician command center
│   │   │   ├── PatientView.jsx  ← Full patient intelligence panel
│   │   │   ├── ICUMonitor.jsx   ← Real-time ICU board
│   │   │   ├── AdminPanel.jsx   ← CFO/COO business dashboard
│   │   │   └── Compliance.jsx   ← Audit + governance view
│   │   │
│   │   ├── components/
│   │   │   ├── PatientCard.jsx
│   │   │   ├── VitalsChart.jsx
│   │   │   ├── RiskBadge.jsx
│   │   │   ├── AIRecommendation.jsx
│   │   │   ├── AgentStatus.jsx
│   │   │   └── FeedbackButton.jsx
│   │   │
│   │   └── services/
│   │       ├── api.js
│   │       └── websocket.js
│   │
│   └── public/
│
├── infrastructure/
│   ├── docker-compose.yml       ← Full local stack
│   ├── docker-compose.prod.yml
│   ├── kubernetes/              ← K8s manifests
│   │   ├── namespace.yaml
│   │   ├── backend-deploy.yaml
│   │   ├── kafka-statefulset.yaml
│   │   └── postgres-statefulset.yaml
│   ├── terraform/               ← AWS infrastructure as code
│   │   ├── main.tf
│   │   ├── rds.tf
│   │   ├── eks.tf
│   │   └── kms.tf
│   └── nginx/
│       └── nginx.conf
│
├── scripts/
│   ├── seed_synthea.py          ← Load synthetic patient data
│   ├── migrate_db.py            ← Database migrations
│   ├── validate_fhir.py         ← FHIR R4 validation tests
│   └── perf_test.py             ← Load testing (50k patients)
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── clinical/                ← Clinical validation test suite
│
└── docs/
    ├── adr/                     ← Architecture Decision Records
    │   ├── ADR-001-fhir-r4.md
    │   ├── ADR-002-kafka-vs-rabbitmq.md
    │   └── ADR-003-llm-selection.md
    ├── api/                     ← OpenAPI specs
    └── compliance/
        ├── hipaa-controls.md
        ├── fda-samd-classification.md
        └── baa-template.md
```

---

## 🚀 Quick Start (Development)

```bash
# 1. Clone and setup
git clone https://github.com/your-org/cliniqai
cd cliniqai
cp .env.example .env  # Fill in your API keys

# 2. Start all services
docker-compose up -d

# 3. Load synthetic patient data (NEVER use real data in dev)
python scripts/seed_synthea.py --patients 1000

# 4. Run database migrations
python scripts/migrate_db.py

# 5. Start backend
cd backend && uvicorn main:app --reload

# 6. Start frontend
cd frontend && npm install && npm run dev
```

**Services started by docker-compose:**
- PostgreSQL + TimescaleDB (port 5432)
- Redis (port 6379)
- Apache Kafka + Zookeeper (port 9092)
- Qdrant vector store (port 6333)
- Orthanc DICOM server (port 8042)
- Prometheus + Grafana (port 3001)

---

## 🔑 Technology Stack

| Layer | Technology | Why |
|-------|-----------|-----|
| API Framework | FastAPI | Async, auto-OpenAPI, Python ecosystem |
| FHIR Normalization | python-fhir + hapi-fhir | Production-grade FHIR R4 |
| Message Streaming | Apache Kafka | Hospital-scale throughput |
| Time-Series DB | PostgreSQL + TimescaleDB | Native time-series on battle-tested DB |
| Vector Store | Qdrant (self-hosted) | HIPAA: data stays on-premise |
| DICOM | Orthanc + S3 backend | Open source, production hospitals use it |
| LLM Reasoning | Claude Sonnet / GPT-4o | Configurable, vendor-agnostic |
| Agent Framework | LangGraph | Not AutoGen (too verbose for real-time) |
| Compliance | Custom ABAC + JWT | HIPAA RBAC requirements |
| Infrastructure | AWS EKS + KMS | HIPAA-eligible services with BAA |
| Monitoring | Prometheus + Grafana | Standard hospital IT stack |
| CI/CD | GitHub Actions + ArgoCD | GitOps for regulated environment |

---

## 💰 Business Model

| Year | Scope | MRR | ARR |
|------|-------|-----|-----|
| Y1 | 50-bed ICU pilot × 1 hospital | $25k | $300k |
| Y2 | Full hospital × 3 hospitals | $240k | $2.88M |
| Y3 | 10 hospitals + outcome bonus | ~$1.4M | ~$17M |

**Series A trigger**: 10 hospitals, $15M ARR trajectory, FDA clearance in hand.

---

## ⚖️ Compliance Status

- [ ] HIPAA Technical Safeguards — Implemented in Layer 2
- [ ] BAA Template — See `docs/compliance/baa-template.md`
- [ ] FDA SaMD Class II — See `docs/compliance/fda-samd-classification.md`
- [ ] FHIR R4 Certification — Validated against HL7 test suite
- [ ] SOC 2 Type II — Target: Month 8

---

## 📞 Critical Contacts (Template for Hospital Deployment)

- **HIPAA Privacy Officer**: ATHARV MAURYA
- **Security Officer**: ATHARV MAURYA
- **Clinical Validation Lead**: [MD/PhD advisor]
- **FDA Regulatory Counsel**: [law firm]

---

*"The AI is not the product. The outcome-validated, continuously-improving clinical intelligence network is the product."*
