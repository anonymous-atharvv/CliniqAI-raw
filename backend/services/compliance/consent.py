"""
Consent Management — patient consent state storage and enforcement.
Full implementation in gateway.py (ConsentManager class).
Check ai_inference consent before any AI processing.
"""
from .gateway import ConsentManager, ConsentState
__all__ = ["ConsentManager", "ConsentState"]
