# app/main.py
"""FastAPI application entry point for the Coaction Agent Platform.

Wires all layers per HLD:
- Boto3SessionFactory (centralized AWS client creation)
- RuntimeOrchestrator (standard execution pipeline)
- Control plane (agent registry, execution profiles)
- Services (authorization, guardrails, memory, model gateway, tool gateway, telemetry, audit)
- Middleware (correlation ID, error handling)
- Routers (auth, sessions, knowledge bases, agent invoke)
- Gradio UI (unified deployment)
"""

import os
import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Domain Models
from domain.models import AgentInvocationRequest, IdentityContext

# AWS Adapters
from adapters.aws.boto3_factory import Boto3SessionFactory
from adapters.aws.cognito import CognitoAdapter, CognitoConfig
from adapters.aws.dynamodb import DynamoDBAdapter
from adapters.aws.bedrock_kb_manager import BedrockKBManager

# Control Plane
from control_plane.agent_registry import AgentRegistryRepository
from control_plane.execution_profile_repository import ExecutionProfileRepository

# Services
from services.authorization import AuthorizationService
from services.guardrails import GuardrailService
from services.memory import AgentCoreMemoryProvider
from services.model_gateway import BedrockModelGateway
from services.tool_gateway import AgentCoreReadOnlyToolGateway
from services.telemetry import CloudWatchTelemetryEmitter
from services.audit import MetadataOnlyAuditLogger
from services.agent_service import AgentService

# Runtime
from runtime.orchestrator import RuntimeOrchestrator
from runtime.response_composer import ResponseComposer

# Identity
from app.dependencies.identity import init_jwt_verifier

# Middleware
from app.middleware.correlation import CorrelationIdMiddleware
from app.middleware.errors import ErrorHandlerMiddleware

# Routers
from app.routers.auth_router import router as auth_router, init_auth_router
from app.routers.session_router import router as session_router, init_session_router
from app.routers.kb_router import router as kb_router, init_kb_router
from app.routers.agent_router import router as agent_router, init_agent_router
from app.routers.health import router as health_router

logger = structlog.get_logger(__name__)


