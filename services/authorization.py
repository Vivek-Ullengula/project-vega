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

        In the first release, authorization is permissive — any authenticated
        user can invoke an agent. Tool permissions are checked per-tool by
        the ToolGateway.
        """
        if not identity.user_id:
            raise HTTPException(status_code=401, detail="Missing identity context")

        # Check tool-level role restrictions (if any)
        for perm in profile.tool_permissions:
            if perm.allowed_roles and not any(r in perm.allowed_roles for r in identity.roles):
                logger.warning(
                    "tool_access_denied",
                    tool_id=perm.tool_id,
                    user_roles=identity.roles,
                    required_roles=perm.allowed_roles,
                )

        logger.info(
            "authorization_passed",
            user_id=identity.user_id,
            agent_id=profile.agent_id,
        )
