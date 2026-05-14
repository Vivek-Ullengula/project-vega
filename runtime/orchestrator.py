# coaction_agent_platform/runtime/orchestrator.py
"""Runtime orchestrator per HLD Section 8.

Enforces the standard execution sequence for every agent invocation:
authorize → guardrails input → memory read → retrieval → model → tools →
compose response → guardrails output → memory write → telemetry → audit.
"""

import uuid
import structlog

from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    IdentityContext,
)
from runtime.response_composer import ResponseComposer
from services.authorization import AuthorizationService
from services.guardrails import GuardrailService
from services.telemetry import CloudWatchTelemetryEmitter
from services.audit import MetadataOnlyAuditLogger

logger = structlog.get_logger(__name__)


class RuntimeOrchestrator:
    """Standard runtime orchestrator — enforces the execution pipeline for all agents.

    Per HLD Section 8, this is the central execution pipeline that every agent
    invocation flows through, regardless of agent type.
    """

    def __init__(
        self,
        profile_repo,
        authorization: AuthorizationService,
        guardrails: GuardrailService,
        retriever,
        memory,
        model_gateway,
        tool_gateway,
        response_composer: ResponseComposer,
        telemetry: CloudWatchTelemetryEmitter,
        audit: MetadataOnlyAuditLogger,
    ) -> None:
        self.profile_repo = profile_repo
        self.authorization = authorization
        self.guardrails = guardrails
        self.retriever = retriever
        self.memory = memory
        self.model_gateway = model_gateway
        self.tool_gateway = tool_gateway
        self.response_composer = response_composer
        self.telemetry = telemetry
        self.audit = audit

    async def execute(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
    ) -> AgentInvocationResponse:
        """Execute the standard orchestration pipeline."""
        session_id = request.session_id or str(uuid.uuid4())
        correlation_id = identity.correlation_id

        logger.info(
            "orchestrator_execute_start",
            agent_id=request.agent_id,
            session_id=session_id,
            user_id=identity.user_id,
        )

        try:
            # 1. Load execution profile
            profile = await self.profile_repo.get_profile(request.agent_id)

            # 2. Authorization check
            await self.authorization.authorize_invocation(identity, profile)

            # 3. Input guardrails
            await self.guardrails.check_input(request, profile)

            # 4. Read memory context
            memory_context = await self.memory.read(request, identity, profile)

            # 5. Retrieval (delegated to the agent's tools via Strands)
            # The retriever runs inside the Strands agent as a tool call.
            # We pass memory context for conversation history.

            # 6. Model invocation (via Strands agent)
            model_result = await self.model_gateway.invoke(
                request=request,
                identity=identity,
                profile=profile,
                memory_context=memory_context,
            )

            # 7. Tool results (extracted from model_result if any)
            tool_results = await self.tool_gateway.execute_readonly_tools(
                model_result=model_result,
                identity=identity,
                profile=profile,
            )

            # 8. Compose response
            response = await self.response_composer.compose(
                request=request,
                profile=profile,
                model_result=model_result,
                retrieved_context=model_result.get("citations"),
                memory_context=memory_context,
                tool_results=tool_results,
                session_id=session_id,
                correlation_id=correlation_id,
            )

            # 9. Output guardrails
            await self.guardrails.check_output(response, profile)

            # 10. Write memory
            await self.memory.write(request, response, identity, profile)

            # 11. Telemetry
            await self.telemetry.emit_invocation(request, response, profile)

            # 12. Audit
            await self.audit.record_invocation(request, response, identity, profile)

            return response

        except Exception as e:
            logger.error("orchestrator_execute_failed", error=str(e))
            return AgentInvocationResponse(
                status="error",
                answer=f"An error occurred: {str(e)}",
                session_id=session_id,
                correlation_id=correlation_id,
            )
