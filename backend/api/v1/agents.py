"""
Agents API — v1
Multi-agent system status, session management, and escalation tracking.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime, timezone
from uuid import UUID
import uuid, asyncio, logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agents", tags=["Multi-Agent System"])


class AgentStatusResponse(BaseModel):
    agent_id: str
    agent_name: str
    status: str          # idle|running|completed|failed|timeout|circuit_open
    last_run: Optional[str]
    avg_latency_ms: int
    success_rate: float
    circuit_breaker_open: bool
    consecutive_failures: int


class SessionSummary(BaseModel):
    session_id: str
    patient_deident_id: str
    created_at: str
    last_updated: str
    final_risk_level: Optional[str]
    coordinator_confidence: Optional[float]
    agents_completed: List[str]
    agents_failed: List[str]
    escalations_sent: int


class EscalationRecord(BaseModel):
    escalation_id: str
    patient_deident_id: str
    escalation_type: str
    message: str
    recipients: List[str]
    created_at: str
    sla_deadline: str
    acknowledged: bool
    acknowledged_at: Optional[str]


async def get_current_user():
    return {"user_id": "dev-001", "role": "physician", "hospital_id": "hospital_dev_001"}


@router.get(
    "/status",
    response_model=List[AgentStatusResponse],
    summary="Get real-time status of all 7 agents",
)
async def get_all_agent_status(user=Depends(get_current_user)):
    return [
        AgentStatusResponse(agent_id="triage_agent",        agent_name="Triage Agent",
            status="idle", last_run="2026-04-10T09:14:01Z", avg_latency_ms=420,
            success_rate=0.994, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="risk_agent",          agent_name="Risk Agent",
            status="running", last_run="2026-04-10T09:14:18Z", avg_latency_ms=1240,
            success_rate=0.998, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="diagnosis_agent",     agent_name="Diagnosis Agent",
            status="idle", last_run="2026-04-10T09:13:44Z", avg_latency_ms=3820,
            success_rate=0.991, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="pharmacist_agent",    agent_name="Pharmacist Agent",
            status="idle", last_run="2026-04-10T09:12:30Z", avg_latency_ms=810,
            success_rate=0.997, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="documentation_agent", agent_name="Documentation Agent",
            status="idle", last_run="2026-04-10T09:11:05Z", avg_latency_ms=2640,
            success_rate=0.989, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="coordinator_agent",   agent_name="Coordinator Agent",
            status="idle", last_run="2026-04-10T09:14:22Z", avg_latency_ms=310,
            success_rate=0.999, circuit_breaker_open=False, consecutive_failures=0),
        AgentStatusResponse(agent_id="escalation_agent",    agent_name="Escalation Agent",
            status="idle", last_run="2026-04-10T09:14:25Z", avg_latency_ms=95,
            success_rate=1.000, circuit_breaker_open=False, consecutive_failures=0),
    ]


@router.get("/status/{agent_id}", response_model=AgentStatusResponse, summary="Get status of a specific agent")
async def get_agent_status(agent_id: str, user=Depends(get_current_user)):
    agents = {
        "risk_agent": AgentStatusResponse(agent_id="risk_agent", agent_name="Risk Agent",
            status="running", last_run="2026-04-10T09:14:18Z", avg_latency_ms=1240,
            success_rate=0.998, circuit_breaker_open=False, consecutive_failures=0),
    }
    agent = agents.get(agent_id)
    if not agent:
        raise HTTPException(404, f"Agent '{agent_id}' not found")
    return agent


@router.get(
    "/sessions/{patient_deident_id}",
    response_model=SessionSummary,
    summary="Get active agent session for a patient",
)
async def get_patient_session(patient_deident_id: UUID, user=Depends(get_current_user)):
    return SessionSummary(
        session_id=str(uuid.uuid4()),
        patient_deident_id=str(patient_deident_id),
        created_at="2026-04-10T08:00:00Z",
        last_updated="2026-04-10T09:14:28Z",
        final_risk_level="HIGH",
        coordinator_confidence=0.82,
        agents_completed=["triage_agent", "risk_agent", "pharmacist_agent", "coordinator_agent", "escalation_agent"],
        agents_failed=[],
        escalations_sent=0,
    )


@router.post(
    "/sessions/{patient_deident_id}/trigger",
    status_code=202,
    summary="Manually trigger agent pipeline for a patient",
)
async def trigger_agent_pipeline(
    patient_deident_id: UUID,
    reason: str = Query(..., description="Clinical reason for manual trigger"),
    user=Depends(get_current_user),
):
    logger.info(f"MANUAL_TRIGGER patient={patient_deident_id} reason={reason} actor={user['user_id']}")
    return {
        "status": "triggered",
        "session_id": str(uuid.uuid4()),
        "patient_deident_id": str(patient_deident_id),
        "reason": reason,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "estimated_completion_seconds": 8,
    }


@router.get(
    "/escalations",
    response_model=List[EscalationRecord],
    summary="Get active escalations requiring acknowledgment",
)
async def get_active_escalations(
    ward_code: Optional[str] = Query(None),
    acknowledged: bool = Query(False),
    user=Depends(get_current_user),
):
    return [
        EscalationRecord(
            escalation_id=str(uuid.uuid4()),
            patient_deident_id=str(uuid.uuid4()),
            escalation_type="CRITICAL_RISK",
            message="CRITICAL ALERT: Patient risk level CRITICAL — NEWS2=9, sepsis probability 81%",
            recipients=["physician_attending", "charge_nurse"],
            created_at="2026-04-10T09:02:11Z",
            sla_deadline="2026-04-10T09:07:11Z",
            acknowledged=False,
            acknowledged_at=None,
        ),
    ]


@router.post(
    "/escalations/{escalation_id}/acknowledge",
    summary="Acknowledge an escalation alert",
)
async def acknowledge_escalation(
    escalation_id: str,
    notes: Optional[str] = None,
    user=Depends(get_current_user),
):
    return {
        "escalation_id": escalation_id,
        "acknowledged": True,
        "acknowledged_by": user["user_id"],
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }


@router.get("/metrics", summary="Agent system performance metrics")
async def get_agent_metrics(
    hours: int = Query(24, ge=1, le=168),
    user=Depends(get_current_user),
):
    return {
        "period_hours": hours,
        "total_pipeline_runs": 1847,
        "avg_end_to_end_ms": 4210,
        "p95_end_to_end_ms": 8940,
        "agent_timeouts": 3,
        "circuit_breaker_trips": 0,
        "escalations_sent": 12,
        "escalations_acknowledged": 12,
        "avg_acknowledgment_seconds": 87,
        "sla_breaches": 0,
        "coordinator_confidence_avg": 0.81,
        "pharmacy_critical_alerts": 2,
        "by_agent": {
            "triage_agent":        {"runs": 1847, "failures": 4,  "avg_ms": 420},
            "risk_agent":          {"runs": 1847, "failures": 2,  "avg_ms": 1240},
            "diagnosis_agent":     {"runs": 612,  "failures": 8,  "avg_ms": 3820},
            "pharmacist_agent":    {"runs": 1847, "failures": 3,  "avg_ms": 810},
            "documentation_agent": {"runs": 891,  "failures": 11, "avg_ms": 2640},
            "coordinator_agent":   {"runs": 1847, "failures": 1,  "avg_ms": 310},
            "escalation_agent":    {"runs": 1847, "failures": 0,  "avg_ms": 95},
        },
    }


@router.websocket("/ws/sessions/{patient_deident_id}")
async def agent_session_stream(websocket: WebSocket, patient_deident_id: str):
    """
    Real-time agent session stream.
    Pushes agent state changes as they happen during pipeline execution.
    """
    await websocket.accept()
    try:
        agents = ["triage_agent", "risk_agent", "pharmacist_agent",
                  "coordinator_agent", "escalation_agent"]
        for agent in agents:
            await asyncio.sleep(0.5)
            await websocket.send_json({
                "type": "agent_update",
                "patient_id": patient_deident_id,
                "agent_id": agent,
                "status": "completed",
                "latency_ms": 800,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        await websocket.send_json({
            "type": "pipeline_complete",
            "patient_id": patient_deident_id,
            "risk_level": "HIGH",
            "coordinator_confidence": 0.82,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        while True:
            await asyncio.sleep(30)
            await websocket.send_json({"type": "heartbeat"})
    except WebSocketDisconnect:
        pass
