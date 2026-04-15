"""
Compliance Middleware — PHI De-identification Safety Net.
Intercepts responses for AI_SYSTEM / RESEARCHER roles and scrubs any
PHI field names that slipped through the service layer.
This is defense-in-depth — service layer should de-identify first.
"""
import json, logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from fastapi import Request, Response
from typing import Callable, Any, Set

logger = logging.getLogger(__name__)

PHI_FIELDS: Set[str] = {
    "full_name", "first_name", "last_name", "middle_name",
    "date_of_birth", "birthdate", "dob", "phone", "email",
    "mrn", "ssn", "ssn_last4", "address", "address_line1",
    "city", "patient_name", "name_raw",
}

ROLES_REQUIRING_DEIDENT = {"ai_system", "researcher"}


class ComplianceMiddleware(BaseHTTPMiddleware):
    """PHI scrubbing safety net for AI_SYSTEM and RESEARCHER responses."""

    def __init__(self, app: ASGIApp):
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        user = getattr(request.state, "user", {})

        if user.get("role") not in ROLES_REQUIRING_DEIDENT:
            return response
        if "application/json" not in response.headers.get("content-type", ""):
            return response

        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            data = json.loads(body)
            cleaned, n_scrubbed = _scrub(data, PHI_FIELDS)
            if n_scrubbed:
                logger.warning(
                    f"PHI_SCRUBBED_MIDDLEWARE fields={n_scrubbed} "
                    f"actor={user.get('user_id')} path={request.url.path}"
                )
            new_body = json.dumps(cleaned).encode()
        except Exception:
            new_body = body

        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )


def _scrub(data: Any, phi_fields: Set[str]) -> tuple:
    """Recursively scrub PHI field names. Returns (cleaned_data, n_scrubbed)."""
    n = 0
    if isinstance(data, dict):
        cleaned = {}
        for k, v in data.items():
            if k in phi_fields:
                n += 1
            else:
                sub, sub_n = _scrub(v, phi_fields)
                cleaned[k] = sub
                n += sub_n
        return cleaned, n
    if isinstance(data, list):
        result, total = [], 0
        for item in data:
            sub, sub_n = _scrub(item, phi_fields)
            result.append(sub)
            total += sub_n
        return result, total
    return data, 0
