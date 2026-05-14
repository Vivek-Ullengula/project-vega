# Project Vega — Codebase Detailed Guide

This guide explains every module in the codebase, how they connect, and the data flow for agent invocations. Use this as a reference when modifying or extending the platform.

---

## Directory Structure

```
project-vega/
├── adapters/aws/        # AWS service wrappers
├── agents/              # Agent logic, prompts, tools
├── app/                 # FastAPI application layer
├── control_plane/       # Agent registry & deployment
├── domain/              # Pydantic domain models
├── entrypoints/         # AgentCore MicroVM listener
├── runtime/             # Orchestration pipeline
├── services/            # Business logic services
├── ui/                  # Gradio web interface
├── scripts/             # CLI tools & automation
├── profiles/            # JSON execution profiles
├── config/              # YAML execution profiles
├── tests/               # Unit tests
└── .github/workflows/   # CI/CD pipeline
```

---

## Layer-by-Layer Breakdown

### 1. `domain/models.py` — Unified Domain Models

Single file containing ALL Pydantic models used across the platform:

| Model | Purpose |
|-------|---------|
| `AgentInvocationRequest` | Input from client (agent_id, input_text, session_id) |
| `AgentInvocationResponse` | Output with status, answer, citations, tool_results, metadata |
| `IdentityContext` | User identity (user_id, roles, channel, correlation_id, JWT claims) |
| `SourceCitation` | Reference to a source document (source_id, title, uri, manual_name) |
| `ToolResult` | Result from a tool call (tool_id, status, result_summary) |
| `ExecutionProfile` | Complete agent configuration (model, retrieval, memory, guardrails) |
| `ModelProfile` | Bedrock model settings (model_id, temperature, max_tokens) |
| `RetrievalProfile` | KB retrieval config (knowledge_base_ids, reranking, citations) |
| `MemoryProfile` | Session memory config (scope, retention_days, read/write flags) |
| `ToolPermission` | Per-tool role-based access control |
| `GuardrailProfile` | Bedrock Guardrails config (guardrail_id, input/output checks) |
| `ObservabilityProfile` | CloudWatch telemetry config |

### 2. `adapters/aws/` — AWS Service Wrappers

| File | Class | What It Does |
|------|-------|-------------|
| `boto3_factory.py` | `Boto3SessionFactory` | Centralized AWS client creation with region config |
| `cognito.py` | `CognitoAdapter` | Cognito sign_up, confirm_sign_up, sign_in, get_user, admin_set_role |
| `jwt_verifier.py` | `CognitoJWTVerifier` | RS256 JWT verification using Cognito JWKS endpoint |
| `dynamodb.py` | `DynamoDBAdapter` | Single-table DynamoDB: users, sessions, KB metadata, execution profiles |
| `bedrock_kb_manager.py` | `BedrockKBManager` | Full KB lifecycle: create, add S3 source, sync, status, delete |

**DynamoDB Key Schema:**
```
PK                    SK                   EntityType
────────────────────  ──────────────────   ──────────────
USER#<sub>            PROFILE              User
USER#<sub>            SESSION#<sid>        ChatSession
KB#<kb_id>            META                 KnowledgeBase
PROFILE#<agent_id>    VERSION#<ver>        ExecutionProfile
```

### 3. `agents/` — Agent Logic & Prompts

| File | What It Does |
|------|-------------|
| `prompts.py` | `PROMPT_TEMPLATES` dict keyed by prompt_template_id. Contains the full structured XML prompt for the underwriting agent. `get_prompt(template_id, role)` returns the right prompt. |
| `underwriting_agent.py` | `UnderwritingAgent` class: builds a Strands Agent from an ExecutionProfile, handles invocation, extracts follow-up questions, deduplicates them against history, and returns structured response with citations. |
| `tools/retriever.py` | `search_manuals` Strands tool: calls Bedrock KB Retrieve API, extracts text chunks + metadata (source URL, heading, manual name). Thread-local storage for last retrieval sources. |

