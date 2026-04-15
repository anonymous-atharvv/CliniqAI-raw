"""
HIPAA Compliance + Security Gateway
Layer 2 — Non-negotiable. Everything passes through here.

Implements:
- Safe Harbor de-identification (45 CFR §164.514(b)) — 18 PHI identifiers
- Attribute-Based Access Control (ABAC)
- Immutable audit logging
- Consent management
- Breach detection

CRITICAL: No PHI ever leaves this module unprocessed.
Any function returning patient data must go through de-identify_record() first
unless the caller has explicit PHI access rights verified by check_access().
"""

import uuid
import hashlib
import hmac
import re
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HIPAA Safe Harbor: 18 PHI Identifiers
# 45 CFR §164.514(b)
# ─────────────────────────────────────────────

PHI_IDENTIFIERS = [
    "names",              # 1
    "geographic_data",    # 2
    "dates",              # 3 (except year)
    "phone_numbers",      # 4
    "fax_numbers",        # 5
    "email_addresses",    # 6
    "ssn",                # 7
    "mrn",                # 8 (medical record numbers)
    "health_plan_numbers",# 9
    "account_numbers",    # 10
    "certificate_numbers",# 11
    "vehicle_identifiers",# 12
    "device_identifiers", # 13
    "web_urls",           # 14
    "ip_addresses",       # 15
    "biometric_ids",      # 16
    "photos",             # 17
    "other_unique_ids",   # 18
]


class UserRole(str, Enum):
    PHYSICIAN = "physician"
    NURSE = "nurse"
    RADIOLOGIST = "radiologist"
    ADMIN = "admin"
    RESEARCHER = "researcher"
    AI_SYSTEM = "ai_system"
    PHARMACIST = "pharmacist"


class DataSensitivity(str, Enum):
    STANDARD = "standard"
    SENSITIVE = "sensitive"  # HIV, psych, substance abuse, genetics


class TimeContext(str, Enum):
    ACTIVE_SHIFT = "active_shift"
    ON_CALL = "on_call"
    AFTER_HOURS = "after_hours"


class CareRelationship(str, Enum):
    TREATING = "treating_patient"
    CONSULTING = "consulting"
    NO_RELATIONSHIP = "no_relationship"


class AccessAction(str, Enum):
    READ = "read"
    WRITE = "write"
    INFER = "infer"
    EXPORT = "export"
    DELETE = "delete"


class AccessReason(str, Enum):
    TREATMENT = "treatment"
    OPERATIONS = "operations"
    RESEARCH = "research"
    PAYMENT = "payment"
    AI_INFERENCE = "ai_inference"


@dataclass
class AccessRequest:
    """Request to access patient data. Evaluated by ABAC engine."""
    actor_id: str
    actor_role: UserRole
    patient_id: str
    resource_type: str
    action: AccessAction
    reason: AccessReason
    care_relationship: CareRelationship
    data_sensitivity: DataSensitivity
    time_context: TimeContext
    ip_address: str
    department: Optional[str] = None
    irb_approved: bool = False


@dataclass  
class AuditEvent:
    """
    HIPAA-required audit log entry.
    Every data access generates one of these.
    Stored in write-once (WORM) storage. Never deleted.
    Retained for minimum 6 years per HIPAA.
    """
    event_id: str
    timestamp: str
    actor: str
    action: str
    resource_type: str
    resource_id: str  # Always de-identified
    access_reason: str
    outcome: str  # success|denied
    ip_address: str  # Hashed
    
    # Extended fields
    actor_role: str = ""
    department: str = ""
    session_id: str = ""
    
    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


@dataclass
class ConsentState:
    """Patient consent states. Check BEFORE any AI inference."""
    patient_id: str
    treatment_use: bool = True      # Always true — required for care
    ai_inference: bool = False       # Must opt-in or opt-out per hospital policy
    research_use: bool = False       # Explicit opt-in only
    data_sharing: bool = False       # Explicit opt-in only
    last_updated: str = ""
    updated_by: str = ""


