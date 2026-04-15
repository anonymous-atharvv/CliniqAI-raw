# Architecture Decision Record — CliniQAI Platform

**Version**: 1.0  
**Date**: 2024  
**Status**: Active  
**Authors**: Engineering Team

---

## ADR-001: Overall System Architecture

### Decision
Adopt a layered, event-driven microservices architecture with a compliance gateway as a mandatory passthrough for all data flows.

### Context
- Target: Community hospitals (200–400 beds) running Epic, Cerner, Meditech on-premise
- Must handle 50,000+ patient records + real-time ICU streams at 1Hz per device
- 4-person engineering team → simplicity is a primary constraint
- HIPAA compliance is non-negotiable from Day 1
- No hospital will sign a BAA without demonstrated technical safeguards

### Recommended Stack

**Data Integration:**
- Apache Kafka for streaming (ICU monitors → MQTT bridge → Kafka)
- Apache Airflow for batch ETL orchestration (nightly HL7/CDA)
- Debezium for CDC (Change Data Capture) on hospital databases
- python-fhir + custom normalizer for FHIR R4 conversion

**Storage:**
- Hot: PostgreSQL 16 + TimescaleDB extension (vitals time-series)
- Warm: Apache Parquet on S3 (historical records, columnar for analytics)
- Cold: AWS Glacier (7-year HIPAA archive)
- Vector: Qdrant self-hosted (patient embeddings, semantic search)
- DICOM: Orthanc server + S3 backend (imaging)
- Cache: Redis 7 (agent state, session data, TTL-based)

**AI/ML:**
- LangGraph for multi-agent orchestration
- Claude Sonnet via Azure OpenAI (HIPAA BAA available)
- MONAI Deploy for imaging inference pipeline
- BioMedBERT / GatorTron for clinical NLP
- Temporal Fusion Transformer (MIMIC-IV pretrained) for vitals

**Compliance:**
- Custom ABAC engine (attribute-based access control)
- JWT with embedded clinical claims, validated at API gateway
- AWS KMS for key management
- AWS WORM buckets for immutable audit logs

### Justification
- **Kafka over RabbitMQ**: ICU at 1Hz per device × 100 devices = 360,000 messages/hour. Kafka handles this. RabbitMQ does not scale gracefully here.
- **TimescaleDB over InfluxDB**: Hospital IT teams know PostgreSQL. TimescaleDB gives time-series performance without a foreign database.
- **Qdrant over Pinecone**: Self-hosted = data stays on-premise = HIPAA safe harbor without data egress risk.
- **LangGraph over AutoGen**: AutoGen is verbose. LangGraph gives us deterministic state machines with 10-second hard timeouts — critical for real-time clinical use.
- **Claude/GPT-4o over open-source LLMs**: 128k context window needed for full patient context. No open-source model matches performance at this context length reliably. Azure OpenAI provides HIPAA BAA.

---

## ADR-002: FHIR R4 Implementation Strategy

### Decision
Implement FHIR R4 + SMART on FHIR authentication. Use SMART on FHIR OAuth2 for Epic/Cerner integration.

### LOINC Code Mapping (Non-Negotiable)

| Vital Sign | LOINC Code | Display |
|-----------|-----------|---------|
| Heart Rate | 8867-4 | Heart rate |
| SpO2 | 2708-6 | Oxygen saturation in Arterial blood |
| SpO2 (Pulse Ox) | 59408-5 | Oxygen saturation by Pulse oximetry |
| Blood Pressure Systolic | 8480-6 | Systolic blood pressure |
| Blood Pressure Diastolic | 8462-4 | Diastolic blood pressure |
| Body Temperature | 8310-5 | Body temperature |
| Respiratory Rate | 9279-1 | Respiratory rate |
| MAP | 8478-0 | Mean blood pressure |
| GCS Total | 9269-2 | Glasgow coma score total |

### FHIR Resource Mapping

| Source | FHIR R4 Resource |
|--------|-----------------|
| Patient demographics | Patient |
| Lab result | Observation (category: laboratory) |
| Vital sign | Observation (category: vital-signs) |
| Medication order | MedicationRequest |
| Diagnosis | Condition |
| Clinical note | DocumentReference |
| Imaging study | ImagingStudy + DiagnosticReport |
| Allergy | AllergyIntolerance |
| Procedure | Procedure |

### SMART on FHIR Scopes Required

```
patient/*.read
openid profile
launch/patient
online_access
```

---

## ADR-003: Patient Identity Resolution (MPI)

### Algorithm: Probabilistic Matching

**Matching Fields and Weights:**

| Field | Weight | Match Type |
|-------|--------|-----------|
| SSN (last 4) | 0.35 | Exact |
| MRN | 0.30 | Exact |
| Date of Birth | 0.15 | Exact |
| Last Name | 0.10 | Jaro-Winkler ≥ 0.92 |
| First Name | 0.05 | Jaro-Winkler ≥ 0.90 |
| Address (zip) | 0.05 | Exact |

