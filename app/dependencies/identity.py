# coaction_agent_platform/app/dependencies/identity.py
"""FastAPI dependency: extract and verify user identity from Cognito JWT tokens."""

import uuid
import structlog
from fastapi import Header, HTTPException, Depends
from jose import JWTError

from domain.models import IdentityContext
from adapters.aws.jwt_verifier import CognitoJWTVerifier

logger = structlog.get_logger(__name__)

# Module-level verifier instance — initialized at app startup
_verifier: CognitoJWTVerifier | None = None


def init_jwt_verifier(region: str, user_pool_id: str, app_client_id: str) -> None:
    """Initialize the JWT verifier (called once at app startup)."""
    global _verifier
    _verifier = CognitoJWTVerifier(
        region=region,
        user_pool_id=user_pool_id,
        app_client_id=app_client_id,
    )


def get_jwt_verifier() -> CognitoJWTVerifier:
    """Get the initialized JWT verifier."""
    if _verifier is None:
        raise RuntimeError("JWT verifier not initialized. Call init_jwt_verifier() at startup.")
    return _verifier


async def get_identity_context(
    authorization: str = Header(None, alias="Authorization"),
    custom_auth: str = Header(None, alias="X-Amzn-Bedrock-AgentCore-Runtime-Custom-Authorization"),
) -> IdentityContext:
    """Parse and verify a Cognito JWT token from headers.

    Checks both standard 'Authorization' and AgentCore-specific custom auth headers.
    """
    token_str = authorization or custom_auth
    if not token_str or not token_str.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid Authorization header. Expected: Bearer <token>",
        )

    token = token_str[7:]  # Strip "Bearer "

    try:
        verifier = get_jwt_verifier()
        # Use access token for API authorization
        claims = verifier.verify_token(token, token_use="access")

        return IdentityContext(
            user_id=claims.get("sub", ""),
            roles=[claims.get("custom:role", "agent")],
            channel="api",
            correlation_id=str(uuid.uuid4()),
            session_id=None,
            claims=claims,
        )

    except JWTError as e:
        logger.warning("auth_failed", error=str(e))
        raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")
    except Exception as e:
        logger.error("auth_error", error=str(e))
        raise HTTPException(status_code=401, detail="Authentication failed")


def require_role(*allowed_roles: str):
    """Factory: create a dependency that requires the user to have one of the specified roles.

    Usage:
        @router.post("/knowledge-bases", dependencies=[Depends(require_role("underwriter"))])
    """

    async def _check_role(identity: IdentityContext = Depends(get_identity_context)):
        user_roles = {r.lower() for r in identity.roles}
        if not user_roles.intersection({r.lower() for r in allowed_roles}):
            raise HTTPException(
                status_code=403,
                detail=f"Insufficient permissions. Required role: {', '.join(allowed_roles)}",
            )
        return identity

    return _check_role