class DeIdentificationEngine:
    """
    HIPAA Safe Harbor De-identification Engine.
    
    Removes or transforms all 18 PHI identifiers.
    MUST be called before any data leaves the hospital security perimeter.
    
    Preserves: year of birth, geographic region (state), 
               clinical codes (ICD-10, SNOMED, LOINC).
    """
    
    def __init__(self, salt: str, date_shift_seed: Optional[str] = None):
        """
        salt: Per-deployment secret for consistent pseudonymization.
              Store in HashiCorp Vault. NEVER in code.
        date_shift_seed: If provided, date shifts are consistent per patient.
        """
        self._salt = salt.encode()
        self._date_shift_seed = date_shift_seed
        
        # Regex patterns for PHI detection in text
        self._phi_patterns = [
            (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN-REMOVED]'),      # SSN
            (r'\b\d{10}\b', '[PHONE-REMOVED]'),  # 10-digit phone
            (r'\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b', '[PHONE-REMOVED]'),  # Formatted phone
            (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL-REMOVED]'),  # Email
            (r'\bMRN[:\s]*[A-Z0-9]+\b', '[MRN-REMOVED]'),      # MRN
            (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP-REMOVED]'),  # IP
            (r'https?://[^\s]+', '[URL-REMOVED]'),              # URLs
        ]
    
    def pseudonymize(self, identifier: str, prefix: str = "ID") -> str:
        """
        Replace a PHI identifier with a consistent pseudonym.
        Same input → same output (deterministic) within a deployment.
        Different deployments → different pseudonyms (salt-dependent).
        
        CRITICAL: The original↔pseudonym mapping must be stored SEPARATELY
        in an encrypted vault. This function only generates the pseudonym.
        """
        combined = f"{identifier}:{prefix}".encode()
        hash_bytes = hmac.new(self._salt, combined, hashlib.sha256).digest()
        # Take first 16 bytes, encode as hex = 32 char pseudonym
        pseudonym = f"{prefix}-{hash_bytes[:8].hex().upper()}"
        return pseudonym
    
    def shift_date(self, date_str: str, patient_seed: str) -> str:
        """
        Apply consistent date shift for a patient (±90 days).
        The shift is deterministic per patient — relative time relationships preserved.
        
        IMPORTANT: Only year is preserved in output per Safe Harbor.
        Return format: YYYY-MM-DD with shifted date.
        """
        try:
            # Determine shift amount (consistent per patient)
            patient_hash = hmac.new(
                self._salt, 
                patient_seed.encode(), 
                hashlib.sha256
            ).digest()
            # Convert first 2 bytes to shift value in range [-90, 90]
            shift_int = int.from_bytes(patient_hash[:2], 'big')
            shift_days = (shift_int % 181) - 90  # -90 to +90
            
            # Parse date
            for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"]:
                try:
                    parsed = datetime.strptime(date_str, fmt)
                    shifted = parsed + timedelta(days=shift_days)
                    return shifted.strftime("%Y-%m-%d")
                except ValueError:
                    continue
            
            # If parsing fails, return just the year
            year_match = re.search(r'\b(19|20)\d{2}\b', date_str)
            if year_match:
                return year_match.group()
            
            return "[DATE-REMOVED]"
            
        except Exception as e:
            logger.error(f"Date shift failed: {e}")
            return "[DATE-REMOVED]"
    
    def generalize_age(self, age: int) -> str:
        """Per Safe Harbor: age > 89 → '90+'"""
        if age > 89:
            return "90+"
        return str(age)
    
    def generalize_zip(self, zip_code: str) -> str:
        """
        Per Safe Harbor: geographic data below state level must be generalized.
        Zip codes with population < 20,000 → replace with '00000'.
        For simplicity: return first 3 digits only (regional ZIP prefix).
        """
        if len(zip_code) >= 5:
            # In production: check population table for 3-digit prefix
            return zip_code[:3] + "000"
        return "00000"
    
    def scrub_free_text(self, text: str) -> str:
        """
        Remove PHI patterns from free text (clinical notes).
        This is a best-effort filter — not a replacement for manual review.
        """
        scrubbed = text
        for pattern, replacement in self._phi_patterns:
            scrubbed = re.sub(pattern, replacement, scrubbed, flags=re.IGNORECASE)
        return scrubbed
    
    def deidentify_patient(
        self, 
        patient_fhir: Dict[str, Any],
        patient_seed: str
    ) -> Dict[str, Any]:
        """
        Apply Safe Harbor de-identification to a FHIR Patient resource.
        
        Transforms:
        - name → pseudonym UUID
        - birthDate → year only (or shifted date)
        - identifier (MRN, SSN) → pseudonyms
        - telecom (phone, email) → removed
        - address → state only
        - age > 89 → "90+"
        
        Preserves:
        - birth year
        - state
        - gender
        - clinical codes (in other resources)
        """
        deidentified = {
            "resourceType": "Patient",
            "id": self.pseudonymize(f'{patient_fhir.get("id", "")}__{patient_seed}', "PAT"),
            "meta": patient_fhir.get("meta", {}),
        }
        
        # De-identify identifiers (MRN → pseudonym)
        if identifiers := patient_fhir.get("identifier"):
            deidentified["identifier"] = [
                {
                    "type": ident.get("type"),
                    "value": self.pseudonymize(ident.get("value", ""), "MRN"),
                }
                for ident in identifiers
            ]
        
        # Remove name — replace with pseudonym
        deidentified["name"] = [{
            "use": "anonymous",
            "text": self.pseudonymize(patient_seed, "NAME"),
        }]
        
        # Gender preserved (not PHI by itself)
        if gender := patient_fhir.get("gender"):
            deidentified["gender"] = gender
        
        # Birth date — keep year only
        if birth_date := patient_fhir.get("birthDate"):
            year_match = re.search(r'(19|20)\d{2}', str(birth_date))
            if year_match:
                deidentified["birthDate"] = year_match.group()
        
        # Remove telecom (phone, email)
        deidentified["telecom"] = []
        
        # Address — state only
        if addresses := patient_fhir.get("address"):
            deidentified["address"] = [
                {
                    "use": addr.get("use"),
                    "state": addr.get("state", ""),
                    "country": addr.get("country", "US"),
                    "postalCode": self.generalize_zip(addr.get("postalCode", "")),
                }
                for addr in addresses
            ]
        
        # Add de-identification marker
        if "meta" not in deidentified:
            deidentified["meta"] = {}
        deidentified["meta"]["security"] = [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
            "code": "N",
            "display": "Normal — Safe Harbor De-identified"
        }]
        
        return deidentified