**Confidence Score Rules:**
- ≥ 0.95: Auto-link (with audit trail)
- 0.80–0.94: Flag for human review (do not auto-merge)
- < 0.80: Create new record, flag as potential duplicate

**NEVER auto-merge without:**
1. Complete audit trail entry
2. Original records preserved
3. Merge reason documented

---

## ADR-004: LLM Prompt Architecture

### 5-Section System Prompt Structure

```
SECTION 1: Role + Constraints (static, ~200 tokens)
SECTION 2: Patient Context (dynamic, priority-ordered, fills to context limit)
SECTION 3: Task Specification (CoT clinical reasoning steps, ~500 tokens)
SECTION 4: Output Schema (JSON schema enforcement, ~300 tokens)  
SECTION 5: Guardrails (rejection criteria, ~200 tokens)
```

### Context Prioritization Formula

```python
priority_score = (
    0.40 * recency_score +      # Last 2 hours weighted highest
    0.35 * severity_score +     # Abnormal findings boosted
    0.25 * semantic_similarity  # Relevance to current complaint
)
```

### Token Budget Allocation (128k context)

| Section | Token Budget |
|---------|-------------|
| System prompt (static) | 2,000 |
| Current vitals + alerts | 5,000 |
| Current medications + problems | 8,000 |
| Recent labs (24h) | 10,000 |
| Imaging findings | 8,000 |
| NLP clinical summary | 6,000 |
| Relevant history (vector DB) | 15,000 |
| Remaining | Available for extended context |

---

## ADR-005: Data Quality Scoring

### Composite Score Formula

```
quality_score = (
    0.30 * completeness_score +
    0.25 * timeliness_score +
    0.25 * consistency_score +
    0.20 * validity_score
)
```

**Scoring Definitions:**

- **Completeness**: % of required FHIR fields populated (0–1)
- **Timeliness**: `exp(-λt)` where t = hours since last update, λ = 0.05
- **Consistency**: 1 - (conflicting_field_count / total_field_count)
- **Validity**: % of values within clinical reference ranges

**Threshold**: score < 0.60 → record flagged, excluded from AI inference

---

## Risk Register (Top 5 Risks)

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| HIPAA breach via data egress | Medium | Critical | All data self-hosted; de-identify before any external API call; TLS 1.3 everywhere |
| LLM hallucination on clinical output | High | Critical | Output schema validation; MedNLI consistency check; mandatory human review for HIGH/CRITICAL risk |
| Epic/Cerner integration rejection | Medium | High | SMART on FHIR OAuth2 compliance; Epic App Orchard certification pathway; start with read-only scope |
| Model drift on seasonal disease patterns | Medium | Medium | Weekly drift monitoring; 5% accuracy drop threshold triggers freeze; quarterly clinical review board |
| Agent timeout in real-time clinical scenario | Low | High | 10-second hard timeout per agent; coordinator bypass to raw outputs; rule-based fallback always available |

---

## Implementation Sequence (9 Months)

### Month 1–2: Foundation
- [ ] Infrastructure setup (Docker Compose dev, AWS staging)
- [ ] PostgreSQL + TimescaleDB schema design
- [ ] HIPAA controls implementation (ABAC, audit logging, encryption)
- [ ] Basic FHIR R4 normalizer (Patient, Observation, MedicationRequest)
- [ ] Synthea data generator integration (1,000 synthetic patients)

### Month 3–4: Data Layer
- [ ] Kafka streaming pipeline (ICU MQTT bridge)
- [ ] Batch ETL (HL7 v2 + CDA parser)
- [ ] Master Patient Index (probabilistic matching)
- [ ] Data quality scoring engine
- [ ] SMART on FHIR OAuth2 (Epic sandbox integration)
- [ ] Dead-letter queue + retry logic

### Month 5–6: AI Layer
- [ ] Vitals anomaly detection (rule-based first, then TFT)
- [ ] Sepsis early warning model (MIMIC-IV pretrained)
- [ ] Clinical NLP pipeline (BioMedBERT NER)
- [ ] LLM reasoning engine (5-section prompt architecture)
- [ ] Output validation layer

### Month 7: Agent System
- [ ] Triage agent
- [ ] Risk agent (15-min ICU polling)
- [ ] Pharmacist agent (drug-drug interaction)
- [ ] Coordinator agent
- [ ] Escalation agent (2-min physician page SLA)

### Month 8: Product + Compliance
- [ ] Physician-facing UI (Epic-embedded or standalone)
- [ ] Admin/CFO dashboard
- [ ] Feedback capture system
- [ ] Clinical validation test suite
- [ ] FDA SaMD documentation package
- [ ] Shadow mode deployment capability

### Month 9: Pilot Ready
- [ ] First hospital pilot (ICU, sepsis prediction use case)
- [ ] BAA execution workflow
- [ ] Outcome tracking integration (billing data linkage)
- [ ] Series A metrics dashboard
- [ ] FDA 510k pre-submission preparation