def _env(key: str, default: str = "") -> str:
    """Get an environment variable with a default."""
    return os.environ.get(key, default)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize all services at startup per HLD build sequence."""
    logger.info("app_starting")

    # ── Configuration from environment ──
    region = _env("AWS_REGION", "us-east-1")
    cognito_user_pool_id = _env("COGNITO_USER_POOL_ID")
    cognito_app_client_id = _env("COGNITO_APP_CLIENT_ID")
    dynamodb_table = _env("DYNAMODB_TABLE_NAME", "CoactionPlatform")
    kb_role_arn = _env("BEDROCK_KB_ROLE_ARN")
    embedding_model_arn = _env(
        "EMBEDDING_MODEL_ARN",
        f"arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0",
    )
    rds_resource_arn = _env("RDS_RESOURCE_ARN")
    rds_credentials_secret_arn = _env("RDS_CREDENTIALS_SECRET_ARN")

    # ── Step 1: Boto3 Client Factory (HLD §15) ──
    boto3_factory = Boto3SessionFactory(region_name=region)

    # ── Step 2: Cognito Auth ──
    cognito_adapter = None
    if cognito_user_pool_id and cognito_app_client_id:
        cognito_config = CognitoConfig(
            region=region,
            user_pool_id=cognito_user_pool_id,
            app_client_id=cognito_app_client_id,
        )
        cognito_adapter = CognitoAdapter(cognito_config)
        init_jwt_verifier(region, cognito_user_pool_id, cognito_app_client_id)
        logger.info("cognito_initialized")
    else:
        logger.warning("cognito_not_configured")

    # ── Step 3: DynamoDB ──
    dynamodb_adapter = DynamoDBAdapter(table_name=dynamodb_table, region=region)

    # ── Step 4: Control Plane Repositories (HLD §5) ──
    agent_registry = AgentRegistryRepository(dynamodb_adapter=dynamodb_adapter)  # noqa: F841
    execution_profile_repo = ExecutionProfileRepository(dynamodb_adapter=dynamodb_adapter, config_dir="profiles")

    # ── Step 5: Services (HLD §8, §9, §12) ──
    authorization = AuthorizationService()
    guardrails = GuardrailService(boto3_factory=boto3_factory)
    memory = AgentCoreMemoryProvider(dynamodb_adapter=dynamodb_adapter, boto3_factory=boto3_factory)
    model_gateway = BedrockModelGateway(region=region)
    tool_gateway = AgentCoreReadOnlyToolGateway(boto3_factory=boto3_factory)
    response_composer = ResponseComposer()
    telemetry = CloudWatchTelemetryEmitter(boto3_factory=boto3_factory)
    audit = MetadataOnlyAuditLogger()

    # ── Step 6: Runtime Orchestrator (HLD §8) ──
    RuntimeOrchestrator(  # noqa: F841
        profile_repo=execution_profile_repo,
        authorization=authorization,
        guardrails=guardrails,
        retriever=None,
        memory=memory,
        model_gateway=model_gateway,
        tool_gateway=tool_gateway,
        response_composer=response_composer,
        telemetry=telemetry,
        audit=audit,
    )

    # ── Step 7: Agent Service ──
    agent_service = AgentService(dynamodb=dynamodb_adapter, region=region)

    # ── Step 8: Bedrock KB Manager ──
    kb_manager = BedrockKBManager(
        region=region,
        role_arn=kb_role_arn,
        embedding_model_arn=embedding_model_arn,
    )
    kb_manager._rds_resource_arn = rds_resource_arn
    kb_manager._rds_credentials_secret_arn = rds_credentials_secret_arn

    # Store in app state for root handler access
    app.state.agent_service = agent_service

    # ── Step 9: Wire Routers ──
    init_auth_router(cognito_adapter, dynamodb_adapter)
    init_session_router(dynamodb_adapter)
    init_kb_router(kb_manager, dynamodb_adapter)
    init_agent_router(agent_service)

    logger.info("app_ready", region=region, dynamodb_table=dynamodb_table)

    yield  # Application runs

    logger.info("app_shutting_down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application per HLD §10."""
    app = FastAPI(
        title="Coaction Agent Platform",
        description=(
            "Project Vega — Standard Agent Runtime. "
            "Configuration-driven agent platform with Strands orchestration, "
            "Bedrock KB retrieval, AgentCore Memory, Cognito auth, and DynamoDB storage."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # ── Middleware (HLD §12) ──
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers (HLD §10) — prefixed with /v1 ──
    app.include_router(auth_router, prefix="/v1")
    app.include_router(session_router, prefix="/v1")
    app.include_router(kb_router, prefix="/v1")
    app.include_router(agent_router, prefix="/v1")
    app.include_router(health_router)

    # ── AgentCore Invocation Paths ──
    @app.post("/invocations")
    async def invocations_root(request: Request):
        """Standard AgentCore invocation path."""
        payload = await request.json()
        input_text = payload.get("input_text") or payload.get("prompt")
        if not input_text:
            return {"status": "error", "answer": "Missing 'input_text' or 'prompt' in payload."}

        invocation = AgentInvocationRequest(
            agent_id="coaction-underwriting",
            input_text=input_text,
            session_id=payload.get("session_id"),
        )
        identity = IdentityContext(
            user_id="agentcore-system",
            roles=["agent"],
            channel="agentcore",
            correlation_id=getattr(request.state, "correlation_id", "agentcore-invoke"),
        )

        service = request.app.state.agent_service
        return await service.invoke(invocation, identity)

    @app.post("/")
    async def root_invoke(request: Request):
        """Root handler for direct Bedrock AgentCore invocations."""
        payload = await request.json()
        input_text = payload.get("input_text") or payload.get("prompt")
        if not input_text:
            return {"status": "error", "answer": "Missing 'input_text' or 'prompt' in payload."}

        invocation = AgentInvocationRequest(
            agent_id="coaction-underwriting",
            input_text=input_text,
            session_id=payload.get("session_id"),
        )
        identity = IdentityContext(
            user_id="agentcore-system",
            roles=["agent"],
            channel="agentcore",
            correlation_id=getattr(request.state, "correlation_id", "agentcore-root"),
        )

        service = request.app.state.agent_service
        return await service.invoke(invocation, identity)

    # ── Mount Gradio UI ──
    try:
        from ui.gradio_app import build as build_gradio
        import gradio as gr

        gradio_app = build_gradio()
        gr.mount_gradio_app(app, gradio_app, path="/ui")
        logger.info("gradio_ui_mounted", path="/ui")
    except Exception as e:
        logger.warning("gradio_ui_not_mounted", error=str(e))

    return app


# Create the app instance
app = create_app()
