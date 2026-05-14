import pytest
from services.authorization import AuthorizationService
from domain.models import IdentityContext, ExecutionProfile, ModelProfile, RetrievalProfile


def _make_profile():
    return ExecutionProfile(
        agent_id="test_agent",
        version="1.0",
        prompt_template_id="test",
        model_profile=ModelProfile(model_id="model-1"),
        retrieval_profile=RetrievalProfile(knowledge_base_ids=["kb-1"]),
    )


@pytest.mark.asyncio
async def test_authorize_success():
    auth_service = AuthorizationService()
    identity = IdentityContext(
        user_id="user123",
        roles=["admin"],
        channel="api",
        correlation_id="corr-1",
    )
    # Should not raise any exception
    await auth_service.authorize_invocation(identity, _make_profile())


@pytest.mark.asyncio
async def test_authorize_missing_user():
    from fastapi import HTTPException

    auth_service = AuthorizationService()
    identity = IdentityContext(
        user_id="",
        roles=["admin"],
        channel="api",
        correlation_id="corr-1",
    )
    with pytest.raises(HTTPException):
        await auth_service.authorize_invocation(identity, _make_profile())
