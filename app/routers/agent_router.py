# coaction_agent_platform/app/routers/agent_router.py
"""Agent invocation endpoint."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
)
from app.dependencies.identity import get_identity_context

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/agents", tags=["Agent"])

# Module-level reference — set at app startup
_agent_service = None


def init_agent_router(agent_service) -> None:
    """Initialize with AgentService instance."""
    global _agent_service
    _agent_service = agent_service


# ── Request Model ────────────────────────────────────────────────────────


class InvokeRequest(BaseModel):
    input_text: str
    session_id: str | None = None
    top_k: int = 5


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/{agent_id}/invoke", response_model=AgentInvocationResponse)
async def invoke_agent(
    agent_id: str,
    req: InvokeRequest,
    identity: IdentityContext = Depends(get_identity_context),
):
    """Invoke an agent with a query.

    The agent_id determines which ExecutionProfile to load.
    Authentication is handled via Cognito JWT in the Authorization header.
    """
    if not _agent_service:
        raise HTTPException(status_code=503, detail="Agent service not initialized")

    invocation = AgentInvocationRequest(
        agent_id=agent_id,
        input_text=req.input_text,
        session_id=req.session_id,
    )

    response = await _agent_service.invoke(invocation, identity)
    return response


@router.get("/{agent_id}/health")
async def agent_health(agent_id: str):
    """Check if an agent is configured and ready."""
    if not _agent_service:
        return {"status": "unavailable", "agent_id": agent_id}

    return {"status": "healthy", "agent_id": agent_id}
