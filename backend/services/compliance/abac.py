"""
Attribute-Based Access Control Engine.
Full implementation in gateway.py (ABACEngine class).
"""
from .gateway import ABACEngine, AccessRequest, UserRole, DataSensitivity, TimeContext, CareRelationship, AccessAction, AccessReason
__all__ = ["ABACEngine", "AccessRequest", "UserRole", "DataSensitivity", "TimeContext", "CareRelationship", "AccessAction", "AccessReason"]
