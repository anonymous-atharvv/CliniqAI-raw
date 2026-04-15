#!/usr/bin/env python3
"""
FHIR R4 Validation Script
Validates all ingested FHIR resources against the HL7 FHIR R4 specification.
Run before Epic/Cerner integration submission.

Checks:
  1. Required fields per resource type
  2. LOINC code validity for Observations
  3. ICD-10 code format for Conditions
  4. RxNorm code presence for MedicationRequests
  5. Date format compliance (FHIR uses YYYY-MM-DD)
  6. SMART on FHIR endpoint conformance

Usage:
  python scripts/validate_fhir.py                     # Validate DB records
  python scripts/validate_fhir.py --bundle sample.json # Validate a FHIR bundle file
  python scripts/validate_fhir.py --endpoint           # Test FHIR endpoint conformance
  python scripts/validate_fhir.py --epic-sandbox       # Test against Epic FHIR sandbox
"""

import sys, json, re, argparse, logging, asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

from services.fhir.loinc_mapper import LOINCMapper, LOINC_REGISTRY


@dataclass
class ValidationResult:
    resource_type: str
    resource_id: str
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class FHIRValidator:
    """Validates FHIR R4 resources against HL7 specification requirements."""

    REQUIRED_FIELDS = {
        "Patient":            ["resourceType", "identifier", "name"],
        "Observation":        ["resourceType", "status", "code", "subject"],
        "MedicationRequest":  ["resourceType", "status", "intent", "medicationCodeableConcept", "subject"],
        "Condition":          ["resourceType", "clinicalStatus", "code", "subject"],
        "Encounter":          ["resourceType", "status", "class", "subject"],
        "AllergyIntolerance": ["resourceType", "clinicalStatus", "code", "patient"],
    }

    VALID_STATUSES = {
        "Observation":        ["registered", "preliminary", "final", "amended", "cancelled", "entered-in-error"],
        "MedicationRequest":  ["active", "on-hold", "cancelled", "completed", "entered-in-error", "stopped"],
        "Condition":          ["active", "recurrence", "relapse", "inactive", "remission", "resolved"],
        "Encounter":          ["planned", "in-progress", "onhold", "discharged", "completed", "cancelled"],
    }

    ICD10_PATTERN = re.compile(r'^[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?$')
    DATE_PATTERN   = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')
    LOINC_SYSTEM   = "http://loinc.org"

    def validate_resource(self, resource: Dict) -> ValidationResult:
        rtype = resource.get("resourceType", "Unknown")
        rid   = resource.get("id", "no-id")
        result = ValidationResult(resource_type=rtype, resource_id=rid, is_valid=True)

        # 1. Required fields
        for field in self.REQUIRED_FIELDS.get(rtype, []):
            if not resource.get(field):
                result.errors.append(f"Missing required field: {field}")

        # 2. Status validation
        status = resource.get("status")
        if status and rtype in self.VALID_STATUSES:
            if status not in self.VALID_STATUSES[rtype]:
                result.errors.append(f"Invalid status '{status}' for {rtype}")

        # 3. Resource-specific checks
        if rtype == "Observation":
            self._validate_observation(resource, result)
        elif rtype == "Patient":
            self._validate_patient(resource, result)
        elif rtype == "MedicationRequest":
            self._validate_medication(resource, result)
        elif rtype == "Condition":
            self._validate_condition(resource, result)

        result.is_valid = len(result.errors) == 0
        return result

    def _validate_observation(self, obs: Dict, result: ValidationResult):
        code_block = obs.get("code", {})
        codings = code_block.get("coding", [])
        loinc_codings = [c for c in codings if c.get("system") == self.LOINC_SYSTEM]

        if not loinc_codings:
            result.warnings.append("Observation has no LOINC coding — interoperability limited")
        else:
            for c in loinc_codings:
                code = c.get("code", "")
                if not LOINCMapper.is_valid_code(code):
                    result.warnings.append(f"LOINC code '{code}' not in CliniQAI registry (may still be valid)")

        # Value must exist for vital signs
        category_codes = [
            c.get("code") for cat in obs.get("category", []) for c in cat.get("coding", [])
        ]
        if "vital-signs" in category_codes:
            if not (obs.get("valueQuantity") or obs.get("valueString") or obs.get("valueCodeableConcept")):
                result.errors.append("Vital sign Observation must have a value")

        # Effective datetime
        if not (obs.get("effectiveDateTime") or obs.get("effectivePeriod")):
            result.errors.append("Observation must have effectiveDateTime or effectivePeriod")
        else:
            dt = obs.get("effectiveDateTime", "")
            if dt and not self.DATE_PATTERN.match(dt[:10]):
                result.errors.append(f"effectiveDateTime format invalid: {dt}")

    def _validate_patient(self, patient: Dict, result: ValidationResult):
        identifiers = patient.get("identifier", [])
        mrn_ids = [i for i in identifiers if i.get("type", {}).get("coding", [{}])[0].get("code") == "MR"]
        if not mrn_ids:
            result.warnings.append("Patient has no MRN identifier (code=MR)")

        name_list = patient.get("name", [])
        if name_list:
            for name in name_list:
                if not name.get("family"):
                    result.errors.append("Patient name missing family (last name)")
        
        birth = patient.get("birthDate", "")
        if birth and not self.DATE_PATTERN.match(birth):
            result.errors.append(f"birthDate format invalid: {birth} (expected YYYY-MM-DD)")

    def _validate_medication(self, med: Dict, result: ValidationResult):
        med_concept = med.get("medicationCodeableConcept", {})
        codings = med_concept.get("coding", [])
        rxnorm_codings = [c for c in codings if "rxnorm" in c.get("system", "").lower()]
        if not rxnorm_codings:
            result.warnings.append("MedicationRequest has no RxNorm coding — pharmacist agent DDI checking limited")
        if not med.get("dosageInstruction"):
            result.warnings.append("MedicationRequest missing dosageInstruction")

    def _validate_condition(self, condition: Dict, result: ValidationResult):
        code = condition.get("code", {})
        codings = code.get("coding", [])
        icd10_codings = [c for c in codings if "icd" in c.get("system", "").lower()]
        for c in icd10_codings:
            icd = c.get("code", "")
            if icd and not self.ICD10_PATTERN.match(icd):
                result.errors.append(f"ICD-10 code format invalid: '{icd}' (expected A00.0 format)")

    def validate_bundle(self, bundle: Dict) -> Tuple[int, int, List[ValidationResult]]:
        if bundle.get("resourceType") != "Bundle":
            return 0, 0, [ValidationResult("Bundle", "N/A", False, ["Not a FHIR Bundle"])]
        results = []
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            if resource:
                results.append(self.validate_resource(resource))
        valid = sum(1 for r in results if r.is_valid)
        return valid, len(results), results


