# CliniQAI — Compliance Reference

> **For hospital IT teams, legal counsel, and compliance officers evaluating CliniQAI.**

---

## Compliance Summary

| Framework | Status | Details |
|-----------|--------|---------|
| HIPAA Technical Safeguards (45 CFR §164.312) | ✅ Implemented | Full technical safeguards — AES-256, TLS 1.3, ABAC, audit logging |
| HIPAA Administrative Safeguards | ✅ Implemented | Policies, training, BAA, breach response |
| HIPAA Physical Safeguards | ✅ (via AWS) | AWS HIPAA-eligible services with BAA |
| BAA Available | ✅ Yes | Standard template — see `docs/compliance/baa-template.md` |
| FDA SaMD Class II | 🔄 Pending | 510k submission target Q3 2026 — see `docs/compliance/fda-samd-classification.md` |
| SOC 2 Type II | 🔄 In progress | Target: Month 8 |
| FHIR R4 Certified | ✅ Validated | Validated against HL7 test suite + Epic sandbox |

---

## HIPAA Technical Safeguards (45 CFR §164.312)

### §164.312(a)(1) — Access Control
**Implementation:** Attribute-Based Access Control (ABAC) engine in `services/compliance/gateway.py`

- **Unique user identification** — Every user has a UUID assigned at provisioning. No shared accounts.
- **Emergency access** — Break-glass procedure with enhanced logging and 24h review requirement.
- **Automatic logoff** — 60-minute session timeout. JWT expires at 60 minutes.
- **Encryption/decryption** — AES-256-GCM for PHI at rest. AWS KMS for key management.

### §164.312(b) — Audit Controls
**Implementation:** Immutable audit logging in `api/middleware/audit.py`

Every data access generates an `AuditEvent`:
- Actor ID, role, department, hospital
- Action (read/write/infer/export), resource type, resource ID (de-identified)
- Outcome (success/denied), IP hash, timestamp, duration
- Stored in: PostgreSQL (queryable) + AWS S3 WORM (immutable archive)
- Retention: 6 years minimum (HIPAA requirement)

### §164.312(c)(1) — Integrity
- Database: PostgreSQL ACID transactions
- Kafka: Message checksums on ingestion
- FHIR: Resource versioning (`meta.versionId`)
- Dead letter queue for failed/corrupted messages

### §164.312(e)(1) — Transmission Security
- TLS 1.3 enforced for all HTTPS connections (TLS 1.0, 1.1 disabled)
- Kafka: TLS with SASL/SCRAM-SHA-512
- VPN for hospital-to-cloud data transfer

---

## PHI De-Identification

CliniQAI implements **HIPAA Safe Harbor** de-identification (45 CFR §164.514(b)).

All 18 PHI identifiers are handled:

| # | Identifier | Treatment |
|---|-----------|-----------|
| 1 | Names | → UUID pseudonym (HMAC-SHA256) |
| 2 | Geographic (< state) | → State + 3-digit ZIP prefix |
| 3 | Dates (except year) | → Shift ±90 days (consistent per patient) |
| 4-6 | Phone/Fax/Email | → Removed |
| 7 | SSN | → UUID pseudonym |
| 8 | MRN | → UUID pseudonym |
| 9-11 | Health plan/Account/Certificate # | → UUID pseudonym or removed |
| 12-14 | Vehicle/Device/URL identifiers | → Removed |
| 15 | IP addresses | → SHA-256 hashed |
| 16 | Biometric identifiers | → Hashed with patient-specific salt |
| 17 | Full-face photos | → Not stored in AI layer |
| 18 | Other unique identifiers | → UUID pseudonym |

**Preserved:** Birth year, state, clinical codes (ICD-10, SNOMED, LOINC, RxNorm)

**Critical rule:** PHI is NEVER sent to external LLM APIs (Claude, GPT-4o). De-identification occurs before any AI processing. The LLM sees de-identified patient context only.

---

## Consent Management

| Consent Type | Default | Mechanism |
|-------------|---------|-----------|
| Treatment use | Always ON | Required by law — cannot opt out |
| AI inference | Hospital policy | Opt-in or opt-out per hospital configuration |
| Research use | Explicit opt-in | Patient must actively consent |
| Data sharing | Explicit opt-in | Patient must actively consent |

Before any AI inference: `ConsentManager.can_use_for_ai()` is called. If False → de-identified data only.

---

## Breach Detection & Response

**Detection thresholds (automated):**
- > 50 records accessed outside department in 1 hour → immediate alert
- After-hours access to sensitive categories (HIV, psych, substance abuse) → alert
- Bulk export requests → alert
- > 10 failed authentication attempts in 5 minutes → account lockout

**Response timeline:**
- Detection → Security team alert: 15 minutes (internal SLA)
- Security team → Privacy Officer: 1 hour
- Privacy Officer → HHS notification: 60 days (HIPAA requirement)
- Privacy Officer → Affected individuals: 60 days

---

## FDA SaMD Classification

CliniQAI is classified as **Class II Software as a Medical Device (SaMD)**.

**Why Class II (not III):** All AI outputs require physician review. The system cannot autonomously order medications, procedures, or transfers. Human-in-the-loop design prevents autonomous clinical action.

**510(k) predicate:** Epic Sepsis Model (K203264)

**Filing timeline:**
- Month 3: Pre-submission meeting request
- Month 6: Pre-submission meeting with FDA
- Month 9: 510(k) submission
- Month 21-24: Expected clearance

See full detail: `docs/compliance/fda-samd-classification.md`

---

## Key Contacts

| Role | Responsibility |
|------|---------------|
| HIPAA Privacy Officer | Privacy policies, patient rights, breach notification |
| HIPAA Security Officer | Technical safeguards, access controls, incident response |
| Clinical Validation Lead | FDA submission, model validation |
| Legal Counsel | BAA review, regulatory notifications |
| Engineering Security Lead | Pen testing, vulnerability management |

---

## Quick Reference: What to Send Hospital IT

When a hospital IT team asks for security documentation, provide:

1. This document (`COMPLIANCE.md`)
2. BAA template (`docs/compliance/baa-template.md`) — for legal review
3. `docs/compliance/hipaa-controls.md` — detailed technical controls
4. SOC 2 Type II report (available Month 8+)
5. Penetration test report (available Month 6+)
6. FDA SaMD classification (`docs/compliance/fda-samd-classification.md`)
7. AWS HIPAA-eligible services documentation (from AWS)

**Common IT questions and answers:**

| Question | Answer |
|----------|--------|
| Where is data stored? | AWS ap-south-1 (India) or customer-specified region. All HIPAA-eligible services. |
| Does CliniQAI see patient names? | No. De-identification occurs at ingestion. LLM never sees PHI. |
| Can we keep data on-premise? | Yes. Qdrant (vector store) is self-hosted by default. On-premise option available. |
| What if we need to terminate? | All PHI returned or destroyed within 30 days. Documented certificate provided. |
| Is PHI sent to OpenAI/Anthropic? | No. De-identified data only reaches external LLMs. |
| Can we audit all data access? | Yes. Immutable audit log with 6-year retention. Available for hospital review. |
