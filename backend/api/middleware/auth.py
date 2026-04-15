"""
API Middleware Stack
====================
Order of execution for every request:
  1. SecurityHeadersMiddleware  — HIPAA security headers
  2. RequestTracingMiddleware   — Request ID, timing
  3. AuthMiddleware             — JWT validation + claims extraction
  4. AuditMiddleware            — Immutable access logging
  5. ComplianceMiddleware       — PHI de-identification gate

All middleware is HIPAA-aware: no PHI in logs, no PHI in error messages.
"""

import jwt
import uuid
import time
import json
import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Callable, Any
from functools import wraps

from fastapi import Request, Response, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Endpoints that don't require authentication
PUBLIC_PATHS = {"/health", "/health/detailed", "/docs", "/openapi.json", "/redoc"}

# Endpoints where PHI may be returned (stricter logging)
PHI_PATHS = {"/api/v1/patients", "/api/v1/encounters"}


# ─────────────────────────────────────────────
# 1. Security Headers Middleware
# ─────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Inject HIPAA-aligned security headers on every response.
    Prevents: XSS, clickjacking, MIME sniffing, caching of PHI.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Prevent caching of any API response (PHI must not be cached)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"

        # Security hardening
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

        # HSTS: force HTTPS for 1 year (production only)
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"

        # CSP: restrict resource loading
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self' wss:; "
            "frame-ancestors 'none';"
        )

        # Remove server identification
        response.headers.pop("Server", None)
        response.headers.pop("X-Powered-By", None)

        return response


# ─────────────────────────────────────────────
# 2. Request Tracing Middleware
# ─────────────────────────────────────────────

class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Assign a unique request ID to every request.
    Track response timing.
    Log slow requests (>2s) for performance monitoring.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        request.state.start_time = time.time()

        response = await call_next(request)

        duration_ms = int((time.time() - request.state.start_time) * 1000)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Response-Time-Ms"] = str(duration_ms)

        # Log slow requests
        if duration_ms > 2000:
            logger.warning(
                f"SLOW_REQUEST path={request.url.path} "
                f"method={request.method} "
                f"duration_ms={duration_ms} "
                f"request_id={request_id}"
            )

        # Prometheus counter (production: use prometheus_client)
        if duration_ms > 5000:
            logger.error(
                f"VERY_SLOW_REQUEST path={request.url.path} "
                f"duration_ms={duration_ms} — SLA breach"
            )

        return response


# ─────────────────────────────────────────────
# 3. Authentication Middleware
# ─────────────────────────────────────────────

class AuthMiddleware(BaseHTTPMiddleware):
    """
    JWT validation middleware.
    
    Token format: Bearer <jwt>
    JWT claims required:
      - sub: user UUID
      - role: physician|nurse|radiologist|pharmacist|admin|researcher|ai_system
      - hospital_id: hospital UUID (row-level security)
      - department: clinical department
      - exp: expiry timestamp
    
    On failure: 401 Unauthorized (never 403 — don't reveal resource existence)
    """

    def __init__(self, app: ASGIApp, secret_key: str, algorithm: str = "HS256"):
        super().__init__(app)
        self.secret_key = secret_key
        self.algorithm = algorithm

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # WebSocket auth via query param (WebSocket limitation)
        if request.url.path.startswith("/ws/"):
            token = request.query_params.get("token")
        else:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Missing or invalid Authorization header"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            token = auth_header[7:]

        try:
            payload = jwt.decode(
                token,
                self.secret_key,
                algorithms=[self.algorithm],
                options={"verify_exp": True},
            )

            # Validate required claims
            required_claims = {"sub", "role", "hospital_id"}
            missing = required_claims - set(payload.keys())
            if missing:
                raise jwt.InvalidTokenError(f"Missing claims: {missing}")

            # Attach user context to request state
            request.state.user = {
                "user_id": payload["sub"],
                "role": payload["role"],
                "hospital_id": payload["hospital_id"],
                "department": payload.get("department", ""),
                "care_assignments": payload.get("care_assignments", []),
                "session_id": payload.get("jti", str(uuid.uuid4())),
            }

        except jwt.ExpiredSignatureError:
            return JSONResponse(
                status_code=401,
                content={"error": "Token expired. Please re-authenticate."},
            )
        except jwt.InvalidTokenError as e:
            logger.warning(f"Invalid JWT: {e} — IP: {_hash_ip(request.client.host)}")
            return JSONResponse(
                status_code=401,
                content={"error": "Invalid token"},
            )

        return await call_next(request)


# ─────────────────────────────────────────────
# 4. Audit Logging Middleware
# ─────────────────────────────────────────────

