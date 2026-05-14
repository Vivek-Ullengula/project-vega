# PROJECT VEGA — ARCHITECTURE KNOWLEDGE BASE
# Coaction Specialty Agentic AI Platform on AWS

---

## Project Overview
AWS-first, governed, headless agentic platform. Control plane separated from runtime plane.
- Language: **Python**
- API layer: **FastAPI**
- Orchestration: **Strands Agents SDK** (`strands` package)
- Model provider: **Amazon Bedrock** via Strands `BedrockModel` — fully AWS-native
- Authentication: **AWS Cognito** (JWT-based)
- Session/data persistence: **DynamoDB** (single-table design)
- Knowledge retrieval: **Bedrock Knowledge Bases** via `search_manuals` tool
- Deployment targets: **ECS/Fargate** (primary), **Bedrock AgentCore Runtime** (MicroVM)
- UI: **Gradio** (mounted at `/ui` on the FastAPI server)

---

## SDK & Library Boundaries — What Uses What

### Via Strands SDK (`strands` package) — direct integration
| Capability | How |
|---|---|
| Agent creation & orchestration | `from strands import Agent` |
| Model invocation (Bedrock) | `from strands.models.bedrock import BedrockModel` |
| Tool specification | Explicit list — `Agent(tools=[search_manuals])` |
| Conversation context | Via `agent.state.messages` — restored from DynamoDB session |

### BedrockModel — model invocation pattern
```python
from strands import Agent
from strands.models.bedrock import BedrockModel

model = BedrockModel(
    model_id="amazon.nova-pro-v1:0",   # from execution profile
    region_name="us-east-1",
    temperature=0.0,
    max_tokens=4096,
)
agent = Agent(model=model, system_prompt=prompt, tools=[search_manuals])
```

### Via `adapters/aws/` — platform-level AWS wrappers
| Capability | Adapter |
|---|---|
| User auth (signup/login/confirm) | `CognitoAdapter` (`adapters/aws/cognito.py`) |
| JWT verification | `CognitoJWTVerifier` (`adapters/aws/jwt_verifier.py`) |
| Session/user/KB data | `DynamoDBAdapter` (`adapters/aws/dynamodb.py`) |
| KB lifecycle management | `BedrockKBManager` (`adapters/aws/bedrock_kb_manager.py`) |
| Centralized AWS clients | `Boto3SessionFactory` (`adapters/aws/boto3_factory.py`) |

### Via raw Boto3 — no Strands-native abstraction
| Capability | Boto3 client |
|---|---|
| Bedrock KB Retrieve | `boto3.client("bedrock-agent-runtime")` — used in `agents/tools/retriever.py` |
| AgentCore Gateway | `boto3.client("bedrock-agentcore")` |
| CloudWatch (metrics, logs) | `boto3.client("cloudwatch")` / `logs` |

---

## Data Architecture — DynamoDB Single Table

All platform data lives in a single DynamoDB table using composite primary keys:

```
Table: CoactionPlatform

PK                         SK                    EntityType          Data
─────────────────────────  ───────────────────   ─────────────────   ──────────────
USER#<cognito_sub>         PROFILE               User                email, name, role
USER#<cognito_sub>         SESSION#<session_id>   ChatSession         messages[], title, TTL
KB#<kb_id>                 META                   KnowledgeBase       name, s3_bucket, status
PROFILE#<agent_id>         VERSION#<version>      ExecutionProfile    model, retrieval, memory config
```

### Why single table?
- Simpler operations: one table to provision, backup, and monitor
- Efficient queries: all user data (sessions + profile) on same partition key
- Cost effective: no separate tables for each entity type
- Aligns with DynamoDB best practices for access patterns

---

## Platform Architecture Layers

| Layer | Technology | Module |
|---|---|---|
| Channel & Access | FastAPI (REST), Gradio (UI) | `app/`, `ui/` |
| Identity & Auth | AWS Cognito + JWT | `adapters/aws/cognito.py`, `jwt_verifier.py` |
| Control Plane | DynamoDB-backed registry | `control_plane/` |
| Runtime Plane | Strands `Agent` + `BedrockModel` | `runtime/`, `agents/` |
| Session Storage | DynamoDB single-table | `adapters/aws/dynamodb.py` |
| Model Services | Bedrock Runtime via Strands | `services/model_gateway.py` |
| Knowledge/RAG | Bedrock Knowledge Bases | `agents/tools/retriever.py` |
| Guardrails | Bedrock Guardrails API | `services/guardrails.py` |
| Tool Gateway | Read-only tool validation | `services/tool_gateway.py` |
| Observability | CloudWatch | `services/telemetry.py` |
| Audit | Structured metadata logger | `services/audit.py` |

---

## Core Design Principle
Every agent is **configuration-driven**. The runtime loads an approved ExecutionProfile from DynamoDB. Individual agent code must NOT hardcode: model IDs, guardrail IDs, knowledge base IDs, tool permissions, or prompt behavior.

---

## Core Domain Models (Pydantic) — `domain/models.py`

All models are in a single unified file:

```python
class AgentInvocationRequest(BaseModel):
    agent_id: str
    input_text: str
    session_id: str | None = None
    channel: str = "api"
    request_metadata: dict[str, Any] = Field(default_factory=dict)

class IdentityContext(BaseModel):
    user_id: str
    roles: list[str] = Field(default_factory=list)
    channel: str
    application_id: str | None = None
    session_id: str | None = None
    correlation_id: str
    claims: dict[str, Any] = Field(default_factory=dict)

class SourceCitation(BaseModel):
    source_id: str
    title: str | None = None
    uri: str | None = None
    manual_name: str | None = None
    chunk_id: str | None = None
    score: float | None = None

class ToolResult(BaseModel):
    tool_id: str
    action_class: Literal["read"]
    status: Literal["success", "failed", "blocked"]
    result_summary: str | None = None
    error_code: str | None = None

class AgentInvocationResponse(BaseModel):
    status: Literal["success", "clarification_required", "blocked", "escalated", "error"]
    answer: str
    citations: list[SourceCitation] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    session_id: str = ""
    correlation_id: str = ""
    model_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
```

## Execution Profile Models (also in `domain/models.py`)

```python
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

class GuardrailProfile(BaseModel):
    guardrail_id: str | None = None
    guardrail_version: str | None = None
    input_check_enabled: bool = True
    output_check_enabled: bool = True

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
```

---

## Python Package Structure (Current)
```
project-vega/
├── adapters/aws/              # AWS service wrappers
│   ├── bedrock_kb_manager.py  # KB lifecycle (create/sync/delete)
│   ├── boto3_factory.py       # Centralized AWS client creation
│   ├── cognito.py             # Cognito signup/login/confirm
│   ├── dynamodb.py            # Single-table DynamoDB adapter
│   └── jwt_verifier.py        # Cognito JWT RS256 verification
├── agents/                    # Agent logic
│   ├── prompts.py             # System prompt templates (PROMPT_TEMPLATES dict)
│   ├── underwriting_agent.py  # Strands Agent with Bedrock + KB retrieval
│   └── tools/
│       └── retriever.py       # search_manuals Strands tool (Bedrock KB)
├── app/                       # FastAPI application
│   ├── main.py                # Entry point with lifespan wiring
│   ├── core/logger.py         # Structured logging
│   ├── dependencies/
│   │   ├── identity.py        # Cognito JWT identity extraction
│   │   ├── services.py        # DI container
│   │   └── settings.py        # Environment settings
│   ├── middleware/
│   │   ├── correlation.py     # X-Correlation-ID middleware
│   │   └── errors.py          # Global error handler
│   └── routers/
│       ├── agent_router.py    # POST /v1/agents/{id}/invoke
│       ├── auth_router.py     # POST /v1/auth/signup|confirm|login
│       ├── health.py          # GET /health, /ping, /ready
│       ├── kb_router.py       # CRUD /v1/knowledge-bases
│       └── session_router.py  # CRUD /v1/sessions
├── control_plane/             # Agent registry & deployment
│   ├── agent_registry.py      # Agent registrations (DynamoDB)
│   ├── execution_profile_repository.py  # Profile loading
│   ├── kb_manager.py          # KB provisioning (pipeline)
│   ├── memory_manager.py      # Memory provisioning (pipeline)
│   └── deployment_manager.py  # Cloud deployment
├── domain/
│   └── models.py              # ALL Pydantic models (unified)
├── entrypoints/
│   └── agent_gateway.py       # AgentCore MicroVM entrypoint
├── runtime/                   # Orchestration pipeline
│   ├── base_agent.py          # Abstract base agent
│   ├── context_builder.py     # Context assembly
│   ├── host_adapter.py        # Local vs AgentCore hosting
│   ├── orchestrator.py        # 12-step execution pipeline
│   ├── response_composer.py   # Response envelope builder
│   └── strands_agent.py       # Agent type definitions
├── services/                  # Business logic
│   ├── agent_service.py       # Central orchestrator
│   ├── audit.py               # Metadata-only audit logger
│   ├── authorization.py       # Identity + role authorization
│   ├── guardrails.py          # Bedrock Guardrails
│   ├── memory.py              # DynamoDB-backed memory provider
│   ├── model_gateway.py       # Strands + Bedrock model gateway
│   ├── telemetry.py           # CloudWatch telemetry
│   └── tool_gateway.py        # Read-only tool gateway
├── ui/
│   └── gradio_app.py          # Gradio UI (auth + chat + KB mgmt)
├── scripts/                   # CLI & automation
│   ├── pre_push_check.py      # CI: ruff + format + pytest
│   ├── platform_bootstrap.py  # Automated agent pipeline
│   ├── query.py               # CLI tool
│   ├── split_manual.py        # Document splitter for KB
│   └── crawlers/              # Web crawlers
├── profiles/                  # JSON execution profiles
├── config/execution_profiles/ # YAML execution profiles
├── tests/unit/                # Unit tests
└── .github/workflows/ci.yml   # GitHub Actions CI
```

---

