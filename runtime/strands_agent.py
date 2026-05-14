# coaction_agent_platform/runtime/strands_agent.py
"""Strands-based agent implementations per HLD Sections 8 and 14.

StrandsBaseAgent provides the Strands-specific orchestration hooks.
RetrievalAgent and ReadOnlyToolAgent are the two reusable agent templates.
"""

from runtime.base_agent import BaseAgent


class StrandsBaseAgent(BaseAgent):
    """Base for all Strands-framework agents."""

    def agent_type(self) -> str:
        return "strands_agent"

    async def build_strands_context(self, runtime_context: dict) -> dict:
        """Hook for subclasses to augment the Strands execution context."""
        return runtime_context


class RetrievalAgent(StrandsBaseAgent):
    """Bedrock KB retrieval enabled, citations enabled, AgentCore Memory enabled,
    tools disabled or optional read-only."""

    def agent_type(self) -> str:
        return "retrieval_agent"


class ReadOnlyToolAgent(StrandsBaseAgent):
    """Bedrock KB retrieval enabled, AgentCore Memory enabled,
    read-only AgentCore Gateway tools enabled, workflow/write/external actions blocked."""

    def agent_type(self) -> str:
        return "readonly_tool_agent"
