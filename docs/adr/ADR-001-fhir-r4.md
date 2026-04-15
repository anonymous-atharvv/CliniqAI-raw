# ADR-001: FHIR R4 + SMART on FHIR as the Integration Standard

**Date**: 2025-Q3  
**Status**: Accepted  
**Deciders**: CTO, Clinical Informatics Lead, Integration Engineer  
**Context**: Choosing the data exchange standard for EHR integration.

---

## Context

CliniQAI must integrate with Epic (32% US market share), Cerner (25%), and Meditech (15%). Each has a different native data format. We need a single normalization target.

**Options evaluated:**
1. **HL7 v2 only** — Universal support, but message-based (not resource-based), hard to query
2. **CDA/CCD** — Document-based XML, widely supported but verbose and hard to work with programmatically  
3. **FHIR R4 + SMART on FHIR** — REST API, JSON, modern OAuth2 auth, EHR vendor investment

## Decision

**Adopt FHIR R4 as the canonical data format. Use SMART on FHIR OAuth2 for all EHR authentication.**

## Rationale

**FHIR R4 (not R2 or STU3):**
- CMS Interoperability Rule mandates FHIR R4 from 2021 — all major EHRs have compliant APIs
- JSON-native (not XML) — easier to work with in Python/JavaScript
- Resource-based — Patient, Observation, MedicationRequest map cleanly to our internal models
- HL7 FHIR R4 is the basis for US Core and USCDI — regulatory alignment

**SMART on FHIR (not basic auth or custom OAuth):**
- Epic will REJECT integrations that don't use SMART on FHIR OAuth2
- Provides: EHR launch context (current patient in context), user identity, ABAC-compatible scopes
- Two launch modes: EHR Launch (inside Epic) and Standalone Launch
- Without SMART, hospital IT cannot approve the integration through Epic App Orchard

**LOINC codes for all Observations (non-negotiable):**
- Hospitals reject integrations that use custom codes
- LOINC is the universal standard for lab/vital observations
- Our registry includes all 65+ codes needed for clinical AI

**Consequences:**
- ✅ Epic App Orchard certification path becomes clear
- ✅ Cerner SMART on FHIR support (similar implementation)
- ✅ All AI models receive consistently formatted input
- ⚠ HL7 v2 messages from legacy systems still need batch parsing (see `batch_etl.py`)
- ⚠ Meditech FHIR support is less mature — may require CDA fallback

## Implementation

See `backend/services/fhir/normalizer.py` and `backend/services/fhir/smart_auth.py`.

LOINC codes for mandatory vitals (per clinical spec):

| Vital | LOINC |
|-------|-------|
| Heart Rate | 8867-4 |
| SpO₂ (Pulse Ox) | 59408-5 |
| BP Systolic | 8480-6 |
| BP Diastolic | 8462-4 |
| Temperature | 8310-5 |
| Respiratory Rate | 9279-1 |