**Key function: `UnderwritingAgent.invoke()`**
```
query → build/cache Strands Agent → restore history → execute → 
  parse answer → extract follow-ups → deduplicate → get citations → 
  return {answer, citations, follow_up_questions, sources, agent_messages}
```

### 4. `services/` — Business Logic

| File | Class | Purpose |
|------|-------|---------|
| `agent_service.py` | `AgentService` | **Central orchestrator**: loads profile → builds agent → loads session history → invokes → saves session → returns response |
| `authorization.py` | `AuthorizationService` | Checks identity has user_id, validates tool-level role restrictions |
| `guardrails.py` | `GuardrailService` | Calls Bedrock Guardrails API for input/output content filtering |
| `memory.py` | `AgentCoreMemoryProvider` | Reads/writes DynamoDB sessions as memory context |
| `model_gateway.py` | `BedrockModelGateway` | Builds Strands Agents from ExecutionProfiles, invokes, returns structured results |
| `telemetry.py` | `CloudWatchTelemetryEmitter` | Emits invocation metrics to CloudWatch |
| `audit.py` | `MetadataOnlyAuditLogger` | Structured audit logging (no raw prompts/responses) |
| `tool_gateway.py` | `AgentCoreReadOnlyToolGateway` | Validates tool permissions; blocks non-read actions |

**AgentService.invoke() flow:**
```python
async def invoke(request, identity):
    profile = load_or_default_profile(request.agent_id)
    agent = get_or_create_agent(profile)
    session = dynamodb.get_session(identity.user_id, request.session_id)
    history = session.messages if session else []
    result = await agent.invoke(query, role, history)
    dynamodb.save_session(user_id, session_id, title, messages)
    return AgentInvocationResponse(...)
```

### 5. `runtime/` — Orchestration Pipeline

| File | Class | Purpose |
|------|-------|---------|
| `orchestrator.py` | `RuntimeOrchestrator` | 12-step pipeline: authorize → guardrails → memory → model → tools → compose → audit |
| `base_agent.py` | `BaseAgent` | Abstract base class, delegates to orchestrator |
| `strands_agent.py` | `StrandsBaseAgent`, `RetrievalAgent`, `ReadOnlyToolAgent` | Agent type definitions |
| `response_composer.py` | `ResponseComposer` | Assembles final `AgentInvocationResponse` from model results |
| `host_adapter.py` | `RuntimeHostAdapter` | Abstracts hosting: `LocalFastApiRuntimeHost` vs `AgentCoreRuntimeHost` |
| `context_builder.py` | (various) | Builds execution context from request + profile |

### 6. `app/` — FastAPI Application

#### Routers

| File | Prefix | Endpoints |
|------|--------|-----------|
| `auth_router.py` | `/v1/auth` | `POST /signup`, `POST /confirm`, `POST /login` |
| `agent_router.py` | `/v1/agents` | `POST /{agent_id}/invoke`, `GET /{agent_id}/health` |
| `session_router.py` | `/v1/sessions` | `GET /`, `GET /{id}`, `DELETE /{id}` |
| `kb_router.py` | `/v1/knowledge-bases` | `POST /`, `GET /`, `GET /{id}`, `POST /{id}/sync`, `DELETE /{id}` |
| `health.py` | `/` | `GET /health`, `GET /ping`, `GET /ready` |

#### Dependencies

| File | Purpose |
|------|---------|
| `identity.py` | Extracts `IdentityContext` from JWT `Authorization` header via `CognitoJWTVerifier` |
| `services.py` | DI container for `get_orchestrator()`, `get_agent_service()`, `get_dynamodb_adapter()` |
| `settings.py` | Pydantic Settings class loading from `.env` |

#### Middleware

| File | Purpose |
|------|---------|
| `correlation.py` | Injects `X-Correlation-ID` header on every request/response |
| `errors.py` | Global error handler returning structured JSON errors |

#### `app/main.py` — Application Entry Point

