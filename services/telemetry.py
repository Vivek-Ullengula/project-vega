# coaction_agent_platform/services/telemetry.py
"""CloudWatch telemetry emitter per HLD Section 12.

First release: CloudWatch only. Raw prompts and responses are NOT logged.
"""

import structlog
from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    ExecutionProfile,
)

logger = structlog.get_logger(__name__)


class CloudWatchTelemetryEmitter:
    """Emits invocation metrics and traces to CloudWatch.

    Per HLD Section 12, emits structured metadata only — no raw prompts
    or raw responses are logged by default.
    """

    def __init__(self, boto3_factory=None):
        self.boto3_factory = boto3_factory
        self.client = boto3_factory.client("cloudwatch") if boto3_factory else None

    async def emit_invocation(
        self,
        request: AgentInvocationRequest,
        response: AgentInvocationResponse,
        profile: ExecutionProfile,
    ) -> None:
        """Emit structured telemetry for an agent invocation."""
        telemetry_event = {
            "agent_id": request.agent_id,
            "agent_version": profile.version,
            "status": response.status,
            "model_id": response.model_id,
            "citation_count": len(response.citations),
            "tool_count": len(response.tool_results),
            "session_id": response.session_id,
            "correlation_id": response.correlation_id,
        }

        if profile.observability_profile.emit_metrics and self.client:
            try:
                self.client.put_metric_data(
                    Namespace="CoactionAgentPlatform",
                    MetricData=[
                        {
                            "MetricName": "AgentInvocation",
                            "Dimensions": [
                                {"Name": "AgentId", "Value": request.agent_id},
                                {"Name": "Status", "Value": response.status},
                            ],
                            "Value": 1,
                            "Unit": "Count",
                        },
                    ],
                )
            except Exception as e:
                logger.error("telemetry_emit_failed", error=str(e))

        # Always emit structured log (structlog → CloudWatch Logs)
        logger.info("agent_invocation_telemetry", **telemetry_event)
