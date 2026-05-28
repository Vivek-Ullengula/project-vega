# ruff: noqa: E402
# app/main.py
"""FastAPI application entry point for the Coaction Agent Platform.

Wires all layers per HLD:
- Boto3SessionFactory (centralized AWS client creation)
- AgentService (core agent invocation pipeline)
- Middleware (correlation ID, error handling)
- Routers (auth, sessions, knowledge bases, agent invoke)
- React SPA static serving
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Purge empty string environment variables to prevent client libraries from throwing credential errors
for _k, _v in list(os.environ.items()):
    if _v == "":
        os.environ.pop(_k, None)

import structlog
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

# Domain Models
from domain.models import AgentInvocationRequest, IdentityContext

# AWS Adapters
from adapters.aws.cognito import CognitoAdapter, CognitoConfig
from adapters.aws.dynamodb import DynamoDBAdapter
from adapters.aws.bedrock_kb_manager import BedrockKBManager

# Services
from services.agent_service import AgentService

# Identity
from app.dependencies.identity import get_identity_context, init_jwt_verifier

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

    # Configuration from environment.
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

    # Step 1: Boto3 client factory.
    # (Boto3 session factory is handled via services container on demand)

    # Step 2: Cognito auth.
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

    # Step 3: DynamoDB.
    dynamodb_adapter = DynamoDBAdapter(table_name=dynamodb_table, region=region)

    # Step 4: Agent service.
    agent_service = AgentService(dynamodb=dynamodb_adapter, region=region)

    # Step 5: Bedrock KB manager.
    kb_manager = BedrockKBManager(
        region=region,
        role_arn=kb_role_arn,
        embedding_model_arn=embedding_model_arn,
    )
    kb_manager._rds_resource_arn = rds_resource_arn
    kb_manager._rds_credentials_secret_arn = rds_credentials_secret_arn

    # Store in app state for AgentCore invocation handlers
    app.state.agent_service = agent_service

    # Step 6: Wire routers.
    init_auth_router(cognito_adapter, dynamodb_adapter)
    init_session_router(dynamodb_adapter)
    init_kb_router(kb_manager, dynamodb_adapter)
    init_agent_router(agent_service)

    logger.info("app_ready", region=region, dynamodb_table=dynamodb_table)

    yield  # Application runs

    logger.info("app_shutting_down")


async def _handle_agentcore_invoke(request: Request, identity: IdentityContext) -> dict:
    """Shared handler for AgentCore invocation paths (POST / and POST /invocations)."""
    payload = await request.json()
    input_text = payload.get("input_text") or payload.get("prompt")
    if not input_text:
        return {"status": "error", "answer": "Missing 'input_text' or 'prompt' in payload."}

    invocation = AgentInvocationRequest(
        agent_id=payload.get("agent_id", "coaction-underwriting"),
        input_text=input_text,
        session_id=payload.get("session_id"),
        top_k=payload.get("top_k", 5),
    )

    service = request.app.state.agent_service
    return await service.invoke(invocation, identity)


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

    # Middleware.
    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(CorrelationIdMiddleware)
    cors_origin_raw = _env(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    )
    cors_origins = [origin.strip() for origin in cors_origin_raw.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials="*" not in cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers are prefixed with /v1.
    app.include_router(auth_router, prefix="/v1")
    app.include_router(session_router, prefix="/v1")
    app.include_router(kb_router, prefix="/v1")
    app.include_router(agent_router, prefix="/v1")
    app.include_router(health_router)

    # AgentCore invocation paths.
    @app.post("/invocations")
    async def invocations_root(
        request: Request,
        identity: IdentityContext = Depends(get_identity_context),
    ):
        """Standard AgentCore invocation path."""
        return await _handle_agentcore_invoke(request, identity)

    @app.post("/")
    async def root_invoke(
        request: Request,
        identity: IdentityContext = Depends(get_identity_context),
    ):
        """Root handler for direct Bedrock AgentCore invocations."""
        return await _handle_agentcore_invoke(request, identity)

    # Mount React frontend SPA in production builds.
    frontend_dist = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")
    )
    if os.path.exists(frontend_dist):
        from fastapi.staticfiles import StaticFiles
        from fastapi.responses import FileResponse

        assets_dir = os.path.join(frontend_dist, "assets")
        if os.path.exists(assets_dir):
            app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

        @app.get("/{fallback_path:path}")
        async def frontend_fallback(fallback_path: str):
            # Allow API and health check routes to pass through.
            if (
                fallback_path.startswith("v1/")
                or fallback_path == "v1"
                or fallback_path == "health"
                or fallback_path == "invocations"
            ):
                from fastapi import HTTPException

                raise HTTPException(status_code=404, detail="Not Found")

            # Serve static files (e.g. coaction.png, favicon.svg, icons.svg)
            # that Vite copies from public/ to the dist root
            static_file = os.path.join(frontend_dist, fallback_path)
            if fallback_path and os.path.isfile(static_file):
                return FileResponse(static_file)

            return FileResponse(os.path.join(frontend_dist, "index.html"))

        logger.info("react_frontend_mounted", path=frontend_dist)

    return app


# Create the app instance
app = create_app()