class AuditMiddleware(BaseHTTPMiddleware):
    """
    HIPAA-compliant immutable audit logging.
    
    Every request to /api/ generates an audit event.
    
    HIPAA 45 CFR §164.312(b): Audit Controls
    - Record all PHI access
    - Identify who accessed what, when, from where
    - Log successful AND denied accesses
    
    Storage: PostgreSQL (queryable) + S3 WORM (immutable archive)
    """

    # Map URL patterns to resource types
    RESOURCE_MAP = {
        "/api/v1/patients": "Patient",
        "/api/v1/vitals": "Observation",
        "/api/v1/inference": "ClinicalInference",
        "/api/v1/agents": "AgentSession",
        "/api/v1/admin": "AdminResource",
        "/api/v1/feedback": "Feedback",
        "/api/v1/compliance": "AuditLog",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only audit API paths
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        start_time = time.time()
        response = await call_next(request)
        duration_ms = int((time.time() - start_time) * 1000)

        # Determine resource type from path
        resource_type = "Unknown"
        for prefix, rtype in self.RESOURCE_MAP.items():
            if request.url.path.startswith(prefix):
                resource_type = rtype
                break

        # Determine action from HTTP method
        action_map = {"GET": "read", "POST": "write", "PUT": "write",
                      "PATCH": "write", "DELETE": "delete"}
        action = action_map.get(request.method, "unknown")

        # Determine outcome
        outcome = "success" if response.status_code < 400 else "denied"

        # Extract user (may not be set if auth failed)
        user = getattr(request.state, "user", {})

        # Extract resource ID from path
        path_parts = request.url.path.rstrip("/").split("/")
        resource_id = path_parts[-1] if len(path_parts) > 4 else "list"
        # Ensure resource ID doesn't contain PHI (UUID only)
        if not _is_safe_id(resource_id):
            resource_id = f"[REDACTED-{hashlib.sha256(resource_id.encode()).hexdigest()[:8]}]"

        audit_event = {
            "event_id": str(uuid.uuid4()),
            "event_timestamp": datetime.now(timezone.utc).isoformat(),
            "request_id": getattr(request.state, "request_id", "unknown"),
            "actor": user.get("user_id", "anonymous"),
            "actor_role": user.get("role", "unknown"),
            "actor_department": user.get("department", ""),
            "hospital_id": user.get("hospital_id", "unknown"),
            "session_id": user.get("session_id", ""),
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "outcome": outcome,
            "http_status": response.status_code,
            "ip_hash": _hash_ip(getattr(request.client, "host", "unknown")),
            "user_agent_hash": _hash_str(request.headers.get("User-Agent", "")),
            "api_endpoint": request.url.path,
            "duration_ms": duration_ms,
            "phi_accessed": resource_type in {"Patient", "Observation"},
        }

        # Write audit log (fire-and-forget)
        _write_audit_log(audit_event)

        # Alert on suspicious patterns
        if response.status_code == 403:
            logger.warning(
                f"ACCESS_DENIED actor={user.get('user_id')} "
                f"resource={resource_type}/{resource_id} "
                f"role={user.get('role')}"
            )

        return response


# ─────────────────────────────────────────────
# 5. Compliance Middleware  
# ─────────────────────────────────────────────

class ComplianceMiddleware(BaseHTTPMiddleware):
    """
    PHI de-identification enforcement at middleware level.
    
    Intercepts responses for AI_SYSTEM and RESEARCHER roles.
    Ensures de-identified fields are never accidentally returned.
    
    This is a safety net — the service layer should de-identify first.
    This middleware catches any that slip through.
    """

    # Fields that must never appear in AI_SYSTEM responses
    PHI_FIELD_NAMES = {
        "full_name", "date_of_birth", "phone", "email", "mrn",
        "ssn", "address", "patient_name", "first_name", "last_name",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        # Only check AI_SYSTEM role responses
        user = getattr(request.state, "user", {})
        if user.get("role") not in ["ai_system", "researcher"]:
            return response

        # Only check JSON responses
        if not response.headers.get("content-type", "").startswith("application/json"):
            return response

        # Read response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            data = json.loads(body)
            cleaned = _scrub_phi_fields(data, self.PHI_FIELD_NAMES)
            new_body = json.dumps(cleaned).encode()

            if new_body != body:
                logger.warning(
                    f"PHI_SCRUBBED_AT_MIDDLEWARE: "
                    f"actor={user.get('user_id')} "
                    f"path={request.url.path} "
                    f"— Service layer failed to de-identify"
                )

            return Response(
                content=new_body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type="application/json",
            )
        except Exception:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
            )


# ─────────────────────────────────────────────
# JWT Token Generation (for auth service)
# ─────────────────────────────────────────────

class TokenService:
    """
    JWT token issuance and validation.
    Used by /auth/login endpoint.
    """

    def __init__(self, secret_key: str, algorithm: str = "HS256"):
        self.secret_key = secret_key
        self.algorithm = algorithm

    def create_access_token(
        self,
        user_id: str,
        role: str,
        hospital_id: str,
        department: str,
        care_assignments: list,
        expires_minutes: int = 60,
    ) -> str:
        import time as time_mod
        payload = {
            "sub": user_id,
            "role": role,
            "hospital_id": hospital_id,
            "department": department,
            "care_assignments": care_assignments,
            "iat": int(time_mod.time()),
            "exp": int(time_mod.time()) + expires_minutes * 60,
            "jti": str(uuid.uuid4()),  # JWT ID for audit linkage
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, user_id: str, hospital_id: str, expires_days: int = 7) -> str:
        import time as time_mod
        payload = {
            "sub": user_id,
            "hospital_id": hospital_id,
            "type": "refresh",
            "iat": int(time_mod.time()),
            "exp": int(time_mod.time()) + expires_days * 86400,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> Dict:
        return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])


