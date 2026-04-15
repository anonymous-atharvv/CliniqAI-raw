"""
CliniQAI API v1 — Router registration.
All routers are imported and registered in backend/main.py.
"""
from .patients import router as patients_router
from .vitals import router as vitals_router
from .inference import router as inference_router
from .agents import router as agents_router
from .admin import router as admin_router

__all__ = [
    "patients_router",
    "vitals_router",
    "inference_router",
    "agents_router",
    "admin_router",
]