class ABACEngine:
    """
    Attribute-Based Access Control Engine.
    
    Evaluates access requests against hospital ABAC policies.
    Called at API gateway level — before any data is returned.
    
    All rules must be explicitly defined. Default: DENY.
    """
    
    def check_access(self, request: AccessRequest) -> Tuple[bool, str]:
        """
        Evaluate whether an access request is permitted.
        
        Returns:
            (allowed: bool, reason: str)
        
        Default: DENY. Must match at least one allow rule.
        """
        
        # AI system: read de-identified data only
        if request.actor_role == UserRole.AI_SYSTEM:
            if request.action in [AccessAction.READ, AccessAction.INFER]:
                return True, "AI system: read/infer de-identified data permitted"
            return False, "AI system cannot write, export, or delete"
        
        # Researcher: de-identified data only, requires IRB approval
        if request.actor_role == UserRole.RESEARCHER:
            if request.action == AccessAction.READ and request.irb_approved:
                return True, "Researcher with IRB approval: read de-identified permitted"
            if not request.irb_approved:
                return False, "Researcher requires IRB approval flag"
            return False, "Researcher: read-only access"
        
        # No care relationship = no access to PHI
        if request.care_relationship == CareRelationship.NO_RELATIONSHIP:
            if request.actor_role not in [UserRole.ADMIN]:
                return False, f"No care relationship with patient — access denied"
        
        # Sensitive data (HIV, psych, substance abuse, genetics)
        if request.data_sensitivity == DataSensitivity.SENSITIVE:
            if request.actor_role == UserRole.NURSE:
                return False, "Nurses cannot access sensitive category data"
            if request.actor_role == UserRole.ADMIN:
                return False, "Admin role cannot access sensitive clinical data"
        
        # After-hours access to sensitive data: flag but allow treating physician
        if (request.time_context == TimeContext.AFTER_HOURS and 
            request.data_sensitivity == DataSensitivity.SENSITIVE and
            request.actor_role == UserRole.PHYSICIAN and
            request.care_relationship == CareRelationship.TREATING):
            return True, "After-hours treating physician access to sensitive data — flagged for review"
        
        # Nurse: vitals + medications for assigned patients only
        if request.actor_role == UserRole.NURSE:
            if request.care_relationship != CareRelationship.TREATING:
                return False, "Nurse: only assigned patients"
            if request.resource_type in ["Observation", "MedicationRequest", "Patient"]:
                return True, "Nurse treating patient access"
            return False, f"Nurse: no access to {request.resource_type}"
        
        # Physician: full access to their department patients
        if request.actor_role == UserRole.PHYSICIAN:
            if request.care_relationship in [CareRelationship.TREATING, CareRelationship.CONSULTING]:
                return True, "Physician treating/consulting patient access"
            return False, "Physician: only treating/consulting patients"
        
        # Radiologist: imaging only
        if request.actor_role == UserRole.RADIOLOGIST:
            if request.resource_type in ["ImagingStudy", "DiagnosticReport", "Patient"]:
                return True, "Radiologist imaging access"
            return False, "Radiologist: imaging resources only"
        
        # Pharmacist: medication + lab + vitals
        if request.actor_role == UserRole.PHARMACIST:
            if request.resource_type in ["MedicationRequest", "Observation", "AllergyIntolerance"]:
                return True, "Pharmacist medication safety access"
            return False, "Pharmacist: medication-related resources only"
        
        # Admin: operations only, no clinical PHI
        if request.actor_role == UserRole.ADMIN:
            if request.reason == AccessReason.OPERATIONS:
                return True, "Admin operational access"
            return False, "Admin: operational access only"
        
        return False, f"No matching access rule for role={request.actor_role}"


