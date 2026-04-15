# FDA Software as a Medical Device (SaMD) Classification

**Document Type**: Regulatory Strategy  
**Status**: Pre-submission preparation  
**Target Submission**: Q3 2026 (510k)

---

## 1. Product Description

**CliniQAI Clinical Decision Support Platform** is a software system that analyzes patient data from multiple clinical modalities (vital signs, laboratory results, clinical notes, and medical imaging) to provide decision support information to licensed healthcare providers in hospital settings.

**Intended Use:**
To assist licensed physicians and clinical staff in the monitoring and risk stratification of hospitalized patients by providing probabilistic risk assessments, clinical pattern recognition, and evidence-based recommendations for physician consideration.

**Indications for Use:**
For use in hospital settings by licensed healthcare providers as a decision support tool. The system is intended for:
- Continuous monitoring of ICU and ward patients for clinical deterioration
- Early warning for potential sepsis onset
- Drug interaction screening
- Length-of-stay optimization assistance

**NOT intended for:**
- Autonomous diagnosis
- Replacing physician clinical judgment
- Automated ordering of medications, procedures, or transfers
- Use without physician review of all outputs

---

## 2. FDA Classification

### Primary Classification: Class II SaMD

**Rationale for Class II (not Class III):**

Per FDA guidance "Software as a Medical Device (SaMD): Clinical Evaluation" (2017):

| Criterion | Our System |
|-----------|-----------|
| State of healthcare situation | Serious (ICU patients, deterioration risk) |
| Significance of information to healthcare decision | Inform clinical management |
| Combined SaMD category | IIb (Serious + Inform) |

**Key design decisions maintaining Class II status:**
1. **Mandatory physician review**: All AI outputs require physician review before clinical action. The system cannot autonomously order medications, procedures, or transfers.
2. **Decision support, not diagnosis**: The system suggests hypotheses for physician evaluation — it does not provide final diagnoses.
3. **Human-in-the-loop**: Every HIGH/CRITICAL risk output has `human_review_required=True` enforced in code.
4. **Override mechanism**: Physicians can override any AI recommendation with one click, with no friction.

### 510(k) Predicate Strategy

**Predicate Device 1:** Epic Sepsis Model (K203264)
- Cleared for: sepsis risk prediction in EHR workflow
- Our similarities: sepsis probability output, EHR-integrated, decision support
- Our improvements: multi-modal inputs, uncertainty quantification, LOINC-coded outputs

**Predicate Device 2:** Sepsis ImmunoScore
- Cleared for: sepsis immune dysregulation assessment
- Supports: sepsis early warning use case

**De Novo consideration:** If 510(k) pathway has insufficient predicates for specific functions (e.g., imaging AI integration), De Novo classification pathway will be used for those functions.

---

## 3. Clinical Evidence Strategy

### Pre-Market Evidence

**Retrospective Validation (Month 6-8):**
- Test on 12 months of de-identified historical data
- Minimum performance thresholds:
  - Sepsis prediction AUROC > 0.85
  - Sensitivity > 0.80 at specificity 0.85
  - Deterioration AUROC > 0.80
  - Imaging critical finding sensitivity > 0.90

**Shadow Mode (Month 8-9):**
- AI runs for 30 days without physician visibility
- Compare AI recommendations to actual clinical decisions
- Measure: agreement rate, false positive rate

**Pilot Deployment (Month 9-12):**
- 5+ physicians actively using system
- Collect physician feedback and acceptance rates
- Document adverse events (none expected for decision support)

**Post-Market Surveillance:**
- Quarterly performance monitoring
- Drift detection with automatic alerts
- Annual clinical review board review
- MedWatch reporting for any SaMD-related adverse events

---

## 4. Labeling Requirements

### Required Labeling Elements

**Intended Use Statement (on all outputs):**
> "AI Decision Support Only. This output requires physician review before any clinical action. Not a diagnosis. Confidence values are probabilistic."

**Contraindications:**
- Not for use as sole basis for clinical decisions
- Not validated for pediatric patients (age < 18) in current version
- Not validated for obstetric patients in current version
- Reduced accuracy in patients with data quality score < 0.60

**Known Limitations:**
- Performance may degrade with seasonal disease patterns (drift monitoring active)
- Imaging AI validated for CXR only in v1.0 (CT/MRI in roadmap)
- Sepsis model trained on MIMIC-IV — local fine-tuning required before deployment

---

## 5. Cybersecurity Documentation (per FDA 2023 Guidance)

**Threat Model:** See `docs/compliance/hipaa-controls.md`

**Security by Design:**
- HIPAA-compliant de-identification before AI processing
- TLS 1.3 for all data in transit
- AES-256-GCM for PHI at rest
- JWT with ABAC for access control
- Immutable audit logging
- No direct patient data accessible to LLM

**Software Bill of Materials (SBOM):** Generated at each release.  
**Vulnerability Disclosure Policy:** security@cliniqai.com, 90-day responsible disclosure.  
**Patch Management:** Critical security patches within 72 hours.

---

## 6. Timeline

| Milestone | Target Date |
|-----------|------------|
| Pre-submission meeting request | Month 3 |
| Pre-submission meeting (FDA) | Month 6 |
| Clinical validation complete | Month 8 |
| 510(k) submission | Month 9 |
| Expected FDA decision | Month 21-24 |
| Market launch (cleared) | Month 22-25 |

**Market during submission period:** Market as "FDA clearance pending" with all decision support labeling in place. Clinical decision support tools that are not autonomous can be marketed before clearance under the FDA's enforcement discretion for CDS software.

---

## 7. Post-Market Commitments

Upon clearance, CliniQAI commits to:
- Annual performance reviews submitted to FDA
- Immediate MedWatch reporting of any SaMD-related adverse events
- 30-day notification before substantive software changes
- Bias monitoring reports (quarterly, demographic subgroup performance)
- Ongoing drift detection and automated alerts at 5% AUROC degradation threshold
