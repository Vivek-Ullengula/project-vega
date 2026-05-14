# coaction_agent_platform/services/guardrails.py
"""Guardrail service per HLD Section 8.

Input and output content checks using Amazon Bedrock Guardrails.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    ExecutionProfile,
)

logger = structlog.get_logger(__name__)


class GuardrailService:
    """Amazon Bedrock Guardrails integration.

    In the first release, guardrails are logged but not yet enforced
    unless a guardrail_id is configured in the ExecutionProfile.
    """

    def __init__(self, boto3_factory=None):
        self.boto3_factory = boto3_factory
        self.client = boto3_factory.client("bedrock-runtime") if boto3_factory else None

    async def check_input(
        self,
        request: AgentInvocationRequest,
        profile: ExecutionProfile,
    ) -> None:
        """Check input against guardrail policy."""
        if not profile.guardrail_profile.input_check_enabled:
            return
        if not profile.guardrail_profile.guardrail_id:
            return  # No guardrail configured

        try:
            response = self.client.apply_guardrail(
                guardrailIdentifier=profile.guardrail_profile.guardrail_id,
                guardrailVersion=profile.guardrail_profile.guardrail_version or "DRAFT",
                source="INPUT",
                content=[{"text": {"text": request.input_text}}],
            )
            action = response.get("action", "NONE")
            if action == "GUARDRAIL_INTERVENED":
                logger.warning("guardrail_input_blocked", agent_id=request.agent_id)
                # In first release, log but do not block
        except Exception as e:
            logger.error("guardrail_input_check_failed", error=str(e))

    async def check_output(
        self,
        response: AgentInvocationResponse,
        profile: ExecutionProfile,
    ) -> None:
        """Check output against guardrail policy."""
        if not profile.guardrail_profile.output_check_enabled:
            return
        if not profile.guardrail_profile.guardrail_id:
            return

        try:
            result = self.client.apply_guardrail(
                guardrailIdentifier=profile.guardrail_profile.guardrail_id,
                guardrailVersion=profile.guardrail_profile.guardrail_version or "DRAFT",
                source="OUTPUT",
                content=[{"text": {"text": response.answer}}],
            )
            action = result.get("action", "NONE")
            if action == "GUARDRAIL_INTERVENED":
                logger.warning("guardrail_output_blocked", agent_id=profile.agent_id)
        except Exception as e:
            logger.error("guardrail_output_check_failed", error=str(e))