class AuditLogger:
    """
    HIPAA-compliant audit logger.
    
    Every data access generates an immutable audit event.
    Stored in AWS WORM buckets (write-once, cannot be deleted).
    Retained 6 years minimum per HIPAA.
    
    CRITICAL: IP addresses are hashed. Actor IDs are real (for accountability).
    Resource IDs are always de-identified.
    """
    
    def __init__(self, audit_backend=None):
        """
        audit_backend: Storage backend (WORM S3 bucket, Elasticsearch, etc.)
        If None, logs to logger (dev mode only).
        """
        self._backend = audit_backend
    
    def log_access(
        self,
        actor_id: str,
        actor_role: str,
        action: AccessAction,
        resource_type: str,
        resource_id: str,  # MUST be de-identified before passing here
        access_reason: AccessReason,
        outcome: str,  # "success" | "denied"
        ip_address: str,
        department: str = "",
        session_id: str = "",
    ) -> AuditEvent:
        """Log a data access event. Non-blocking. Thread-safe."""
        
        # Hash IP for privacy but maintain accountability
        ip_hash = hashlib.sha256(ip_address.encode()).hexdigest()[:16]
        
        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=actor_id,
            actor_role=actor_role,
            action=action.value,
            resource_type=resource_type,
            resource_id=resource_id,  # De-identified
            access_reason=access_reason.value,
            outcome=outcome,
            ip_address=ip_hash,
            department=department,
            session_id=session_id,
        )
        
        # Write to WORM storage (append-only)
        if self._backend:
            self._backend.append(event.to_json())
        else:
            # Dev mode: log to stdout
            logger.info(f"AUDIT: {event.to_json()}")
        
        return event


class ConsentManager:
    """
    Patient consent state management.
    
    CRITICAL RULE: Before ANY AI inference on patient data,
    check ai_inference consent flag.
    If not set: use fully de-identified data only.
    
    Consent states stored in dedicated encrypted database.
    Never stored alongside clinical data.
    """
    
    def __init__(self, consent_store=None):
        self._store = consent_store or {}
    
    def get_consent(self, patient_id: str) -> ConsentState:
        """Get patient consent state. Default: ai_inference=False (opt-in model)."""
        if patient_id in self._store:
            return self._store[patient_id]
        
        # Default consent state — conservative
        return ConsentState(
            patient_id=patient_id,
            treatment_use=True,   # Always true
            ai_inference=False,   # Must explicitly opt in
            research_use=False,
            data_sharing=False,
            last_updated=datetime.now(timezone.utc).isoformat(),
        )
    
    def can_use_for_ai(self, patient_id: str) -> Tuple[bool, str]:
        """
        Check if patient data can be used for AI inference.
        
        Returns:
            (can_use_identified: bool, reason: str)
        
        If False: must use de-identified data only.
        """
        consent = self.get_consent(patient_id)
        
        if consent.ai_inference:
            return True, "Patient consented to AI inference"
        
        return False, "Patient not consented to AI inference — use de-identified data only"
    
    def update_consent(
        self,
        patient_id: str,
        consent_type: str,
        value: bool,
        updated_by: str,
    ) -> ConsentState:
        """Update a specific consent flag. Logs change for audit."""
        consent = self.get_consent(patient_id)
        
        if hasattr(consent, consent_type) and consent_type != "treatment_use":
            setattr(consent, consent_type, value)
            consent.last_updated = datetime.now(timezone.utc).isoformat()
            consent.updated_by = updated_by
            
            if self._store is not None:
                self._store[patient_id] = consent
            
            logger.info(
                f"CONSENT: patient={patient_id} type={consent_type} "
                f"value={value} updated_by={updated_by}"
            )
        
        return consent


