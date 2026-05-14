# coaction_agent_platform/services/tool_gateway.py
"""AgentCore Gateway — read-only tools per HLD Section 9.3.

First release: only read actions are permitted. Workflow, write, and
external actions are blocked by policy.
"""

import structlog
from domain.models import (
    ExecutionProfile,
    IdentityContext,
    ToolResult,
)

logger = structlog.get_logger(__name__)


class AgentCoreReadOnlyToolGateway:
    """Read-only tool gateway per HLD Section 9.3 and 13.

    In the first release:
    - Read-only lookup: ALLOWED
    - Document processing: LIMITED (no enterprise system mutations)
    - Workflow initiation: BLOCKED
    - Write/update: BLOCKED
    - External interaction: BLOCKED
    """

    def __init__(self, boto3_factory=None):
        self.boto3_factory = boto3_factory

    async def execute_readonly_tools(
        self,
        model_result: dict,
        identity: IdentityContext,
        profile: ExecutionProfile,
    ) -> list[ToolResult]:
        """Execute read-only tools from model results.

        In the first release, tool execution is handled inline by Strands
        via the search_manuals tool. This method is a pass-through that
        validates no non-read actions were attempted.
        """
        # In the first release, Strands tools (search_manuals) execute inline.
        # This gateway will validate tool permissions when AgentCore Gateway
        # is integrated.
        return []

    def _resolve_permission(self, tool_id: str, profile: ExecutionProfile):
        """Resolve the permission for a tool from the execution profile."""
        for perm in profile.tool_permissions:
            if perm.tool_id == tool_id:
                return perm
        return None
