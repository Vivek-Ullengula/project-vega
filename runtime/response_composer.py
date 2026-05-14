# coaction_agent_platform/runtime/response_composer.py
"""Response composition per HLD Section 8.

Composes the final AgentInvocationResponse from model results,
retrieved context, memory context, and tool results.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    ExecutionProfile,
    SourceCitation,
    ToolResult,
)

logger = structlog.get_logger(__name__)


class ResponseComposer:
    """Assembles the final response from the orchestration pipeline outputs."""

    async def compose(
        self,
        request: AgentInvocationRequest,
        profile: ExecutionProfile,
        model_result: dict,
        retrieved_context: list[SourceCitation] | None = None,
        memory_context: dict | None = None,
        tool_results: list[ToolResult] | None = None,
        session_id: str = "",
        correlation_id: str = "",
    ) -> AgentInvocationResponse:
        """Compose the final response envelope."""
        answer = model_result.get("answer", "")
        status = model_result.get("status", "success")

        citations = retrieved_context or []
        tools = tool_results or []

        metadata = {
            k: v
            for k, v in model_result.items()
            if k not in ("answer", "status", "citations", "agent_messages")
        }

        return AgentInvocationResponse(
            status=status,
            answer=answer,
            citations=citations,
            tool_results=tools,
            session_id=session_id,
            correlation_id=correlation_id,
            model_id=profile.model_profile.model_id,
            metadata=metadata,
        )
