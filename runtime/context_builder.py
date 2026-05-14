# coaction_agent_platform/runtime/context_builder.py
"""Context builder for agent invocations.

Builds the runtime context from memory, retrieval, and request metadata.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    ExecutionProfile,
    IdentityContext,
)

logger = structlog.get_logger(__name__)


class ContextBuilder:
    """Builds the runtime context that feeds into the Strands agent."""

    async def build(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
        profile: ExecutionProfile,
        memory_context: dict | None = None,
        retrieved_context: list | None = None,
    ) -> dict:
        """Build the full runtime context for agent execution."""
        context = {
            "agent_id": request.agent_id,
            "user_id": identity.user_id,
            "roles": identity.roles,
            "channel": identity.channel,
            "correlation_id": identity.correlation_id,
            "session_id": request.session_id,
            "input_text": request.input_text,
            "profile_version": profile.version,
        }

        if memory_context:
            context["memory"] = memory_context

        if retrieved_context:
            context["retrieved"] = retrieved_context

        return context
