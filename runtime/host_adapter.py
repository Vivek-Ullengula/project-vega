# coaction_agent_platform/runtime/host_adapter.py
"""Runtime host abstraction per HLD Section 4.

Isolates hosting decision so the platform framework works across
ECS/Fargate, AgentCore Runtime, Lambda, or hybrid patterns.
"""

from abc import ABC, abstractmethod
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.orchestrator import RuntimeOrchestrator
    from adapters.aws.boto3_factory import Boto3SessionFactory


class RuntimeHostAdapter(ABC):
    """Abstract hosting adapter — implementations handle invocation routing."""

    @abstractmethod
    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Invoke the runtime host."""
        raise NotImplementedError


class LocalFastApiRuntimeHost(RuntimeHostAdapter):
    """Runs orchestration locally inside the FastAPI service."""

    def __init__(self, orchestrator: "RuntimeOrchestrator") -> None:
        self.orchestrator = orchestrator

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        from domain.models import (
            AgentInvocationRequest,
            IdentityContext,
        )

        request = AgentInvocationRequest(**payload.get("request", {}))
        identity = IdentityContext(**payload.get("identity", {}))
        response = await self.orchestrator.execute(request, identity)
        return response.model_dump()


class AgentCoreRuntimeHost(RuntimeHostAdapter):
    """Invokes Amazon Bedrock AgentCore Runtime when selected."""

    def __init__(self, boto3_factory: "Boto3SessionFactory") -> None:
        self.client = boto3_factory.client("bedrock-agentcore")

    async def invoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Delegates to AgentCore Runtime API.
        # Implementation depends on final hosting decision.
        raise NotImplementedError("AgentCore Runtime hosting is not yet configured.")
