# app/dependencies/services.py
"""Service dependency injection — provides singleton access to key services.

Used primarily by the AgentCore entrypoint (entrypoints/agent_gateway.py).
The main FastAPI app uses lifespan-based DI instead.
"""

from functools import lru_cache

from adapters.aws.boto3_factory import Boto3SessionFactory
from adapters.aws.dynamodb import DynamoDBAdapter
from services.agent_service import AgentService
from services.authorization import AuthorizationService
from services.guardrails import GuardrailService
from services.telemetry import CloudWatchTelemetryEmitter
from services.audit import MetadataOnlyAuditLogger
from runtime.orchestrator import RuntimeOrchestrator
from app.dependencies.settings import get_settings


@lru_cache()
def get_boto3_factory() -> Boto3SessionFactory:
    return Boto3SessionFactory(region_name=get_settings().aws_region)


@lru_cache()
def get_dynamodb_adapter() -> DynamoDBAdapter:
    settings = get_settings()
    return DynamoDBAdapter(table_name=settings.dynamodb_table_name, region=settings.aws_region)


@lru_cache()
def get_agent_service() -> AgentService:
    settings = get_settings()
    dynamodb = get_dynamodb_adapter()
    return AgentService(dynamodb=dynamodb, region=settings.aws_region)


@lru_cache()
def get_orchestrator() -> RuntimeOrchestrator:
    """Build a RuntimeOrchestrator for use by the AgentCore entrypoint.

    The orchestrator wraps AgentService with cross-cutting concerns
    (authorization, guardrails, telemetry, audit) for cloud deployments.
    """
    factory = get_boto3_factory()
    dynamodb = get_dynamodb_adapter()
    agent_service = get_agent_service()

    from control_plane.execution_profile_repository import ExecutionProfileRepository

    return RuntimeOrchestrator(
        agent_service=agent_service,
        profile_repo=ExecutionProfileRepository(dynamodb_adapter=dynamodb, config_dir="profiles"),
        authorization=AuthorizationService(),
        guardrails=GuardrailService(boto3_factory=factory),
        telemetry=CloudWatchTelemetryEmitter(boto3_factory=factory),
        audit=MetadataOnlyAuditLogger(),
    )