Uses FastAPI `lifespan` for service initialization:
1. Load environment config
2. Create `Boto3SessionFactory`
3. Initialize `CognitoAdapter` + JWT verifier
4. Create `DynamoDBAdapter`
5. Initialize all services (authorization, guardrails, memory, model gateway, etc.)
6. Create `RuntimeOrchestrator`
7. Create `AgentService`
8. Create `BedrockKBManager`
9. Wire all routers (`init_auth_router`, `init_session_router`, etc.)
10. Mount Gradio UI at `/ui`

### 7. `ui/gradio_app.py` — Gradio Web Interface

Features:
- **Auth flow**: Signup → email verification → Login (all via Cognito APIs)
- **Chat**: Streaming conversation with follow-up question chips
- **Session management**: Load/create/clear chat sessions via sidebar dropdown
- **KB management**: Create Knowledge Bases (underwriter role only)
- **Glassmorphism UI**: Premium design with gradient message bubbles

### 8. `control_plane/` — Agent Registry & Deployment

| File | Purpose |
|------|---------|
| `agent_registry.py` | Manages agent registrations (backed by DynamoDB) |
| `execution_profile_repository.py` | Loads ExecutionProfiles from DynamoDB or local JSON |
| `kb_manager.py` | KB provisioning for the bootstrap pipeline |
| `memory_manager.py` | Memory provisioning for the bootstrap pipeline |
| `deployment_manager.py` | Cloud deployment orchestration |

### 9. `entrypoints/agent_gateway.py` — AgentCore MicroVM

Lean event listener for native Bedrock AgentCore Runtime. Receives payloads, creates `AgentInvocationRequest` + `IdentityContext`, runs through the orchestrator, and returns response.

### 10. `scripts/` — CLI & Automation

| File | Purpose |
|------|---------|
| `pre_push_check.py` | Pre-commit CI: ruff lint + format + pytest |
| `platform_bootstrap.py` | Automated agent deployment pipeline |
| `query.py` | CLI tool: invoke agent directly without server |
| `split_manual.py` | Split large markdown files into sections for KB ingestion |
| `crawlers/` | Web crawlers for data ingestion into S3 |

---

## Data Flow: Agent Invocation

```
User clicks "Send" in Gradio UI
  │
  ▼
POST /v1/agents/coaction-underwriting/invoke
  │  Body: { input_text, session_id, top_k }
  │  Header: Authorization: Bearer <Cognito JWT>
  │
  ▼
agent_router.py → identity.py extracts IdentityContext from JWT
  │
  ▼
AgentService.invoke(request, identity)
  │
  ├── 1. Load ExecutionProfile (DynamoDB → env defaults)
  ├── 2. Get/create UnderwritingAgent (cached by profile+role)
  ├── 3. Load session history from DynamoDB
  ├── 4. agent.invoke(query, role, history)
  │       │
  │       ├── Build Strands Agent (BedrockModel + system prompt + search_manuals tool)
  │       ├── Restore conversation history
  │       ├── Execute: agent(query)
  │       │     └── Strands calls search_manuals tool → Bedrock KB Retrieve API
  │       ├── Parse answer, extract follow-ups, deduplicate
  │       └── Return {answer, citations, follow_up_questions, sources}
  │
  ├── 5. Build AgentInvocationResponse
  ├── 6. Save session to DynamoDB (messages + title)
  └── 7. Return response to UI
```

---

## Adding a New Feature: Checklist

1. **New domain model?** → Add to `domain/models.py`
2. **New AWS adapter?** → Add to `adapters/aws/`
3. **New API endpoint?** → Create router in `app/routers/`, import in `app/main.py`
4. **New agent type?** → Add prompt to `agents/prompts.py`, add agent class if needed
5. **New service?** → Add to `services/`, wire in `app/main.py` lifespan
6. **Always** → Run `python scripts/pre_push_check.py --fix` before pushing

---

**Maintained by**: Coaction Agent Platform Engineering Team  
👉 **[README](./README.md)** · **[Runbook](./agent_platform_runbook.md)**
