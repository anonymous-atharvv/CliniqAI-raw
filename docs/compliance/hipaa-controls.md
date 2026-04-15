# HIPAA Compliance Documentation — CliniQAI Platform

**Version**: 2.0  
**Classification**: Confidential — Internal Use Only  
**Last Updated**: 2026  
**Owner**: HIPAA Privacy & Security Officer  

---

## Executive Summary

CliniQAI is a Business Associate (BA) under HIPAA, providing a Software as a Medical Device (SaMD) platform to Covered Entities (hospitals). This document details our Technical Safeguards implementation per 45 CFR §164.312.

**Compliance Status:**
- HIPAA Technical Safeguards: ✅ IMPLEMENTED
- HIPAA Administrative Safeguards: ✅ IMPLEMENTED  
- HIPAA Physical Safeguards: ✅ (via AWS HIPAA-eligible services)
- BAA Available: ✅ YES
- FDA SaMD Class II (510k): 🔄 IN PROGRESS

---

## 1. Technical Safeguards (45 CFR §164.312)

### 1.1 Access Control (§164.312(a)(1))

**Implementation: Attribute-Based Access Control (ABAC)**

Our ABAC engine evaluates every data access against four dimensions:
- **Actor role**: physician | nurse | radiologist | pharmacist | admin | researcher | AI_system
- **Care relationship**: treating_patient | consulting | no_relationship
- **Data sensitivity**: standard | sensitive (HIV, psych, substance abuse, genetics)
- **Time context**: active_shift | on_call | after_hours

**Rules (abbreviated — full ruleset in `backend/services/compliance/gateway.py`):**

| Role | Standard PHI | Sensitive PHI | AI Inference |
|------|-------------|--------------|--------------|
| Treating Physician | ✅ Full access | ✅ With documentation | ✅ Read only |
| Nurse (assigned) | ✅ Vitals + Meds | ❌ Not permitted | ✅ De-identified |
| AI System | ❌ Never | ❌ Never | ✅ De-identified only |
| Researcher | ❌ | ❌ | ✅ IRB approved + de-id only |

**Technical implementation:**
```python
# Every API request passes through ABACEngine.check_access()
allowed, reason = gateway.abac.check_access(access_request)
if not allowed:
    # Audit log the denial
    gateway.audit.log_access(..., outcome="denied")
    raise HTTPException(403, reason)
```

**Unique User Identification (§164.312(a)(2)(i)):**
- Every user has a UUID assigned at provisioning
- JWTs contain user UUID, role, department, and care assignments
- No shared accounts permitted (technical enforcement)

**Emergency Access Procedure (§164.312(a)(2)(ii)):**
- Break-glass procedure available for treating physicians in emergencies
- Break-glass access is logged with enhanced audit detail
- Security team alerted within 15 minutes of break-glass use
- Review required within 24 hours

**Automatic Logoff (§164.312(a)(2)(iii)):**
- Session timeout: 60 minutes of inactivity
- JWT access tokens expire after 60 minutes
- Refresh tokens expire after 7 days
- Epic-embedded sessions follow Epic's session policy

### 1.2 Audit Controls (§164.312(b))

**Implementation: Immutable Append-Only Audit Log**

Every data access generates an `AuditEvent` stored in:
1. PostgreSQL `cliniqai_audit.access_log` (TimescaleDB, fast queries)
2. AWS S3 WORM bucket (write-once, cannot be deleted or modified)
3. Both stores are cryptographically linked (hash chain)

**Audit event schema:**
```json
{
  "event_id": "uuid",
  "event_timestamp": "ISO8601",
  "actor": "user_uuid",
  "actor_role": "physician",
  "action": "read|write|infer|export",
  "resource_type": "Patient|Observation|MedicationRequest",
  "resource_id": "de-identified-uuid",
  "access_reason": "treatment|operations|research|payment",
  "outcome": "success|denied",
  "ip_hash": "sha256-hash-of-ip",
  "phi_accessed": false,
  "deidentified_data": true
}
```

**Retention: 6 years (HIPAA minimum)**

**Breach detection queries:**
```sql
-- Find suspicious bulk access (>50 patients in 1 hour)
SELECT actor_id, COUNT(DISTINCT resource_id) as patients_accessed
FROM cliniqai_audit.access_log
WHERE event_timestamp > NOW() - INTERVAL '1 hour'
GROUP BY actor_id
HAVING COUNT(DISTINCT resource_id) > 50;
```

### 1.3 Integrity (§164.312(c)(1))

**Data integrity mechanisms:**
- All database writes use PostgreSQL transactions (ACID)
- SHA-256 checksums on FHIR resource storage
- Kafka message checksums on ingestion
- FHIR resource versioning (`meta.versionId`)
- Dead letter queue for failed/corrupted messages
- Database checksums enabled on all PostgreSQL tables

**Transmission integrity:**
- TLS 1.3 for all data in transit
- Message authentication codes on Kafka messages
- FHIR resource digital signatures (optional, hospital-configurable)

### 1.4 Transmission Security (§164.312(e)(1))

