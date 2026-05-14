# Coaction Agent Platform — Operations Runbook

This is the operational manual for deploying, running, and scaling multi-agent architectures on **Project Vega**. It covers ground-up AWS provisioning, the automated bootstrap pipeline, manual agent creation, verification procedures, and troubleshooting.

---

## 📑 Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Phase 1: AWS Environment Provisioning](#2-phase-1-aws-environment-provisioning)
3. [Phase 2: Application Setup & Local Development](#3-phase-2-application-setup--local-development)
4. [Phase 3: Agent Creation & Deployment](#4-phase-3-agent-creation--deployment)
5. [Phase 4: CI/CD & Pre-Push Checks](#5-phase-4-cicd--pre-push-checks)
6. [Phase 5: Verification & Troubleshooting](#6-phase-5-verification--troubleshooting)

---

## 1. Architecture Overview

The platform uses a **dual-ingress** pattern with shared runtime logic:

```text
┌──────────────────────────────────────────────────────────────────────┐
│                       AWS CLOUD BOUNDARY                             │
│                                                                      │
│  ┌────────────────┐  ┌─────────────────┐  ┌─────────────────────┐   │
│  │  AWS Cognito    │  │  DynamoDB        │  │  Aurora PostgreSQL  │   │
│  │  (Auth + JWT)   │  │  (Single Table)  │  │  (pgvector for KB)  │   │
│  └───────┬────────┘  └────────┬────────┘  └──────────┬──────────┘   │
│          │                    │                       │              │
│  ┌───────▼────────────────────▼───────────────────────▼──────────┐   │
│  │              Shared Agent Runtime Container                    │   │
│  │                                                                │   │
│  │   FastAPI (:8000)          AgentCore MicroVM (:8080)           │   │
│  │   ├── /v1/auth/*           ├── /invocations                   │   │
│  │   ├── /v1/agents/*/invoke  └── entrypoints/agent_gateway.py   │   │
│  │   ├── /v1/sessions/*                                          │   │
│  │   ├── /v1/knowledge-bases/*                                   │   │
│  │   └── /ui (Gradio)                                            │   │
│  └───────┬────────────────────────────────────────────┬──────────┘   │
│          │                                            │              │
│  ┌───────▼────────────────────────────────────────────▼──────────┐   │
│  │  Amazon Bedrock Models          │  Bedrock Knowledge Bases    │   │
│  │  (nova-pro, claude-sonnet)      │  (Vector Retrieval)         │   │
│  └─────────────────────────────────┴─────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
```

### Key Services

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Authentication | AWS Cognito | User signup, email confirm, JWT login |
| Session Storage | DynamoDB (single-table) | Users, sessions, KB metadata, profiles |
| Vector Store | Aurora PostgreSQL + pgvector | Knowledge Base embeddings |
| Agent Runtime | Strands Agent SDK | Bedrock model invocation + tool calling |
| Retrieval | Bedrock Knowledge Bases | Document search via `search_manuals` tool |
| UI | Gradio | Chat interface with auth + KB management |

---

## 2. Phase 1: AWS Environment Provisioning

### A. DynamoDB Table

Create a single table for all platform data:

```bash
aws dynamodb create-table \
  --table-name CoactionPlatform \
  --attribute-definitions \
    AttributeName=PK,AttributeType=S \
    AttributeName=SK,AttributeType=S \
  --key-schema \
    AttributeName=PK,KeyType=HASH \
    AttributeName=SK,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST
```

The table uses composite keys:
- `USER#<sub>` + `PROFILE` → User profiles
- `USER#<sub>` + `SESSION#<sid>` → Chat sessions
- `KB#<id>` + `META` → Knowledge Base metadata
- `PROFILE#<agent_id>` + `VERSION#<ver>` → Execution profiles

### B. AWS Cognito User Pool

1. Create a **User Pool** with email-based sign-in
2. Add a custom attribute: `custom:role` (string, mutable)
3. Create an **App Client** with `USER_PASSWORD_AUTH` flow enabled
4. Note: `COGNITO_USER_POOL_ID` and `COGNITO_APP_CLIENT_ID`

### C. Aurora PostgreSQL (for Bedrock KB)

1. Launch **Aurora PostgreSQL-Compatible** (Serverless v2)
2. Restrict port `5432` to your VPC subnets only
3. Enable pgvector:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS bedrock_integration;

CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (
    id uuid DEFAULT uuid_generate_v4() PRIMARY KEY,
    embedding vector(1536),
    chunks text,
    metadata jsonb
);

CREATE INDEX ON bedrock_integration.bedrock_kb USING hnsw (embedding vector_cosine_ops);
```

### D. S3 Bucket for Documents

```bash
aws s3 mb s3://vega-binding-authority --region us-east-1
aws s3api put-public-access-block \
  --bucket vega-binding-authority \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
```

### E. IAM Execution Role

Create `VegaPlatformExecutionRole` with permissions for:
- `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream`
- `bedrock:Retrieve`, `bedrock:RetrieveAndGenerate`
- `bedrock-agent:CreateKnowledgeBase`, `bedrock-agent:StartIngestionJob` etc.
- `s3:GetObject`, `s3:PutObject`, `s3:ListBucket`
- `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:DeleteItem`
- `cognito-idp:*` (for user management)
- `logs:CreateLogStream`, `logs:PutLogEvents`

### F. Bedrock Knowledge Base

1. AWS Console → Bedrock → Knowledge Bases → Create
2. Assign `VegaPlatformExecutionRole`
3. Select embedding model: `amazon.titan-embed-text-v2:0`
4. Connect Aurora PostgreSQL as vector store
5. Add S3 data source → Sync
6. Note the **Knowledge Base ID** (e.g., `2KMBSFAGGS`)

Or create programmatically via the API:
```bash
curl -X POST http://localhost:8000/v1/knowledge-bases \
  -H "Authorization: Bearer <token>" \
  -d '{"name": "my-kb", "s3_bucket": "vega-binding-authority", "s3_prefix": "docs/"}'
```

---

## 3. Phase 2: Application Setup & Local Development

### Environment Configuration

Create `.env` at project root:

```env
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-key
AWS_SECRET_ACCESS_KEY=your-secret

COGNITO_USER_POOL_ID=us-east-1_XXXXXXX
COGNITO_APP_CLIENT_ID=your-client-id

DYNAMODB_TABLE_NAME=CoactionPlatform

BEDROCK_KB_ID=your-kb-id
BEDROCK_MODEL_ID=amazon.nova-pro-v1:0

BEDROCK_KB_ROLE_ARN=arn:aws:iam::...:role/VegaPlatformExecutionRole
EMBEDDING_MODEL_ARN=arn:aws:bedrock:us-east-1::foundation-model/amazon.titan-embed-text-v2:0
```

### Running Locally

```bash
# Install dependencies
pip install -r requirements.txt
pip install ruff pytest   # Development tools

# Start the platform (API + UI)
python main.py
# → API at http://localhost:8000/v1
# → UI at http://localhost:8000/ui
# → Docs at http://localhost:8000/docs

# CLI testing (no server required)
python scripts/query.py "What class codes cover restaurants?"
python scripts/query.py --interactive
```

### Docker

```bash
docker build -t project-vega .
docker run -p 8000:8000 --env-file .env project-vega
```

---

## 4. Phase 3: Agent Creation & Deployment

### Method A: Manual Creation

**Step 1** — Define the system prompt in `agents/prompts.py`:
```python
PROMPT_TEMPLATES["my_new_agent_v1"] = """<role>
You are an expert assistant for XYZ domain.
</role>

<core_directives>
1. NO HALLUCINATION: Every fact must be from retrieved context.
2. CITATION: Include sources for every response.
</core_directives>

<response_format>
- Answer first, then citations, then follow-up questions.
</response_format>
"""
```

**Step 2** — Create a Knowledge Base (via console or API):
```bash
# Via the platform API (underwriter role required)
curl -X POST http://localhost:8000/v1/knowledge-bases \
  -H "Authorization: Bearer <jwt>" \
  -d '{"name": "my-kb", "s3_bucket": "my-bucket", "s3_prefix": "docs/"}'
```

**Step 3** — Store an Execution Profile in DynamoDB:
```python
from adapters.aws.dynamodb import DynamoDBAdapter

db = DynamoDBAdapter(table_name="CoactionPlatform")
db.save_execution_profile(
    agent_id="my-new-agent",
    version="latest",
    profile_data={
        "agent_id": "my-new-agent",
        "version": "1.0",
        "prompt_template_id": "my_new_agent_v1",
        "model_profile": {"model_id": "amazon.nova-pro-v1:0"},
        "retrieval_profile": {"knowledge_base_ids": ["YOUR_KB_ID"]},
        "memory_profile": {"enabled": True},
    }
)
```

**Step 4** — Invoke the agent:
```bash
curl -X POST http://localhost:8000/v1/agents/my-new-agent/invoke \
  -H "Authorization: Bearer <jwt>" \
  -d '{"input_text": "What are the coverage limits?"}'
```

### Method B: Automated Pipeline (Single Command)

**Step 1** — Create `profiles/my_agent.json`:
```json
{
  "agent_id": "my-new-agent",
  "version": "1.0",
  "prompt_template_id": "my_new_agent_v1",
  "s3_bucket": "my-data-bucket",
  "s3_prefix": "docs/my-agent/",
  "model_profile": {"model_id": "amazon.nova-pro-v1:0"},
  "retrieval_profile": {"knowledge_base_ids": [], "enabled": true}
}
```

**Step 2** — Run the bootstrap script:
```bash
python scripts/platform_bootstrap.py \
  my-new-agent \
  my-data-bucket \
  arn:aws:iam::123456:role/VegaPlatformExecutionRole
```

This automatically creates the KB, adds the S3 data source, triggers sync, and deploys the agent to Bedrock AgentCore.

---

## 4.5 Pushing Code Updates to Existing Agents

If you modify `agents/prompts.py` or agent logic, you must push those changes to the AWS Bedrock AgentCore runtime.

### Method A: Automated Deployment Pipeline
This is the recommended approach for deploying code updates.

**1. Authenticate and Push the Container to AWS ECR:**
```bash
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <your-account-id>.dkr.ecr.us-east-1.amazonaws.com

# Build specifically for AWS Graviton (ARM64)
docker buildx build --target runtime --platform linux/arm64 -t <your-account-id>.dkr.ecr.us-east-1.amazonaws.com/vega-platform:latest --push .
```

**2. Trigger the Automated AgentCore Update:**
Run the bootstrap script again. Because the KB and Memory already exist, it skips creation, pulls your new container, and publishes a new version.
```bash
python scripts/platform_bootstrap.py \
  <your_agent_id> \
  <your_bucket_name> \
  arn:aws:iam::<your-account-id>:role/VegaPlatformExecutionRole
```
AWS will automatically update your `DEFAULT` endpoint to point to the newly published version.

### Method B: Manual Deployment via AWS Console
If you do not want to use the automated script:
1. Build and push your Docker container to ECR (same as Step 1 above).
2. Open the **AWS Console** → **Bedrock** → **AgentCore**.
3. Select your Agent Runtime.
4. Click **Create Version** (this pulls the latest `:latest` container from ECR).
5. Go to **Endpoints**, select the `DEFAULT` endpoint, click **Edit**, and change the associated version to the new version you just created.

---

## 5. Phase 4: CI/CD & Pre-Push Checks

### Before Every Push

```bash
# Read-only check (recommended before push)
python scripts/pre_push_check.py

# Auto-fix lint + format
python scripts/pre_push_check.py --fix
```

This runs:
1. **Ruff Lint** — catches import errors, unused vars, bare excepts
2. **Ruff Format** — enforces consistent code style
3. **Pytest** — runs all unit tests

### Makefile Targets

```bash
make lint     # ruff check
make format   # ruff format
make test     # pytest
make check    # All three
make fix      # Auto-fix + reformat
```

### GitHub Actions

`.github/workflows/ci.yml` runs automatically on push/PR:
- Ruff lint + format conformance
- Pytest suite
- (Optional) Docker build + ECR push + ECS deploy

---

## 6. Phase 5: Verification & Troubleshooting

### Health Checks

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ping
curl http://localhost:8000/ready
```

### CloudWatch Logs

Access log streams at:
```
/aws/bedrock-agentcore/runtimes/<agent_id>-<runtime_hash>-DEFAULT
```

### Common Errors & Fixes

| Error | Cause | Fix |
|-------|-------|-----|
| `psycopg2.OperationalError: connection to server at "localhost" failed` | MicroVM lacks cloud DB config | Run `platform_bootstrap.py` to inject real DB endpoints |
| `JWTError: Unable to find matching key for kid` | Cognito JWKS key rotation | Restart app to flush JWKS cache |
| `HTTPException 503: Auth service not initialized` | Missing `COGNITO_USER_POOL_ID` env var | Set Cognito env vars in `.env` |
| `HTTPException 503: Agent service not initialized` | Lifespan startup failed | Check `DYNAMODB_TABLE_NAME` and AWS credentials |
| `AccessDeniedException: service control policy` | Cross-region model ID | Use intra-region IDs like `amazon.nova-pro-v1:0` |

---

**Maintained by**: Coaction Agent Platform Engineering Team  
👉 **[Return to README](./README.md)** · **[Codebase Guide](./codebase_detailed_guide.md)**
