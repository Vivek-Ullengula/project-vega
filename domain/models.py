# domain/models.py
"""Unified domain models for the Coaction Agent Platform.

Contains all API models, execution profile models, and identity models
in a single consolidated module.
"""

from pydantic import BaseModel, Field
from typing import Any, Literal

# ---------- API Models ----------


class AgentInvocationRequest(BaseModel):
    """Request from client to invoke an agent."""

    agent_id: str
    input_text: str
    session_id: str | None = None
    channel: str = "api"
    request_metadata: dict[str, Any] = Field(default_factory=dict)


class IdentityContext(BaseModel):
    """User identity extracted from API Gateway headers."""

    user_id: str
    roles: list[str] = Field(default_factory=list)
    channel: str
    application_id: str | None = None
    session_id: str | None = None
    correlation_id: str
    claims: dict[str, Any] = Field(default_factory=dict)


class SourceCitation(BaseModel):
    """A reference to a source document from retrieval."""

    source_id: str
    title: str | None = None
    uri: str | None = None
    manual_name: str | None = None
    chunk_id: str | None = None
    score: float | None = None


class ToolResult(BaseModel):
    """Result of invoking a tool."""

    tool_id: str
    action_class: Literal["read"]
    status: Literal["success", "failed", "blocked"]
    result_summary: str | None = None
    error_code: str | None = None


class AgentInvocationResponse(BaseModel):
    """Final response from the agent runtime."""

    status: Literal["success", "clarification_required", "blocked", "escalated", "error"]
    answer: str
    citations: list[SourceCitation] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    session_id: str = ""
    correlation_id: str = ""
    model_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------- Execution Profile Models ----------


class ModelProfile(BaseModel):
    provider: Literal["bedrock"] = "bedrock"
    model_id: str
    temperature: float = 0.0
    max_tokens: int | None = None
    fallback_model_id: str | None = None


class RetrievalProfile(BaseModel):
    provider: Literal["bedrock_knowledge_base"] = "bedrock_knowledge_base"
    enabled: bool = True
    knowledge_base_ids: list[str] = Field(default_factory=list)
    metadata_filters: dict[str, Any] = Field(default_factory=dict)
    reranking_enabled: bool = True
    min_confidence: float | None = None
    citations_required: bool = True


class MemoryProfile(BaseModel):
    provider: Literal["agentcore_memory"] = "agentcore_memory"
    enabled: bool = True
    persistent: bool = True
    memory_scope: Literal["agent_user", "agent_session", "agent_task"] = "agent_user"
    retention_days: int = 90
    read_enabled: bool = True
    write_enabled: bool = True


class ToolPermission(BaseModel):
    tool_id: str
    action_class: Literal["read"] = "read"
    allowed_roles: list[str] = Field(default_factory=list)
    requires_approval: bool = False


class GuardrailProfile(BaseModel):
    guardrail_id: str | None = None
    guardrail_version: str | None = None
    input_check_enabled: bool = True
    output_check_enabled: bool = True


class ObservabilityProfile(BaseModel):
    provider: Literal["cloudwatch"] = "cloudwatch"
    emit_metrics: bool = True
    emit_traces: bool = True
    log_raw_prompt: bool = False
    log_raw_response: bool = False


class ExecutionProfile(BaseModel):
    agent_id: str
    version: str
    orchestration_framework: Literal["strands"] = "strands"
    prompt_template_id: str
    model_profile: ModelProfile
    retrieval_profile: RetrievalProfile
    memory_profile: MemoryProfile = MemoryProfile()
    tool_permissions: list[ToolPermission] = Field(default_factory=list)
    guardrail_profile: GuardrailProfile = GuardrailProfile()
    observability_profile: ObservabilityProfile = ObservabilityProfile()
    response_contract_version: str = "v1"
