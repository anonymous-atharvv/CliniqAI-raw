# BUSINESS ASSOCIATE AGREEMENT (BAA)
## CliniQAI — Standard Template

**THIS IS A TEMPLATE. LEGAL REVIEW REQUIRED BEFORE EXECUTION.**  
Engage qualified HIPAA counsel to review and customize before signing with any hospital.

---

**BUSINESS ASSOCIATE AGREEMENT**

This Business Associate Agreement ("Agreement") is entered into as of [DATE] ("Effective Date") by and between:

**Covered Entity (CE):** [HOSPITAL NAME], a [STATE] [ENTITY TYPE], located at [ADDRESS] ("Hospital")

**Business Associate (BA):** CliniQAI, Inc., a Delaware corporation, located at [ADDRESS] ("CliniQAI")

---

## 1. DEFINITIONS

Terms used but not defined herein have the meanings set forth in HIPAA, the HITECH Act, and their implementing regulations (45 CFR Parts 160 and 164).

**"Protected Health Information" or "PHI"** means individually identifiable health information received by CliniQAI from Hospital.

**"Platform"** means the CliniQAI Hospital Intelligence Platform, including all software, APIs, and services provided under the Master Services Agreement ("MSA") between the parties.

---

## 2. OBLIGATIONS OF BUSINESS ASSOCIATE

### 2.1 Permitted Uses and Disclosures

CliniQAI may use and disclose PHI only as necessary to:
(a) Provide the Platform services to Hospital as described in the MSA;
(b) Perform data aggregation for Hospital's health care operations;
(c) Improve the Platform using de-identified data (as defined in Section 2.7).

CliniQAI shall NOT use or disclose PHI for any other purpose.

### 2.2 Technical Safeguards

CliniQAI represents and warrants that it has implemented and maintains:

**Encryption:**
- AES-256-GCM encryption for all PHI at rest
- TLS 1.3 for all PHI in transit
- AWS KMS customer-managed keys (CMK) for key management
- Key rotation: annual minimum

**Access Controls:**
- Attribute-Based Access Control (ABAC) per 45 CFR §164.312(a)(1)
- Unique user identification per §164.312(a)(2)(i)
- Automatic session timeout (60 minutes inactive)
- Role-based access: physician, nurse, pharmacist, admin, researcher

**Audit Controls:**
- Immutable audit log per 45 CFR §164.312(b)
- Every PHI access generates an audit event
- WORM storage (AWS S3 Object Lock, COMPLIANCE mode)
- 6-year minimum retention

**De-Identification:**
- Safe Harbor method per 45 CFR §164.514(b) — all 18 PHI identifiers
- PHI de-identified BEFORE processing by AI/ML systems
- Original-to-pseudonym mapping in separate encrypted vault (HashiCorp Vault)

### 2.3 Subcontractors

CliniQAI shall ensure any subcontractors that create, receive, maintain, or transmit PHI on CliniQAI's behalf agree in writing to the same restrictions and conditions that apply to CliniQAI.

**Current subprocessors with PHI access:**
- Amazon Web Services (AWS) — HIPAA BAA executed, US region only
- [Additional subprocessors listed in Exhibit A]

### 2.4 Breach Notification

Upon discovery of a breach of unsecured PHI:
- CliniQAI shall notify Hospital's Privacy Officer within **30 days** of discovery
- Notification shall include: (a) identification of individuals affected; (b) description of PHI involved; (c) contact information for affected individuals; (d) steps individuals should take to protect themselves; (e) CliniQAI's steps to investigate, mitigate, and prevent future breaches
- CliniQAI shall cooperate with Hospital's breach assessment and required HHS notifications

### 2.5 Access Rights

CliniQAI shall make PHI available to Hospital as necessary for Hospital to respond to individuals' requests for access under 45 CFR §164.524.

### 2.6 Accounting of Disclosures

CliniQAI shall document disclosures of PHI and provide this information to Hospital to permit Hospital to respond to individual requests for an accounting.

### 2.7 De-Identified Data for Model Improvement

Hospital grants CliniQAI the right to use de-identified data (rendered HIPAA-compliant under Safe Harbor, 45 CFR §164.514(b)) for the purpose of improving the Platform's AI models, subject to:

(a) All data is de-identified prior to use in model training;  
(b) Hospital-specific data is never disclosed to other hospital customers;  
(c) Model improvements benefit all hospitals in the CliniQAI network;  
(d) Hospital may opt out of data sharing with 30 days written notice;  
(e) Upon termination, CliniQAI will delete all Hospital-derived model weights within 90 days.

### 2.8 Return or Destruction of PHI

Upon termination of this Agreement:
- CliniQAI shall return or destroy all PHI within **30 days** of the termination date
- If return or destruction is infeasible, CliniQAI shall notify Hospital and extend protections indefinitely
- Destruction shall be documented and certificate provided to Hospital

---

## 3. OBLIGATIONS OF COVERED ENTITY

Hospital shall:
(a) Notify CliniQAI of any limitation in its Notice of Privacy Practices that affects CliniQAI's use or disclosure of PHI;
(b) Notify CliniQAI of any changes in, or revocation of, permission by individual to use or disclose PHI;
(c) Obtain any patient authorizations required for research uses of PHI;
(d) Not request CliniQAI use or disclose PHI in any manner that would violate HIPAA.

---

## 4. TERM AND TERMINATION

### 4.1 Term

This Agreement shall be effective as of the Effective Date and shall continue until terminated in accordance with this Section.

### 4.2 Termination for Cause

Either party may terminate this Agreement immediately if the other party materially breaches this Agreement and fails to cure such breach within 30 days of written notice.

### 4.3 Effect of Termination

Obligations in Section 2.8 survive termination.

---

## 5. MISCELLANEOUS

### 5.1 Amendment

CliniQAI agrees to amend this Agreement as necessary to comply with changes in HIPAA or applicable state law.

### 5.2 Regulatory References

Regulatory references shall be construed to include amendments and successor regulations.

### 5.3 Interpretation

This Agreement shall be interpreted in the manner most consistent with HIPAA compliance.

---

## SIGNATURES

**Covered Entity (Hospital):**

Signature: _________________________  
Name: _________________________  
Title: _________________________  
Date: _________________________  

**Business Associate (CliniQAI):**

Signature: _________________________  
Name: _________________________  
Title: Chief Executive Officer  
Date: _________________________  

---

*[EXHIBIT A: Current Subprocessors with HIPAA BAA Coverage]*  
*[EXHIBIT B: Data Security Technical Specifications]*  
*[EXHIBIT C: Incident Response Contact Information]*