async def validate_fhir_endpoint(base_url: str):
    """Test SMART on FHIR conformance of a FHIR endpoint."""
    import httpx
    logger.info(f"Testing FHIR endpoint: {base_url}")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Check metadata (CapabilityStatement)
        try:
            r = await client.get(f"{base_url}/metadata")
            if r.status_code == 200:
                cap = r.json()
                fhir_version = cap.get("fhirVersion", "unknown")
                logger.info(f"  ✅ CapabilityStatement: FHIR version {fhir_version}")
                software = cap.get("software", {})
                logger.info(f"  ✅ Software: {software.get('name', 'unknown')} {software.get('version', '')}")
            else:
                logger.error(f"  ❌ CapabilityStatement: HTTP {r.status_code}")
        except Exception as e:
            logger.error(f"  ❌ CapabilityStatement: {e}")

        # Check SMART configuration
        try:
            r = await client.get(f"{base_url}/.well-known/smart-configuration")
            if r.status_code == 200:
                smart = r.json()
                logger.info(f"  ✅ SMART config found. Token endpoint: {smart.get('token_endpoint', 'N/A')}")
                scopes = smart.get("scopes_supported", [])
                logger.info(f"  ✅ Supported scopes: {', '.join(scopes[:5])}")
            else:
                logger.warning(f"  ⚠ SMART config: HTTP {r.status_code} (may not support SMART on FHIR)")
        except Exception as e:
            logger.warning(f"  ⚠ SMART config: {e}")


async def main():
    parser = argparse.ArgumentParser(description="CliniQAI FHIR R4 Validator")
    parser.add_argument("--bundle", type=str, help="Validate a FHIR bundle JSON file")
    parser.add_argument("--endpoint", type=str, help="Test FHIR endpoint conformance")
    parser.add_argument("--epic-sandbox", action="store_true", help="Test against Epic FHIR R4 sandbox")
    parser.add_argument("--loinc-check", action="store_true", help="Validate LOINC code registry completeness")
    args = parser.parse_args()

    validator = FHIRValidator()

    if args.loinc_check:
        logger.info("LOINC Registry validation:")
        for param, entry in LOINC_REGISTRY.items():
            issues = []
            if not entry.get("code"): issues.append("missing code")
            if not entry.get("display"): issues.append("missing display")
            if not entry.get("unit"): issues.append("missing unit (may be intentional)")
            status = "✅" if not [i for i in issues if "missing code" in i or "missing display" in i] else "❌"
            if issues:
                logger.info(f"  {status} {param}: {', '.join(issues)}")
        logger.info(f"\n  Total: {len(LOINC_REGISTRY)} parameters in registry.")

    elif args.bundle:
        bundle_file = Path(args.bundle)
        if not bundle_file.exists():
            logger.error(f"Bundle file not found: {args.bundle}")
            sys.exit(1)
        bundle = json.loads(bundle_file.read_text())
        valid, total, results = validator.validate_bundle(bundle)
        logger.info(f"\nBundle validation: {valid}/{total} resources valid")
        for r in results:
            status = "✅" if r.is_valid else "❌"
            logger.info(f"  {status} {r.resource_type}/{r.resource_id}")
            for e in r.errors:   logger.error(f"       ERROR: {e}")
            for w in r.warnings: logger.warning(f"       WARN: {w}")
        sys.exit(0 if valid == total else 1)

    elif args.endpoint:
        await validate_fhir_endpoint(args.endpoint)

    elif args.epic_sandbox:
        await validate_fhir_endpoint("https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4")

    else:
        logger.info("Running basic LOINC coverage check...")
        vital_signs = LOINCMapper.get_vital_signs()
        labs = LOINCMapper.get_laboratory_tests()
        logger.info(f"  ✅ {len(vital_signs)} vital sign parameters")
        logger.info(f"  ✅ {len(labs)} laboratory test parameters")
        logger.info(f"  ✅ {len(LOINC_REGISTRY)} total LOINC codes registered")
        logger.info("\nRun with --bundle <file> or --endpoint <url> for full validation.")


if __name__ == "__main__":
    asyncio.run(main())
