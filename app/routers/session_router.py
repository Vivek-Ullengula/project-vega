# coaction_agent_platform/app/routers/session_router.py
"""Chat session endpoints — CRUD operations backed by DynamoDB."""

import structlog
from fastapi import APIRouter, Depends, HTTPException

from domain.models import IdentityContext
from app.dependencies.identity import get_identity_context

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/sessions", tags=["Sessions"])

# Module-level reference — set at app startup
_dynamodb = None


def init_session_router(dynamodb_adapter) -> None:
    """Initialize with DynamoDB adapter (called at app startup)."""
    global _dynamodb
    _dynamodb = dynamodb_adapter


@router.get("")
async def list_sessions(identity: IdentityContext = Depends(get_identity_context)):
    """List the current user's chat sessions."""
    if not _dynamodb:
        raise HTTPException(status_code=503, detail="Database not initialized")

    sessions = _dynamodb.list_user_sessions(identity.user_id)
    return [
        {
            "session_id": s["session_id"],
            "title": s.get("title", "New Chat"),
            "last_accessed": s.get("last_accessed", ""),
            "message_count": len(s.get("messages", [])),
        }
        for s in sessions
    ]


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    identity: IdentityContext = Depends(get_identity_context),
):
    """Get a specific chat session with its messages."""
    if not _dynamodb:
        raise HTTPException(status_code=503, detail="Database not initialized")

    session = _dynamodb.get_session(identity.user_id, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    return {
        "session_id": session["session_id"],
        "title": session.get("title", "New Chat"),
        "messages": session.get("messages", []),
        "last_accessed": session.get("last_accessed", ""),
    }


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    identity: IdentityContext = Depends(get_identity_context),
):
    """Delete a chat session."""
    if not _dynamodb:
        raise HTTPException(status_code=503, detail="Database not initialized")

    _dynamodb.delete_session(identity.user_id, session_id)
    return {"message": "Session deleted"}
