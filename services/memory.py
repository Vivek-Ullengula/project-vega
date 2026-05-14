# coaction_agent_platform/services/memory.py
"""AgentCore Memory provider per HLD Section 9.2.

Persistent memory is default for all agents. Every agent has a memory profile,
retention rule, and audit metadata.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
    ExecutionProfile,
)

logger = structlog.get_logger(__name__)


class AgentCoreMemoryProvider:
    """AgentCore Memory integration — read/write scoped persistent memory.

    In the first release, memory is stored in DynamoDB via the session system.
    Future: integrate with Amazon Bedrock AgentCore Memory API.
    """

    def __init__(self, dynamodb_adapter=None, boto3_factory=None):
        self.dynamodb = dynamodb_adapter
        self.boto3_factory = boto3_factory

    async def read(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
        profile: ExecutionProfile,
    ) -> dict:
        """Read scoped persistent memory for this agent/user/session."""
        if not profile.memory_profile.enabled or not profile.memory_profile.read_enabled:
            return {}

        if not self.dynamodb or not request.session_id:
            return {}

        session = self.dynamodb.get_session(identity.user_id, request.session_id)
        if not session:
            return {}

        return {
            "messages": session.get("messages", []),
            "session_id": request.session_id,
            "scope": profile.memory_profile.memory_scope,
        }

    async def write(
        self,
        request: AgentInvocationRequest,
        response: AgentInvocationResponse,
        identity: IdentityContext,
        profile: ExecutionProfile,
    ) -> None:
        """Write memory after applying data minimization rules."""
        if not profile.memory_profile.enabled or not profile.memory_profile.write_enabled:
            return

        # Memory write is handled by the AgentService during session save.
        # This hook exists for future AgentCore Memory API integration.
        logger.debug(
            "memory_write_delegated",
            session_id=response.session_id,
            scope=profile.memory_profile.memory_scope,
        )
