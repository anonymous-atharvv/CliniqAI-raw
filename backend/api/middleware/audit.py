"""
Audit Middleware — Immutable HIPAA audit logging on every API request.
Writes to PostgreSQL + S3 WORM bucket. Retained 6 years minimum.
"""
import uuid, json, time, hashlib, logging
from datetime import datetime, timezone
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from fastapi import Request, Response
from typing import Callable

logger = logging.getLogger(__name__)

RESOURCE_MAP = {
    "/api/v1/patients":   "Patient",
    "/api/v1/vitals":     "Observation",
    "/api/v1/inference":  "ClinicalInference",
    "/api/v1/agents":     "AgentSession",
    "/api/v1/admin":      "AdminResource",
    "/api/v1/feedback":   "Feedback",
    "/api/v1/compliance": "AuditLog",
}

class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """
    HIPAA §164.312(b) — Audit Controls.
    Every API request generates an immutable audit event.
    Denials logged as rigorously as successes.
    """
    def __init__(self, app: ASGIApp, audit_backend=None):
        super().__init__(app)
        self._backend = audit_backend

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        t0 = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - t0) * 1000)

        user = getattr(request.state, "user", {})
        path = request.url.path
        resource_type = next((v for k, v in RESOURCE_MAP.items() if path.startswith(k)), "Unknown")
        action = {"GET":"read","POST":"write","PUT":"write","PATCH":"write","DELETE":"delete"}.get(request.method, "unknown")
        outcome = "success" if response.status_code < 400 else "denied"
        parts = path.rstrip("/").split("/")
        rid = parts[-1] if len(parts) > 4 else "list"
        if not _safe_id(rid):
            rid = f"[ID:{hashlib.sha256(rid.encode()).hexdigest()[:8]}]"

        event = {
            "event_id": str(uuid.uuid4()),
            "event_timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": getattr(request.state, "request_id", ""),
            "actor": user.get("user_id", "anonymous"),
            "actor_role": user.get("role", "unknown"),
            "actor_department": user.get("department", ""),
            "hospital_id": user.get("hospital_id", "unknown"),
            "action": action,
            "resource_type": resource_type,
            "resource_id": rid,
            "outcome": outcome,
            "http_status": response.status_code,
            "ip_hash": hashlib.sha256(
                getattr(getattr(request, "client", None), "host", "").encode()
            ).hexdigest()[:16],
            "api_endpoint": path,
            "duration_ms": duration_ms,
            "phi_accessed": resource_type in {"Patient", "Observation"},
        }
        _write_audit(event, self._backend)
        if response.status_code == 403:
            logger.warning(f"ACCESS_DENIED actor={user.get('user_id')} resource={resource_type}/{rid}")
        return response

def _safe_id(s: str) -> bool:
    import re
    return bool(re.match(r'^[0-9a-f\-]{8,36}$|^[a-z0-9_-]{1,32}$', s.lower()))

def _write_audit(event: dict, backend=None):
    if backend:
        try:
            backend.append(json.dumps(event))
        except Exception as e:
            logger.error(f"Audit write failed: {e}")
    logger.info(f"AUDIT {json.dumps(event)}")
