# coaction_agent_platform/services/audit.py
"""Metadata-only audit logger per HLD Section 12.

Persists metadata, correlation IDs, source IDs, tool IDs, model IDs,
status, and audit outcomes only. No raw prompt or response logging.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
    ExecutionProfile,
)

logger = structlog.get_logger(__name__)


class MetadataOnlyAuditLogger:
    """Audit logger — captures invocation metadata for compliance and debugging.

    Per HLD: raw_prompt_logged and raw_response_logged are always False by default.
    """

    async def record_invocation(
        self,
        request: AgentInvocationRequest,
        response: AgentInvocationResponse,
        identity: IdentityContext,
        profile: ExecutionProfile,
    ) -> None:
        """Record an audit event for an agent invocation."""
        audit_event = {
            "correlation_id": identity.correlation_id,
            "agent_id": request.agent_id,
            "agent_version": profile.version,
            "user_id": identity.user_id,
            "channel": identity.channel,
            "status": response.status,
            "model_id": response.model_id,
            "citation_count": len(response.citations),
            "tool_count": len(response.tool_results),
            "raw_prompt_logged": False,
            "raw_response_logged": False,
        }

        logger.info("audit_invocation_recorded", **audit_event)