**Encryption in transit:**
- TLS 1.3 enforced for all HTTPS connections
- TLS 1.2 minimum (TLS 1.0 and 1.1 disabled)
- Certificate pinning for Epic/Cerner integration endpoints
- VPN required for hospital-to-cloud data transfer (site-to-site IPSec)
- Kafka TLS with SASL/SCRAM-SHA-512 authentication

**Cipher suites (TLS 1.3 only):**
- TLS_AES_256_GCM_SHA384
- TLS_CHACHA20_POLY1305_SHA256
- TLS_AES_128_GCM_SHA256

---

## 2. De-identification (45 CFR §164.514(b))

### 2.1 Safe Harbor Method — 18 PHI Identifiers

Our `DeIdentificationEngine` removes or transforms all 18 Safe Harbor identifiers:

| # | PHI Identifier | Our Treatment |
|---|----------------|---------------|
| 1 | Names | Replace with UUID pseudonym (HMAC-SHA256) |
| 2 | Geographic data (< state) | Generalize to state + 3-digit ZIP prefix |
| 3 | Dates (except year) | Shift ±90 days (consistent per patient, patient-seed-based) |
| 4 | Phone numbers | Remove entirely |
| 5 | Fax numbers | Remove entirely |
| 6 | Email addresses | Remove entirely |
| 7 | SSN | Replace with pseudonym (HMAC-SHA256) |
| 8 | Medical record numbers | Replace with pseudonym |
| 9 | Health plan numbers | Replace with pseudonym |
| 10 | Account numbers | Replace with pseudonym |
| 11 | Certificate/license numbers | Remove |
| 12 | Vehicle identifiers | Remove |
| 13 | Device identifiers | Replace with device pseudonym |
| 14 | Web URLs | Remove / replace |
| 15 | IP addresses | Hash (SHA-256) |
| 16 | Biometric identifiers | Hash with patient-specific salt |
| 17 | Full-face photographs | Not stored in AI layer |
| 18 | Any unique identifying numbers | Replace with pseudonym |

**Preserved (Safe Harbor allows):**
- Year of birth
- State of residence  
- Clinical codes (ICD-10, SNOMED, LOINC, RxNorm)
- Vital sign values (not PHI by themselves)

**Age > 89 rule:**
Per Safe Harbor, ages over 89 must not be disclosed. Our system:
```python
def generalize_age(age: int) -> str:
    return "90+" if age > 89 else str(age)
```

### 2.2 Expert Determination Method

For research use cases requiring more data utility, we can apply Expert Determination (alternative to Safe Harbor). This requires:
- Statistical analysis showing re-identification risk < 0.09
- Sign-off from qualified privacy statistician
- IRB approval
- Enhanced BAA terms

### 2.3 Original-to-De-identified Mapping

The mapping between original PHI and pseudonyms is stored in a **separate encrypted store** (HashiCorp Vault) with:
- Different access controls than clinical data
- Requires dual authorization for access
- Separate audit log
- Air-gapped from AI processing systems

This mapping is NEVER stored in the same database as AI-processed data.

---

## 3. Breach Notification (45 CFR §164.400)

### 3.1 Breach Detection

Our `BreachDetector` monitors for:
- User accessing >50 records outside their department in 1 hour
- API calls from unrecognized IP ranges
- Bulk export requests
- After-hours access to sensitive categories (HIV, psych, substance abuse)
- Failed authentication attempts >10 in 5 minutes

**Alert timeline:**
- Detection → Security team alert: **15 minutes** (internal SLA)
- Security team → HIPAA Privacy Officer: **1 hour**
- HIPAA Privacy Officer → HHS: **60 days** (HIPAA requirement)
- HIPAA Privacy Officer → Affected individuals: **60 days**

### 3.2 Breach Response Playbook

**Phase 1 — Detection (0–1 hour):**
```
1. Automated alert fires → security_team@cliniqai.com + PagerDuty
2. Security analyst reviews alert in <15 minutes
3. Preliminary scope assessment:
   - How many records affected?
   - What PHI was exposed?
   - Was it actual exposure or a false positive?
4. If confirmed: escalate to Privacy Officer + Legal counsel
5. Preserve all logs (do NOT delete anything)
```

**Phase 2 — Containment (1–24 hours):**
```
1. Disable compromised account/access path
2. Rotate affected credentials
3. Forensic log preservation (immutable snapshot)
4. Determine root cause (data quality? model error? UX failure?)
5. Determine scope: complete patient list of affected records
```

**Phase 3 — Notification (24–60 days):**
```
1. Notify HHS if >500 individuals affected (within 60 days)
2. Notify state attorney general if required by state law
3. Individual notification letters (mail + email if available)
4. Media notification if >500 in a state
5. Annual report to HHS for <500 individual breaches
```

---

## 4. Business Associate Agreement (BAA) Requirements

### What CliniQAI Commits To in a BAA

CliniQAI's standard BAA commits to:

1. **Use PHI only as permitted** by the BAA and HIPAA
2. **Safeguards**: Implement administrative, physical, and technical safeguards
3. **Subcontractors**: Require equivalent HIPAA compliance from all subcontractors (AWS, Azure)
4. **Reporting**: Report any breach to Covered Entity within 30 days of discovery
5. **Access**: Provide access to PHI records if HHS requires
6. **Return/Destruction**: Upon termination, return or destroy all PHI within 30 days
7. **Minimum necessary**: Use only the minimum PHI necessary for the purpose

### AWS Services with HIPAA BAA Coverage (Used by CliniQAI)
- Amazon EC2 / EKS (compute)
- Amazon RDS / Aurora PostgreSQL (database)
- Amazon S3 (object storage)
- AWS KMS (key management)
- Amazon CloudWatch (monitoring — no PHI in logs)
- AWS Certificate Manager
- Amazon VPC (network isolation)

### Services NOT Covered by AWS HIPAA BAA (NEVER store PHI in these)
- Amazon SES (email) — use only for operational alerts, never patient data
- Amazon SNS — operational alerts only
- Amazon CloudFront — serve only de-identified data

---

## 5. FDA SaMD Classification

### Our Classification: Class II — Software as a Medical Device

**Intended Use:**
CliniQAI provides clinical decision support to assist licensed healthcare providers in:
- Risk stratification of hospitalized patients
- Early warning for clinical deterioration
- Drug interaction screening
- Documentation assistance

**NOT intended for:**
- Autonomous clinical decision-making
- Replacing physician judgment
- Making final diagnoses

**Why Class II (not Class III):**
- All AI outputs require physician review before clinical action
- System cannot automatically order medications, procedures, or transfers
- Human-in-the-loop design ensures physician retains decision authority
- Physician can override any recommendation with one click

**510(k) Predicate Devices:**
- Epic Sepsis Model (K203264) — predicate for sepsis prediction
- Sepsis ImmunoScore — predicate for sepsis early warning
- Auris Sepsis Index — predicate for deterioration prediction

**De Novo pathway considerations:**
If no suitable predicate found for a specific function, De Novo classification pathway will be used.

**Pre-Submission Meeting:**
Scheduled with FDA Center for Devices and Radiological Health (CDRH).
Pre-submission request filed: Q1 2026.
Expected 510(k) submission: Q3 2026.
Expected clearance: Q1 2027 (12–18 months from submission).

---

## 6. Consent Management

### Patient Consent States

```python
@dataclass
class ConsentState:
    treatment_use:  bool = True   # ALWAYS true — required for care
    ai_inference:   bool = False  # Hospital policy: opt-in OR opt-out
    research_use:   bool = False  # Explicit opt-in ONLY
    data_sharing:   bool = False  # Explicit opt-in ONLY
```

### Consent Check Before AI Inference

```python
# Before ANY AI processing:
can_use, reason = gateway.consent.can_use_for_ai(patient_id)
if not can_use:
    # Must use fully de-identified data
    patient_data = deidentifier.deidentify_patient(patient_data, patient_id)
```

### Hospital-Level Consent Policy Options

Hospitals can configure:
- **Opt-Out Model** (default): AI inference is on unless patient opts out
- **Opt-In Model**: AI inference off unless patient explicitly consents
- **Blanket Consent**: Hospital policy covers all admitted patients (requires patient notification)

---

## 7. Minimum Necessary Standard

HIPAA requires accessing only the minimum PHI necessary for the intended purpose.

**Our implementation:**
- AI models receive de-identified data only
- LLM reasoning engine never sees full patient name
- Audit log resource_id is always the de-identified pseudonym
- Kafka messages contain de-identified IDs only
- Vector store (Qdrant) stores only de-identified embeddings

**Exception: Treating physician context**
When a treating physician accesses their patient's record through our interface, they may access full PHI. This is appropriate under HIPAA's "treatment" exception. All such access is audit-logged.

---

## 8. Staff Training Requirements

All CliniQAI employees with PHI access must complete:
- [ ] HIPAA Fundamentals (annual, 2 hours)
- [ ] Security Awareness Training (annual, 1 hour)
- [ ] Incident Response Training (annual, 30 minutes)
- [ ] Role-specific training (engineering: secure coding; clinical: PHI handling)

Training completion tracked in HR system. PHI access provisioned only after training completion.

---

## Appendix A: Key Contacts

| Role | Responsibility |
|------|---------------|
| HIPAA Privacy Officer | Privacy policies, breach notification |
| HIPAA Security Officer | Technical safeguards, access controls |
| Legal Counsel | BAA review, HHS notification |
| Engineering Security Lead | Implementation, security testing |
| Clinical Informatics Director | Clinical validation, physician workflows |

---

## Appendix B: Annual Review Schedule

| Quarter | Activity |
|---------|---------|
| Q1 | Risk Assessment update |
| Q2 | Penetration testing + vulnerability assessment |
| Q3 | HIPAA audit (internal) |
| Q4 | BAA review with hospital partners, policy updates |

**Next review date**: Q1 2027

---

*This document is confidential. Distribution limited to authorized personnel with a need to know. Do not share externally without Legal review.*
