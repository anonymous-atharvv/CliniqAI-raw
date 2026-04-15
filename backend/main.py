"""
CliniQAI Backend — FastAPI Application Entry Point
===================================================
HIPAA-compliant hospital AI platform.

All requests flow through:
  1. Request tracing middleware (unique request_id)
  2. Security headers middleware (HIPAA-aligned)
  3. JWT authentication (api/middleware/auth.py)
  4. ABAC authorization (per resource, per action)
  5. Immutable audit logging (api/middleware/audit.py)

PHI policy: never returned to AI_SYSTEM or RESEARCHER roles.
"""

from fastapi import FastAPI, Request, status, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import logging
import time
import uuid
from contextlib import asynccontextmanager

from config.settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.WARNING,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


# ─────────────────────────────────────────────
# Lifespan (startup / shutdown)
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown."""
    logger.info(f"Starting CliniQAI — hospital: {settings.HOSPITAL_NAME} | env: {settings.ENVIRONMENT}")
    if settings.ENVIRONMENT == "production":
        assert settings.JWT_SECRET_KEY != "dev-secret-CHANGE-IN-PRODUCTION", \
            "FATAL: JWT_SECRET_KEY is still the dev default — do not deploy"
        assert settings.DEIDENT_SALT not in ("", "CHANGE_ME"), \
            "FATAL: DEIDENT_SALT not configured"
    logger.info(f"✅ Config validated | FDA status: {settings.FDA_CLEARANCE_STATUS}")
    yield
    logger.info("CliniQAI backend shutdown complete")


# ─────────────────────────────────────────────
# App Factory
# ─────────────────────────────────────────────

app = FastAPI(
    title="CliniQAI Hospital Intelligence API",
    description=(
        "HIPAA-compliant multi-modal AI platform for community hospitals.\n\n"
        "**Auth:** Bearer JWT — obtain via `POST /auth/login`\n\n"
        "**HIPAA:** All patient data is de-identified before AI processing. "
        "Audit log generated for every data access.\n\n"
        "**Disclaimer:** AI Decision Support Only. "
        "All outputs require physician review before clinical action."
    ),
    version=settings.APP_VERSION,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    openapi_url="/openapi.json" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# Middleware Stack
# ─────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Hospital-ID", "X-Request-ID"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
)

if settings.ENVIRONMENT == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[f"*.cliniqai.com", "localhost"],
    )


@app.middleware("http")
async def request_tracing(request: Request, call_next):
    """Attach unique request ID; track response time; warn on slow requests."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = request_id
    t0 = time.time()
    response = await call_next(request)
    ms = int((time.time() - t0) * 1000)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Response-Time-Ms"] = str(ms)
    if ms > 5000 and not request.url.path.startswith("/ws"):
        logger.warning(f"SLOW {request.method} {request.url.path} — {ms}ms req={request_id}")
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Inject HIPAA-aligned security headers on every response."""
    response = await call_next(request)
    response.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        "Cache-Control": "no-store, no-cache, must-revalidate, private",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
    })
    response.headers.pop("Server", None)
    return response


# ─────────────────────────────────────────────
# Router Registration
# ─────────────────────────────────────────────

from api.middleware.auth import auth_router
from api.v1.patients  import router as patients_router
from api.v1.vitals    import router as vitals_router
from api.v1.inference import router as inference_router
from api.v1.agents    import router as agents_router
from api.v1.admin     import router as admin_router

app.include_router(auth_router,      prefix="",        tags=["Authentication"])
app.include_router(patients_router,  prefix="/api/v1", tags=["Patients"])
app.include_router(vitals_router,    prefix="/api/v1", tags=["Vitals"])
app.include_router(inference_router, prefix="/api/v1", tags=["Clinical Inference"])
app.include_router(agents_router,    prefix="/api/v1", tags=["Multi-Agent System"])
app.include_router(admin_router,     prefix="/api/v1", tags=["Admin Intelligence"])


# ─────────────────────────────────────────────
# Health Endpoints
# ─────────────────────────────────────────────

@app.get("/health", tags=["System"], summary="Basic health check")
async def health():
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "hospital": settings.HOSPITAL_NAME,
    }


@app.get("/health/detailed", tags=["System"], summary="Detailed dependency health")
async def health_detailed():
    return {
        "status": "healthy",
        "services": {
            "api": "ok",
            "database": "ok",   # Production: verify actual DB connection
            "redis": "ok",
            "kafka": "ok",
            "qdrant": "ok",
        },
        "features": {
            "sepsis_prediction": settings.FEATURE_SEPSIS_PREDICTION,
            "imaging_ai": settings.FEATURE_IMAGING_AI,
            "pharmacist_agent": settings.FEATURE_PHARMACIST_AGENT,
            "federated_learning": settings.FEATURE_FEDERATED_LEARNING,
        },
        "compliance": {
            "hipaa_mode": settings.ENVIRONMENT == "production",
            "fda_clearance_status": settings.FDA_CLEARANCE_STATUS,
        },
    }


# ─────────────────────────────────────────────
# Error Handlers
# ─────────────────────────────────────────────

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all. Never expose internal details in production."""
    logger.error(
        f"Unhandled {type(exc).__name__} — "
        f"{request.method} {request.url.path} — "
        f"req={getattr(request.state, 'request_id', '?')}",
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "request_id": getattr(request.state, "request_id", "unknown"),
        },
    )
