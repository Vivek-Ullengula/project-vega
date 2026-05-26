# coaction_agent_platform/runtime/orchestrator.py
"""Runtime orchestrator per HLD Section 8.

Enforces the standard execution sequence for cloud (AgentCore) invocations:
authorize → guardrails input → agent invocation → guardrails output →
telemetry → audit.

The orchestrator delegates the core agent invocation to AgentService,
which handles profile loading, agent building, session management,
and response construction. The orchestrator wraps this with cross-cutting
concerns (authorization, guardrails, telemetry, audit).

For local FastAPI invocations, AgentService is called directly (lean path).
For cloud AgentCore invocations, the orchestrator adds the middleware pipeline.
"""

import uuid
import structlog

from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
)
from services.authorization import AuthorizationService
from services.guardrails import GuardrailBlockedError, GuardrailService
from services.telemetry import CloudWatchTelemetryEmitter
from services.audit import MetadataOnlyAuditLogger

logger = structlog.get_logger(__name__)


class RuntimeOrchestrator:
    """Standard runtime orchestrator — enforces the execution pipeline for cloud invocations.

    Per HLD Section 8, this wraps the AgentService invocation with cross-cutting
    concerns that are required for production cloud deployments.
    """

    def __init__(
        self,
        agent_service,
        profile_repo,
        authorization: AuthorizationService,
        guardrails: GuardrailService,
        telemetry: CloudWatchTelemetryEmitter,
        audit: MetadataOnlyAuditLogger,
    ) -> None:
        self.agent_service = agent_service
        self.profile_repo = profile_repo
        self.authorization = authorization
        self.guardrails = guardrails
        self.telemetry = telemetry
        self.audit = audit

    async def execute(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
    ) -> AgentInvocationResponse:
        """Execute the standard orchestration pipeline.

        Pipeline:
        1. Load execution profile
        2. Authorization check
        3. Input guardrails
        4. Delegate to AgentService (profile load, agent build, KB retrieval, session mgmt)
        5. Output guardrails
        6. Telemetry
        7. Audit
        """
        session_id = request.session_id or str(uuid.uuid4())
        correlation_id = identity.correlation_id

        logger.info(
            "orchestrator_execute_start",
            agent_id=request.agent_id,
            session_id=session_id,
            user_id=identity.user_id,
        )

        try:
            # 1. Load execution profile (for authorization & guardrail checks)
            profile = await self.profile_repo.get_profile(request.agent_id)

            # 2. Authorization check
            await self.authorization.authorize_invocation(identity, profile)

            # 3. Input guardrails
            await self.guardrails.check_input(request, profile)

            # 4. Delegate to AgentService for core invocation
            response = await self.agent_service.invoke(request, identity)

            # 5. Output guardrails
            await self.guardrails.check_output(response, profile)

            # 6. Telemetry
            await self.telemetry.emit_invocation(request, response, profile)

            # 7. Audit
            await self.audit.record_invocation(request, response, identity, profile)

            return response

        except GuardrailBlockedError as e:
            logger.warning("orchestrator_guardrail_blocked", source=e.source)
            return AgentInvocationResponse(
                status="blocked",
                answer=str(e),
                session_id=session_id,
                correlation_id=correlation_id,
            )
        except Exception as e:
            logger.error("orchestrator_execute_failed", error=str(e))
            return AgentInvocationResponse(
                status="error",
                answer="Sorry, I couldn't complete that request. Please try again or contact support if it continues.",
                session_id=session_id,
                correlation_id=correlation_id,
            )
