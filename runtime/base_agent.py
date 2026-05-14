# coaction_agent_platform/runtime/base_agent.py
"""Base agent classes per HLD Section 8.

All agents extend BaseAgent. The base agent delegates execution
to the RuntimeOrchestrator, which enforces the standard sequence.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.orchestrator import RuntimeOrchestrator

from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
)


class BaseAgent(ABC):
    """Abstract base for all Coaction agents."""

    def __init__(self, agent_id: str, orchestrator: "RuntimeOrchestrator") -> None:
        self.agent_id = agent_id
        self.orchestrator = orchestrator

    async def invoke(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
    ) -> AgentInvocationResponse:
        """Invoke the agent via the standard orchestration pipeline."""
        return await self.orchestrator.execute(request, identity)

    @abstractmethod
    def agent_type(self) -> str:
        """Return the agent type identifier."""
        raise NotImplementedError
