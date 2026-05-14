# Project Vega — Coaction Agent Platform

Configuration-driven, multi-agent platform for Coaction underwriting assistants.  
Built with **Strands Agent SDK**, **Amazon Bedrock**, **Cognito Auth**, and **DynamoDB**.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          Gradio UI (/ui)                            │
│   Signup → Confirm → Login → Chat (streaming) → KB Management      │
└───────────────┬──────────────────────────────────────────────────────┘
                │ HTTP (Bearer JWT)
┌───────────────▼──────────────────────────────────────────────────────┐
│                       FastAPI Backend (:8000)                        │
│                                                                      │
│  /v1/auth/*        → Cognito signup, confirm, login                  │
│  /v1/sessions/*    → DynamoDB session CRUD                           │
│  /v1/agents/{id}/invoke → Agent invocation                           │
│  /v1/knowledge-bases/* → KB lifecycle (create, sync, delete)         │
│  /health, /ping, /ready → Health checks                              │
│  /invocations, /       → AgentCore-compatible invocation paths       │
└───────────────┬──────────────────────────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────┐
│                       AgentService (Orchestrator)                    │
│                                                                      │
│  1. Load ExecutionProfile (DynamoDB or env defaults)                  │
│  2. Initialize/cache UnderwritingAgent                               │
│  3. Load session history from DynamoDB                               │
│  4. Execute Strands Agent (Bedrock model + search_manuals tool)      │
│  5. Extract follow-up questions + citations                          │
│  6. Save session to DynamoDB                                         │
│  7. Return AgentInvocationResponse                                   │
└──────────────────────────────────────────────────────────────────────┘
```

### Key Components

| Layer | Directory | Purpose |
|-------|-----------|---------|
| **Adapters** | `adapters/aws/` | AWS service wrappers: Cognito, DynamoDB, Bedrock KB Manager, Boto3 Factory |
| **Agents** | `agents/` | Underwriting agent logic, prompt templates, retrieval tool |
| **App** | `app/` | FastAPI routers, middleware, dependencies, main entry |
| **Control Plane** | `control_plane/` | Agent registry, execution profiles, KB/memory/deployment managers |
| **Domain** | `domain/` | Pydantic models: requests, responses, profiles, citations |
| **Runtime** | `runtime/` | Base agent, orchestrator, response composer, host adapters |
| **Services** | `services/` | Authorization, guardrails, memory, model gateway, telemetry, audit |
| **UI** | `ui/` | Gradio web interface with auth, chat, KB management |
| **Scripts** | `scripts/` | Bootstrap pipeline, crawlers, pre-push checks |
| **Entrypoints** | `entrypoints/` | AgentCore Runtime MicroVM listener |
| **Profiles** | `profiles/` | JSON execution profile definitions |

---

## Quick Start

### Prerequisites

- Python 3.11+
- AWS credentials with access to Bedrock, Cognito, DynamoDB
- `.env` file (see `.env.example`)

### 1. Install Dependencies

```bash
pip install -r requirements.txt
# For development (linting + testing):
pip install ruff pytest
```

### 2. Configure Environment

Create a `.env` file at project root:

```env
# AWS
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret

# Cognito
COGNITO_USER_POOL_ID=us-east-1_XXXXXXX
COGNITO_APP_CLIENT_ID=your-client-id

# DynamoDB
DYNAMODB_TABLE_NAME=CoactionPlatform

# Bedrock
BEDROCK_KB_ID=your-kb-id
BEDROCK_MODEL_ID=amazon.nova-pro-v1:0

# Optional
BEDROCK_KB_ROLE_ARN=arn:aws:iam::...
EMBEDDING_MODEL_ARN=arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0
RDS_RESOURCE_ARN=...
RDS_CREDENTIALS_SECRET_ARN=...
```

### 3. Run the Platform

```bash
# Unified mode (API + UI on one port)
python main.py
# → API at http://localhost:8000/v1
# → UI at http://localhost:8000/ui
# → Health at http://localhost:8000/health

# Standalone UI (if running API separately)
python ui/gradio_app.py
```

---

## How to Create a New Agent

### Method A: Manual Creation (Step by Step)

This is the approach used when you want full control. You manually:

1. **Write the prompt template** in `agents/prompts.py`:

```python
PROMPT_TEMPLATES["my_new_agent_v1"] = """<role>
You are an expert assistant for XYZ domain...
</role>

<tool_usage_rules>
- Call the search_manuals tool ONCE per query...
</tool_usage_rules>

<response_format>
- Answer first, then citations, then follow-ups
</response_format>
"""
```

2. **Create a Knowledge Base** in AWS Bedrock console:
   - Go to AWS Console → Bedrock → Knowledge Bases → Create
   - Select embedding model, storage (Aurora pgvector or OpenSearch)
   - Add S3 data source with your documents
   - Note the `kb_id` after creation

3. **Create a Memory** (optional, for conversation persistence):
   - Use AgentCore Memory API or configure DynamoDB-backed sessions

4. **Store an Execution Profile** in DynamoDB:

```python
from adapters.aws.dynamodb import DynamoDBAdapter

db = DynamoDBAdapter(table_name="CoactionPlatform", region="us-east-1")
db.save_execution_profile(
    agent_id="my-new-agent",
    version="latest",
    profile_data={
        "agent_id": "my-new-agent",
        "version": "1.0",
        "prompt_template_id": "my_new_agent_v1",
        "model_profile": {
            "model_id": "amazon.nova-pro-v1:0",
            "temperature": 0.0,
            "max_tokens": 4096
        },
        "retrieval_profile": {
            "knowledge_base_ids": ["YOUR_KB_ID"],
            "enabled": True
        },
        "memory_profile": {"enabled": True}
    }
)
```

5. **Invoke your agent**:

```bash
curl -X POST http://localhost:8000/v1/agents/my-new-agent/invoke \
  -H "Authorization: Bearer <your-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{"input_text": "What are the coverage limits?"}'
```

6. **Deploy to AgentCore** (optional via Console):
Instead of CLI, navigate to the AWS console, create a new AgentCore runtime, and point it to your ECR container URI.

### Method B: Automated Pipeline (Single File)

This uses the bootstrap pipeline to create KB + Memory + deploy automatically:

1. **Create a JSON execution profile** at `profiles/my_agent.json`:

```json
{
  "agent_id": "my-new-agent",
  "version": "1.0",
  "prompt_template_id": "my_new_agent_v1",
  "s3_bucket": "my-data-bucket",
  "s3_prefix": "docs/my-agent/",
  "model_profile": {
    "model_id": "amazon.nova-pro-v1:0",
    "temperature": 0.0,
    "max_tokens": 4096
  },
  "retrieval_profile": {
    "knowledge_base_ids": [],
    "enabled": true
  },
  "memory_profile": {
    "enabled": true
  }
}
```

2. **Run the bootstrap pipeline**:

```bash
python scripts/platform_bootstrap.py \
  my-new-agent \
  my-data-bucket \
  arn:aws:iam::123456:role/BedrockKBRole
```

This will automatically:
- Create a Bedrock Knowledge Base
- Add S3 data source and trigger sync
- Create AgentCore Memory (if configured)
- Deploy the agent to the AWS AgentCore Runtime

3. **The agent is now live** — invoke it via the API or Gradio UI.

---

## Pushing Code Updates to Existing Agents

When you modify the codebase (such as updating prompts in `agents/prompts.py` or agent logic), you must push the changes to AWS AgentCore.

### Method A: Automated Deployment Pipeline (Recommended)
1. **Build and push the updated container to ECR**:
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <your-account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build specifically for AWS Graviton (ARM64)
docker buildx build --target runtime --platform linux/arm64 -t <your-account-id>.dkr.ecr.us-east-1.amazonaws.com/vega-platform:latest --push .
```

2. **Trigger the automated AgentCore update**:
```bash
python scripts/platform_bootstrap.py \
  <your_agent_id> \
  <your_bucket_name> \
  arn:aws:iam::<your-account-id>:role/VegaPlatformExecutionRole
```
Because the KB already exists, it skips creation and directly publishes a new AgentCore version, automatically updating your `DEFAULT` endpoint.

### Method B: Manual Deployment via AWS Console
1. Run the same `docker build` and `docker push` commands as Method A to upload the new container to ECR.
2. Go to the **AWS Console** → **Bedrock** → **AgentCore**.
3. Select your Agent Runtime.
4. Click **Create Version** (this pulls the new container).
5. Go to **Endpoints**, select the `DEFAULT` endpoint, click **Edit**, and change the associated version to the newly created version.

---

## Deployment

### Local Development

```bash
python main.py
```

### Docker

```bash
docker build -t project-vega .
docker run -p 8000:8000 --env-file .env project-vega
```

### AgentCore Runtime

```bash
# Configure
agentcore configure

# Deploy
agentcore deploy --config .bedrock_agentcore.yaml
```

### ECS/Fargate

Use the CI/CD pipeline (`.github/workflows/ci.yml`) which:
1. Runs ruff lint + format check
2. Runs pytest
3. Builds Docker image
4. Pushes to ECR
5. Deploys to ECS

---

## Code Quality & CI/CD

### Pre-Push Checks

**Before any commit**, run:

```bash
# Check only (no modifications)
python scripts/pre_push_check.py

# Auto-fix lint errors and format code
python scripts/pre_push_check.py --fix
```

This runs:
1. **Ruff Linter** — catches import errors, unused variables, bare excepts
2. **Ruff Formatter** — ensures consistent code style
3. **Pytest** — runs all unit tests

### Makefile Targets

```bash
make lint          # Run ruff check
make format        # Run ruff format
make test          # Run pytest
make check         # Run all checks (lint + format + test)
make fix           # Auto-fix lint + reformat
```

### GitHub Actions CI

The `.github/workflows/ci.yml` pipeline runs on every push/PR:
- Ruff lint check
- Ruff format conformance
- Pytest suite

---

## Project Structure

```
project-vega/
├── adapters/
│   └── aws/
│       ├── bedrock_kb_manager.py   # KB lifecycle (create/sync/delete)
│       ├── boto3_factory.py        # Centralized AWS client creation
│       ├── cognito.py              # Cognito signup/login/confirm
│       ├── dynamodb.py             # Single-table DynamoDB adapter
│       └── jwt_verifier.py         # Cognito JWT RS256 verification
├── agents/
│   ├── prompts.py                  # System prompt templates
│   ├── underwriting_agent.py       # Strands Agent with Bedrock + KB
│   └── tools/
│       └── retriever.py            # search_manuals tool (Bedrock KB)
├── app/
│   ├── main.py                     # FastAPI app with lifespan wiring
│   ├── core/
│   │   ├── auth.py                 # Local auth fallback
│   │   └── logger.py               # Structured logging
│   ├── db/
│   │   ├── database.py             # SQLAlchemy (local dev)
│   │   └── models.py               # Local DB models
│   ├── dependencies/
│   │   ├── identity.py             # Cognito JWT identity extraction
│   │   ├── services.py             # DI container
│   │   └── settings.py             # Environment settings
│   ├── middleware/
│   │   ├── correlation.py          # Correlation ID middleware
│   │   └── errors.py               # Error handler middleware
│   └── routers/
│       ├── agent_router.py         # /v1/agents/{id}/invoke
│       ├── auth_router.py          # /v1/auth/signup|confirm|login
│       ├── health.py               # /health, /ping, /ready
│       ├── kb_router.py            # /v1/knowledge-bases CRUD
│       ├── session_router.py       # /v1/sessions CRUD
│       ├── feedback.py             # User feedback
│       └── threads.py              # Thread management
├── control_plane/
│   ├── agent_registry.py           # Agent registration
│   ├── deployment_manager.py       # Cloud deployment
│   ├── execution_profile_repository.py  # Profile loading
│   ├── kb_manager.py               # KB provisioning (pipeline)
│   ├── memory_manager.py           # Memory provisioning (pipeline)
│   └── prompt_repository.py        # Prompt loading from config
├── domain/
│   └── models.py                   # All Pydantic models (unified)
├── entrypoints/
│   └── agent_gateway.py            # AgentCore MicroVM entrypoint
├── runtime/
│   ├── base_agent.py               # Abstract base agent
│   ├── context_builder.py          # Context assembly
│   ├── host_adapter.py             # Runtime host abstraction
│   ├── orchestrator.py             # 12-step execution pipeline
│   ├── response_composer.py        # Response envelope builder
│   └── strands_agent.py            # Agent type definitions
├── services/
│   ├── agent_service.py            # Central orchestrator (profile→agent→execute→save)
│   ├── audit.py                    # Metadata-only audit logger
│   ├── authorization.py            # Platform authorization
│   ├── guardrails.py               # Bedrock Guardrails integration
│   ├── memory.py                   # AgentCore Memory provider
│   ├── model_gateway.py            # Strands + Bedrock model gateway
│   ├── retrieval.py                # KB retrieval (legacy/fallback)
│   ├── session_manager.py          # In-memory session fallback
│   ├── telemetry.py                # CloudWatch telemetry emitter
│   └── tool_gateway.py             # Read-only tool gateway
├── ui/
│   └── gradio_app.py               # Gradio UI (auth + chat + KB mgmt)
├── scripts/
│   ├── pre_push_check.py           # CI verification (ruff + pytest)
│   ├── platform_bootstrap.py       # Automated agent pipeline
│   ├── query.py                    # Testing tool
│   └── crawlers/                   # Web crawlers for data ingestion
├── profiles/
│   └── vega_binding_authority_bot.json  # Default agent profile
├── tests/
│   └── unit/                       # Unit tests
├── .github/workflows/ci.yml        # GitHub Actions CI
├── Dockerfile                       # Production container
├── Makefile                         # Build targets
├── main.py                         # Root entry point
├── pyproject.toml                   # Project config
└── README.md                       # This file
```

---

## API Reference

### Authentication

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/auth/signup` | POST | Register new user (Cognito) |
| `/v1/auth/confirm` | POST | Confirm email with code |
| `/v1/auth/login` | POST | Login, get JWT tokens |

### Agent Invocation

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/agents/{agent_id}/invoke` | POST | Invoke an agent |
| `/v1/agents/{agent_id}/health` | GET | Check agent health |

### Sessions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/sessions` | GET | List user's chat sessions |
| `/v1/sessions/{id}` | GET | Get session with messages |
| `/v1/sessions/{id}` | DELETE | Delete a session |

### Knowledge Bases

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/knowledge-bases` | POST | Create new KB (underwriter only) |
| `/v1/knowledge-bases` | GET | List all KBs |
| `/v1/knowledge-bases/{id}` | GET | Get KB details |
| `/v1/knowledge-bases/{id}/sync` | POST | Trigger re-sync |
| `/v1/knowledge-bases/{id}` | DELETE | Delete KB |

---

## License

Proprietary — Coaction Specialty Insurance.
