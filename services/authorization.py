# coaction_agent_platform/services/authorization.py
"""Authorization service per HLD Section 8.

Enforces platform-level authorization and policy checks after
API Gateway authentication has terminated.
"""

import structlog
from fastapi import HTTPException

from domain.models import (
    IdentityContext,
    ExecutionProfile,
)

logger = structlog.get_logger(__name__)


class AuthorizationService:
    """Platform authorization — validates that a user is allowed to invoke an agent."""

    async def authorize_invocation(
        self,
        identity: IdentityContext,
        profile: ExecutionProfile,
    ) -> None:
        """Check that the identity is authorized to invoke this agent.

        The first release only exposes read tools, but role restrictions still
        need to be enforced before the agent can invoke those tools.
        """
        if not identity.user_id:
            raise HTTPException(status_code=401, detail="Missing identity context")

        # Check tool-level role restrictions (if any)
        for perm in profile.tool_permissions:
            allowed_roles = {role.lower() for role in perm.allowed_roles}
            user_roles = {role.lower() for role in (identity.roles or ["agent"])}
            if allowed_roles and not user_roles.intersection(allowed_roles):
                logger.warning(
                    "tool_access_denied",
                    tool_id=perm.tool_id,
                    user_roles=identity.roles,
                    required_roles=perm.allowed_roles,
                )
                raise HTTPException(
                    status_code=403,
                    detail=f"Role is not allowed to use required tool: {perm.tool_id}",
                )

        logger.info(
            "authorization_passed",
            user_id=identity.user_id,
            agent_id=profile.agent_id,
        )
