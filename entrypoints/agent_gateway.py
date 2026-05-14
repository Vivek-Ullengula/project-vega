"""
Native AgentCore Runtime entrypoint loop.
Acts as a lean event listener that receives cloud invocation payloads and
delegates execution directly to the shared RuntimeOrchestrator pipeline.
Maintains zero standalone orchestration, middleware, or auth logic.
"""

import os
import sys
import uuid
import asyncio
import logging

from dotenv import load_dotenv
from bedrock_agentcore.runtime import BedrockAgentCoreApp

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.models import AgentInvocationRequest, IdentityContext
from app.dependencies.services import get_orchestrator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent_gateway")

load_dotenv()

AGENT_ID = os.environ.get("AGENT_ID", "vega_binding_authority_bot")

app = BedrockAgentCoreApp()


@app.entrypoint
def invoke(payload: dict) -> dict:
    """Handle native Bedrock AgentCore invocation events."""
    user_message = payload.get("prompt", "")
    session_id = payload.get("session_id", str(uuid.uuid4()))
    user_id = payload.get("user_id", "anonymous")

    if not user_message:
        return {"status": "error", "answer": "Missing required field: prompt"}

    logger.info(f"AgentCore entrypoint routing invoke for agent: {AGENT_ID}")

    request = AgentInvocationRequest(
        agent_id=AGENT_ID,
        input_text=user_message,
        session_id=session_id,
        channel="agentcore",
        request_metadata=payload,
    )

    identity = IdentityContext(
        user_id=user_id,
        roles=["underwriter"],
        channel="agentcore",
        correlation_id=payload.get("correlation_id", str(uuid.uuid4())),
    )

    try:
        orchestrator = get_orchestrator()

        # Execute shared orchestration pipeline asynchronously
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        response = loop.run_until_complete(orchestrator.execute(request, identity))

        # Convert Pydantic response model to standard dict serializable payload
        return response.model_dump()

    except Exception as e:
        logger.exception("AgentCore SDK entrypoint unhandled orchestration crash")
        return {
            "status": "error",
            "answer": f"Internal Gateway Error: {str(e)}",
            "session_id": session_id,
            "agent_id": AGENT_ID,
        }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(
        f"Starting specialized AgentCore SDK listener loop for agent: {AGENT_ID} on port {port}"
    )
    app.run(port=port)
