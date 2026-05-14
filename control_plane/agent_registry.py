# coaction_agent_platform/control_plane/agent_registry.py
"""Agent registry per HLD Section 5.

Tracks registered agents, their active versions, and configuration state.
"""

import structlog

logger = structlog.get_logger(__name__)


class AgentRegistryEntry:
    """An entry in the agent registry."""

    def __init__(self, agent_id: str, active_version: str, status: str = "active"):
        self.agent_id = agent_id
        self.active_version = active_version
        self.status = status


class AgentRegistryRepository:
    """Registry of deployed agents.

    In the first release, the registry is backed by DynamoDB.
    Each agent has an active version that maps to an ExecutionProfile.
    """

    def __init__(self, dynamodb_adapter=None):
        self.dynamodb = dynamodb_adapter
        self._registry: dict[str, AgentRegistryEntry] = {}

    def register(self, agent_id: str, version: str) -> None:
        """Register or update an agent in the registry."""
        self._registry[agent_id] = AgentRegistryEntry(
            agent_id=agent_id,
            active_version=version,
        )
        logger.info("agent_registered", agent_id=agent_id, version=version)

    async def get_active_agent(self, agent_id: str) -> AgentRegistryEntry:
        """Get the active agent entry."""
        if agent_id in self._registry:
            return self._registry[agent_id]

        # Default: assume version "latest"
        entry = AgentRegistryEntry(agent_id=agent_id, active_version="latest")
        self._registry[agent_id] = entry
        return entry

    def list_agents(self) -> list[AgentRegistryEntry]:
        """List all registered agents."""
        return list(self._registry.values())