# ─────────────────────────────────────────────
# Auth Router (login / refresh / logout)
# ─────────────────────────────────────────────

from fastapi import APIRouter

auth_router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str
    password: str
    hospital_id: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = 3600
    user_role: str
    user_department: str




@auth_router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest):
    """
    Authenticate a user and return JWT tokens.
    
    Production: validate against hospital LDAP/AD or local user store.
    MFA required for all clinical users in production.
    """
    # Production: validate credentials against user store
    # Development: accept any credentials for testing
    role_map = {
        "physician_001": ("physician", "ICU"),
        "nurse_001": ("nurse", "ICU"),
        "admin_001": ("admin", "Administration"),
        "researcher_001": ("researcher", "Research"),
    }

    user_info = role_map.get(payload.username, ("physician", "ICU"))
    role, department = user_info

    token_svc = TokenService(secret_key="dev-secret-CHANGE-IN-PRODUCTION")
    access_token = token_svc.create_access_token(
        user_id=payload.username,
        role=role,
        hospital_id=payload.hospital_id,
        department=department,
        care_assignments=[],
    )
    refresh_token = token_svc.create_refresh_token(payload.username, payload.hospital_id)

    logger.info(f"LOGIN: user={payload.username} role={role} hospital={payload.hospital_id}")

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user_role=role,
        user_department=department,
    )


@auth_router.post("/refresh")
async def refresh_token(refresh_token: str):
    """Exchange refresh token for new access token."""
    try:
        token_svc = TokenService(secret_key="dev-secret-CHANGE-IN-PRODUCTION")
        claims = token_svc.verify_token(refresh_token)
        if claims.get("type") != "refresh":
            raise HTTPException(401, "Invalid refresh token type")

        new_access = token_svc.create_access_token(
            user_id=claims["sub"],
            role=claims.get("role", "physician"),
            hospital_id=claims["hospital_id"],
            department=claims.get("department", ""),
            care_assignments=claims.get("care_assignments", []),
        )
        return {"access_token": new_access, "token_type": "bearer", "expires_in": 3600}
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Refresh token expired. Please log in again.")
    except Exception:
        raise HTTPException(401, "Invalid refresh token")


@auth_router.post("/logout")
async def logout(request: Request):
    """
    Logout endpoint.
    Production: add token JTI to Redis blocklist until expiry.
    """
    user = getattr(request.state, "user", {})
    logger.info(f"LOGOUT: user={user.get('user_id')}")
    # Production: redis.setex(f"blocklist:{jti}", ttl, "1")
    return {"message": "Logged out successfully"}


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _hash_ip(ip: str) -> str:
    """Hash IP address for audit log privacy."""
    return hashlib.sha256(ip.encode()).hexdigest()[:16]


def _hash_str(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _is_safe_id(s: str) -> bool:
    """Check if a string is a UUID or short alphanumeric (safe for logging)."""
    import re
    return bool(re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$|^[a-z0-9_-]{1,32}$",
        s.lower()
    ))


def _write_audit_log(event: dict):
    """Write audit event to storage backends."""
    # Production:
    # 1. asyncpg INSERT into cliniqai_audit.access_log
    # 2. S3 WORM append (via Kinesis Firehose)
    # Development: log to stdout
    logger.info(f"AUDIT {json.dumps(event)}")


def _scrub_phi_fields(data: Any, phi_fields: set) -> Any:
    """Recursively remove PHI field names from response data."""
    if isinstance(data, dict):
        return {
            k: _scrub_phi_fields(v, phi_fields)
            for k, v in data.items()
            if k not in phi_fields
        }
    elif isinstance(data, list):
        return [_scrub_phi_fields(item, phi_fields) for item in data]
    return data