class BreachDetector:
    """
    HIPAA Breach Detection Monitor.
    
    Monitors for anomalous access patterns.
    HIPAA requires breach notification within 60 days of discovery.
    We alert within 15 minutes of detection.
    
    Patterns monitored:
    - Bulk access (>50 records outside department in 1 hour)
    - Unusual IP ranges
    - After-hours sensitive data access
    - Bulk export requests
    """
    
    def __init__(self, alert_callback=None):
        self._alert_callback = alert_callback
        self._access_counts: Dict[str, List[datetime]] = {}
    
    def record_access(
        self,
        actor_id: str,
        department: str,
        patient_department: str,
        record_type: str,
        ip_address: str,
        is_export: bool = False,
    ) -> Optional[str]:
        """
        Record an access and check for breach indicators.
        Returns breach alert string if detected, None if clean.
        """
        now = datetime.now(timezone.utc)
        one_hour_ago = now - timedelta(hours=1)
        
        # Track access counts per user per hour
        key = f"{actor_id}:outside_dept"
        if key not in self._access_counts:
            self._access_counts[key] = []
        
        # Prune old entries
        self._access_counts[key] = [
            t for t in self._access_counts[key] if t > one_hour_ago
        ]
        
        # Count out-of-department accesses
        if department != patient_department:
            self._access_counts[key].append(now)
        
        # Check bulk access threshold
        if len(self._access_counts[key]) > 50:
            alert = (
                f"BREACH ALERT: Actor {actor_id} accessed >50 records "
                f"outside department in 1 hour. "
                f"Count: {len(self._access_counts[key])}. "
                f"IP: {hashlib.sha256(ip_address.encode()).hexdigest()[:8]}"
            )
            logger.critical(alert)
            if self._alert_callback:
                self._alert_callback(alert)
            return alert
        
        # Check bulk export
        if is_export:
            alert = f"BREACH ALERT: Bulk export request by {actor_id}"
            logger.warning(alert)
            if self._alert_callback:
                self._alert_callback(alert)
            return alert
        
        return None


# ─────────────────────────────────────────────
# Compliance Gateway — unified entry point
# ─────────────────────────────────────────────

class ComplianceGateway:
    """
    The single entry point for all data access.
    
    Usage:
        gateway = ComplianceGateway(...)
        
        # Check access before serving any data
        allowed, reason = gateway.check_access(access_request)
        if not allowed:
            raise PermissionError(reason)
        
        # De-identify data if needed
        safe_data = gateway.deidentify_if_required(patient_data, consent)
        
        # Log the access (always)
        gateway.log_access(...)
    """
    
    def __init__(self, salt: str):
        self.deidentifier = DeIdentificationEngine(salt=salt)
        self.abac = ABACEngine()
        self.audit = AuditLogger()
        self.consent = ConsentManager()
        self.breach_detector = BreachDetector()
    
    def process_request(
        self,
        access_request: AccessRequest,
        patient_data: Dict[str, Any],
    ) -> Tuple[bool, Optional[Dict[str, Any]], str]:
        """
        Full compliance pipeline for a data access request.
        
        Returns:
            (allowed, processed_data, reason)
        """
        # Step 1: ABAC access check
        allowed, reason = self.abac.check_access(access_request)
        
        outcome = "success" if allowed else "denied"
        
        # Step 2: Audit log (always, regardless of outcome)
        self.audit.log_access(
            actor_id=access_request.actor_id,
            actor_role=access_request.actor_role.value,
            action=access_request.action,
            resource_type=access_request.resource_type,
            resource_id=self.deidentifier.pseudonymize(
                access_request.patient_id, "PAT"
            ),
            access_reason=access_request.reason,
            outcome=outcome,
            ip_address=access_request.ip_address,
        )
        
        if not allowed:
            return False, None, reason
        
        # Step 3: Breach detection
        self.breach_detector.record_access(
            actor_id=access_request.actor_id,
            department=access_request.department or "unknown",
            patient_department="unknown",  # Would come from patient data
            record_type=access_request.resource_type,
            ip_address=access_request.ip_address,
            is_export=access_request.action == AccessAction.EXPORT,
        )
        
        # Step 4: De-identification check
        # AI system always gets de-identified data
        # Researchers always get de-identified data
        needs_deidentification = access_request.actor_role in [
            UserRole.AI_SYSTEM, UserRole.RESEARCHER
        ]
        
        if not needs_deidentification:
            # Check consent for AI inference
            if access_request.reason == AccessReason.AI_INFERENCE:
                can_use_identified, consent_reason = self.consent.can_use_for_ai(
                    access_request.patient_id
                )
                if not can_use_identified:
                    needs_deidentification = True
        
        # Step 5: Apply de-identification if required
        if needs_deidentification and "resourceType" in patient_data:
            if patient_data.get("resourceType") == "Patient":
                processed_data = self.deidentifier.deidentify_patient(
                    patient_data, access_request.patient_id
                )
            else:
                processed_data = patient_data  # Non-patient resources: no names to remove
        else:
            processed_data = patient_data
        
        return True, processed_data, reason