## Runtime Orchestration Sequence (must follow this exact order)
```
1.  Load ExecutionProfile from DynamoDB (or env defaults)
2.  authorization.authorize_invocation(identity, profile)
3.  guardrails.check_input(request, profile)           ← Bedrock Guardrails
4.  memory.read(request, identity, profile)             ← DynamoDB session history
5.  retriever.retrieve(request, identity, profile)      ← search_manuals tool
6.  model_gateway.invoke(...)                           ← Strands Agent + BedrockModel
7.  tool_gateway.validate_tools(...)                    ← read-only enforcement
8.  response_composer.compose(...)                      ← build AgentInvocationResponse
9.  guardrails.check_output(response, profile)          ← output guardrail
10. memory.write(request, response, identity, profile)  ← save session to DynamoDB
11. telemetry.emit_invocation(...)                      ← CloudWatch metrics
12. audit.record_invocation(...)                        ← metadata only
13. return response
```

---

## AgentService.invoke() — Central Flow

The `services/agent_service.py` is the main orchestrator used by routers:

```python
async def invoke(request, identity):
    # 1. Load or create ExecutionProfile
    profile = _load_or_default_profile(request.agent_id)
    
    # 2. Get or create UnderwritingAgent (cached)
    agent = _get_or_create_agent(profile)
    
    # 3. Load session history from DynamoDB
    session = dynamodb.get_session(identity.user_id, session_id)
    history = session["messages"] if session else []
    
    # 4. Invoke agent
    result = await agent.invoke(query, role, history)
    
    # 5. Build response
    response = AgentInvocationResponse(
        status="success",
        answer=result["answer"],
        citations=result["citations"],
        session_id=session_id,
        ...
    )
    
    # 6. Save session to DynamoDB
    dynamodb.save_session(user_id, session_id, title, messages)
    
    return response
```

---

## FastAPI Endpoints (Current)
```
Auth:
  POST   /v1/auth/signup              — Cognito user registration
  POST   /v1/auth/confirm             — Email verification
  POST   /v1/auth/login               — JWT token login

Agent:
  POST   /v1/agents/{agent_id}/invoke — Agent invocation
  GET    /v1/agents/{agent_id}/health — Agent health check

Sessions:
  GET    /v1/sessions                 — List user sessions (DynamoDB)
  GET    /v1/sessions/{id}            — Get session with messages
  DELETE /v1/sessions/{id}            — Delete session

Knowledge Bases:
  POST   /v1/knowledge-bases          — Create KB (underwriter only)
  GET    /v1/knowledge-bases          — List all KBs
  GET    /v1/knowledge-bases/{id}     — Get KB details
  POST   /v1/knowledge-bases/{id}/sync — Trigger re-sync
  DELETE /v1/knowledge-bases/{id}     — Delete KB

Health:
  GET    /health                      — Health check
  GET    /ping                        — Liveness (AgentCore required)
  GET    /ready                       — Dependency readiness

AgentCore:
  POST   /invocations                 — Standard AgentCore path
  POST   /                            — Root AgentCore path
```

---

## Identity & Authentication

Authentication uses AWS Cognito. JWT tokens are verified using the Cognito JWKS endpoint.

```python
# app/dependencies/identity.py
async def get_identity_context(
    authorization: str = Header(None)
) -> IdentityContext:
    token = authorization.replace("Bearer ", "")
    claims = jwt_verifier.verify_token(token, token_use="access")
    return IdentityContext(
        user_id=claims["sub"],
        roles=claims.get("custom:role", "agent").split(","),
        channel="api",
        correlation_id=request.state.correlation_id,
        claims=claims,
    )
```

---

## Deployment — Multiple Targets

### ECS/Fargate (Primary)
```bash
docker build -t project-vega .
docker run -p 8000:8000 --env-file .env project-vega
```

### Bedrock AgentCore Runtime
- Platform: `linux/arm64`
- Required endpoints: `POST /invocations` + `GET /ping`
- Port: `8080`
- Entrypoint: `entrypoints/agent_gateway.py`

### CI/CD Pipeline
- `.github/workflows/ci.yml`: lint + format + test on every push
- `scripts/pre_push_check.py`: local verification before push
- `Makefile`: `make check`, `make fix`, `make test`

---

## Confirmed Architecture Decisions

| Area | Decision |
|---|---|
| Authentication | AWS Cognito with JWT verification |
| Data persistence | DynamoDB single-table design |
| Model provider | Amazon Bedrock via Strands `BedrockModel` |
| Knowledge/RAG | Bedrock Knowledge Bases via `search_manuals` tool |
| Session storage | DynamoDB (`USER#<sub>` + `SESSION#<sid>`) |
| Agent prompts | `agents/prompts.py` — `PROMPT_TEMPLATES` dict |
| Domain models | Unified `domain/models.py` — single file |
| Tool scope (first release) | Read-only only |
| Payload logging | Never log raw prompt or response |
| Boto3 client creation | Only through `Boto3SessionFactory` |
| Deployment target | ECS/Fargate (primary), AgentCore Runtime (secondary) |
| CI/CD | Ruff linter + Ruff formatter + Pytest |
| UI | Gradio mounted at `/ui` on FastAPI server |

---

**Maintained by**: Coaction Agent Platform Engineering Team
