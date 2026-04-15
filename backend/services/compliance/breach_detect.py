"""
Breach Detection — anomalous access pattern monitoring.
Full implementation in gateway.py (BreachDetector class).
HIPAA requires breach notification within 60 days; we alert within 15 minutes.
"""
from .gateway import BreachDetector
__all__ = ["BreachDetector"]
