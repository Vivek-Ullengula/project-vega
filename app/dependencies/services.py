# app/dependencies/services.py
"""Service dependency injection — provides singleton access to key services.

Used primarily by the AgentCore entrypoint (entrypoints/agent_gateway.py)
and any legacy code paths. The main FastAPI app uses lifespan-based DI instead.
"""

from functools import lru_cache

from adapters.aws.boto3_factory import Boto3SessionFactory
from adapters.aws.dynamodb import DynamoDBAdapter
from services.agent_service import AgentService
from services.authorization import AuthorizationService
from services.guardrails import GuardrailService
from services.memory import AgentCoreMemoryProvider
from services.model_gateway import BedrockModelGateway
from services.tool_gateway import AgentCoreReadOnlyToolGateway
from services.telemetry import CloudWatchTelemetryEmitter
from services.audit import MetadataOnlyAuditLogger
from runtime.orchestrator import RuntimeOrchestrator
from runtime.response_composer import ResponseComposer
from app.dependencies.settings import get_settings


@lru_cache()
def get_boto3_factory() -> Boto3SessionFactory:
    return Boto3SessionFactory(region_name=get_settings().aws_region)


@lru_cache()
def get_dynamodb_adapter() -> DynamoDBAdapter:
    settings = get_settings()
    table = (
        settings.dynamodb_table_name
        if hasattr(settings, "dynamodb_table_name")
        else "CoactionPlatform"
    )
    return DynamoDBAdapter(table_name=table, region=settings.aws_region)


@lru_cache()
def get_agent_service() -> AgentService:
    settings = get_settings()
    dynamodb = get_dynamodb_adapter()
    return AgentService(dynamodb=dynamodb, region=settings.aws_region)


@lru_cache()
def get_orchestrator() -> RuntimeOrchestrator:
    """Build a RuntimeOrchestrator for use by the AgentCore entrypoint."""
    factory = get_boto3_factory()
    dynamodb = get_dynamodb_adapter()

    from control_plane.execution_profile_repository import ExecutionProfileRepository
    
    return RuntimeOrchestrator(
        profile_repo=ExecutionProfileRepository(dynamodb_adapter=dynamodb, config_dir="profiles"),
        authorization=AuthorizationService(),
        guardrails=GuardrailService(boto3_factory=factory),
        retriever=None,
        memory=AgentCoreMemoryProvider(dynamodb_adapter=dynamodb, boto3_factory=factory),
        model_gateway=BedrockModelGateway(region=get_settings().aws_region),
        tool_gateway=AgentCoreReadOnlyToolGateway(boto3_factory=factory),
        response_composer=ResponseComposer(),
        telemetry=CloudWatchTelemetryEmitter(boto3_factory=factory),
        audit=MetadataOnlyAuditLogger(),
    )
