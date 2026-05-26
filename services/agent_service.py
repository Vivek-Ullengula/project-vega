# coaction_agent_platform/services/agent_service.py
"""Orchestration service: load profile → init agent → execute → return response."""

import os
import uuid
import structlog
from collections.abc import AsyncIterator
from fastapi import HTTPException

from domain.models import (
    AgentInvocationRequest,
    AgentInvocationResponse,
    ExecutionProfile,
    ModelProfile,
    RetrievalProfile,
    MemoryProfile,
    IdentityContext,
)
from agents.underwriting_agent import UnderwritingAgent
from adapters.aws.dynamodb import DynamoDBAdapter
from services.authorization import AuthorizationService

logger = structlog.get_logger(__name__)

FORCED_MODEL_ID = "gpt-5.2"


class AgentService:
    """Central orchestrator for agent invocations.

    Responsibilities:
    1. Load ExecutionProfile for the requested agent_id
    2. Initialize/cache the UnderwritingAgent
    3. Load session history from DynamoDB
    4. Execute the query
    5. Save session state back to DynamoDB
    6. Return structured AgentInvocationResponse
    """

    def __init__(self, dynamodb: DynamoDBAdapter, region: str = "us-east-1"):
        self.dynamodb = dynamodb
        self.region = region
        self._agents: dict[str, UnderwritingAgent] = {}
        self._profiles: dict[str, ExecutionProfile] = {}

    @staticmethod
    def _pin_profile_model(profile: ExecutionProfile) -> ExecutionProfile:
        """Force the runtime onto the single supported model."""
        if profile.model_profile.model_id != FORCED_MODEL_ID:
            logger.info(
                "model_profile_pinned",
                from_model=profile.model_profile.model_id,
                to_model=FORCED_MODEL_ID,
            )
            profile.model_profile.model_id = FORCED_MODEL_ID
        return profile

    @staticmethod
    def _serialize_citations(citations: list) -> list[dict]:
        serialized_citations = []
        for citation in citations:
            if hasattr(citation, "model_dump"):
                serialized_citations.append(citation.model_dump())
            elif isinstance(citation, dict):
                serialized_citations.append(citation)
        return serialized_citations

    async def _load_profile(self, agent_id: str) -> ExecutionProfile:
        """Load an ExecutionProfile using the standard resolution chain.

        Resolution order (via ExecutionProfileRepository):
        1. In-memory cache
        2. DynamoDB (PROFILE#<agent_id> / VERSION#latest)
        3. Disk scan — all JSON files in profiles/ matched by agent_id field
        4. Environment variable fallback (safety net)
        """
        if agent_id in self._profiles:
            return self._profiles[agent_id]

        # Try ExecutionProfileRepository (DynamoDB → disk scan)
        try:
            from control_plane.execution_profile_repository import ExecutionProfileRepository

            repo = ExecutionProfileRepository(dynamodb_adapter=self.dynamodb, config_dir="profiles")
            profile = await repo.get_profile(agent_id)
            profile = self._pin_profile_model(profile)

            self._profiles[agent_id] = profile
            return profile
        except Exception as e:
            logger.debug("profile_repo_fallback", agent_id=agent_id, reason=str(e))

        # Fallback: try direct file load by filename
        profile = None
        import json
        from pathlib import Path

        profile_path = Path("profiles") / f"{agent_id}.json"
        if profile_path.exists():
            try:
                raw = json.loads(profile_path.read_text())
                profile = ExecutionProfile(**raw)
                profile = self._pin_profile_model(profile)
                logger.info(
                    "profile_loaded_from_file",
                    agent_id=agent_id,
                    path=str(profile_path),
                )
            except Exception as e:
                logger.warning(
                    "profile_file_parse_error",
                    path=str(profile_path),
                    error=str(e),
                )

        # Last resort: environment variables
        if not profile:
            kb_id_raw = os.getenv("BEDROCK_KB_ID", "2KMBSFAGGS")
            kb_ids = [kid.strip() for kid in kb_id_raw.split(",") if kid.strip()]
            profile = ExecutionProfile(
                agent_id=agent_id,
                version="1.0",
                prompt_template_id="underwriting_system_v1",
                model_profile=ModelProfile(
                    model_id=FORCED_MODEL_ID,
                    temperature=0.6,
                    max_tokens=4096,
                ),
                retrieval_profile=RetrievalProfile(
                    knowledge_base_ids=kb_ids,
                ),
                memory_profile=MemoryProfile(),
            )
            logger.warning(
                "using_default_profile",
                agent_id=agent_id,
                kb_ids=kb_ids,
                model_id=FORCED_MODEL_ID,
                msg="No stored profile found; using env/defaults.",
            )

        profile = self._pin_profile_model(profile)
        self._profiles[agent_id] = profile
        return profile

    def _get_or_create_agent(self, agent_id: str, profile: ExecutionProfile) -> UnderwritingAgent:
        """Get or create a cached UnderwritingAgent."""
        if agent_id not in self._agents:
            self._agents[agent_id] = UnderwritingAgent(profile=profile, region=self.region)
        return self._agents[agent_id]

    def reload_agent(self, agent_id: str) -> None:
        """Force reload an agent (e.g., after profile update)."""
        self._profiles.pop(agent_id, None)
        self._agents.pop(agent_id, None)
        logger.info("agent_reloaded", agent_id=agent_id)

    def _persist_and_build_response(
        self,
        *,
        request: AgentInvocationRequest,
        identity: IdentityContext,
        session_id: str,
        session_data: dict | None,
        history: list[dict],
        result: dict,
        agent: UnderwritingAgent,
    ) -> AgentInvocationResponse:
        """Persist a completed invocation and build the API response."""
        updated_messages = list(history)
        updated_messages.append({"role": "user", "content": request.input_text})

        updated_messages.append(
            {
                "role": "assistant",
                "content": result["answer"],
                "citations": self._serialize_citations(result.get("citations", [])),
            }
        )

        title = (
            request.input_text[:80]
            if len(updated_messages) <= 2
            else (
                session_data.get("title", request.input_text[:80])
                if session_data
                else request.input_text[:80]
            )
        )

        self.dynamodb.save_session(
            user_id=identity.user_id,
            session_id=session_id,
            title=title,
            messages=updated_messages,
        )

        return AgentInvocationResponse(
            status="success",
            answer=result["answer"],
            citations=result.get("citations", []),
            session_id=session_id,
            correlation_id=identity.correlation_id,
            model_id=None,
            metadata={
                "follow_up_questions": result.get("follow_up_questions", []),
                "sources": result.get("sources", []),
                "top_k": request.top_k,
            },
        )

    async def invoke(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
    ) -> AgentInvocationResponse:
        """Invoke an agent with the user's query.

        Full lifecycle:
        1. Load agent (with cached ExecutionProfile)
        2. Load session history from DynamoDB
        3. Execute the query
        4. Save updated session to DynamoDB
        5. Return structured response
        """
        agent_id = request.agent_id
        session_id = request.session_id or str(uuid.uuid4())
        user_id = identity.user_id
        role = identity.roles[0] if identity.roles else "agent"

        logger.info(
            "agent_invocation_start",
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
        )

        try:
            profile = await self._load_profile(agent_id)
            await AuthorizationService().authorize_invocation(identity, profile)
            agent = self._get_or_create_agent(agent_id, profile)

            # Load session history from DynamoDB
            history = []
            session_data = self.dynamodb.get_session(user_id, session_id)
            if session_data:
                history = session_data.get("messages", [])
                logger.info("session_history_loaded", count=len(history))

            # Execute the agent
            result = await agent.invoke(
                query=request.input_text,
                role=role,
                history=history,
                model_id=None,
                top_k=request.top_k,
            )

            return self._persist_and_build_response(
                request=request,
                identity=identity,
                session_id=session_id,
                session_data=session_data,
                history=history,
                result=result,
                agent=agent,
            )

        except HTTPException as e:
            logger.warning(
                "agent_invocation_blocked",
                agent_id=agent_id,
                status_code=e.status_code,
                detail=e.detail,
            )
            return AgentInvocationResponse(
                status="blocked" if e.status_code == 403 else "error",
                answer=str(e.detail),
                session_id=session_id,
                correlation_id=identity.correlation_id,
            )
        except Exception as e:
            logger.error(
                "agent_invocation_failed",
                agent_id=agent_id,
                error=str(e),
            )
            return AgentInvocationResponse(
                status="error",
                answer="Sorry, I couldn't complete that request. Please try again or contact support if it continues.",
                session_id=session_id,
                correlation_id=identity.correlation_id,
            )

    async def stream_invoke(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
    ) -> AsyncIterator[dict[str, object]]:
        """Stream an invocation as display deltas followed by a final response."""
        agent_id = request.agent_id
        session_id = request.session_id or str(uuid.uuid4())
        user_id = identity.user_id
        role = identity.roles[0] if identity.roles else "agent"

        logger.info(
            "agent_stream_invocation_start",
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
            role=role,
        )

        yield {"type": "session", "session_id": session_id}

        try:
            profile = await self._load_profile(agent_id)
            await AuthorizationService().authorize_invocation(identity, profile)
            agent = self._get_or_create_agent(agent_id, profile)

            history = []
            session_data = self.dynamodb.get_session(user_id, session_id)
            if session_data:
                history = session_data.get("messages", [])
                logger.info("session_history_loaded", count=len(history))

            final_result: dict | None = None
            async for event in agent.stream(
                query=request.input_text,
                role=role,
                history=history,
                model_id=None,
                top_k=request.top_k,
            ):
                if event.get("type") == "delta":
                    yield {"type": "delta", "text": event.get("text", "")}
                elif event.get("type") == "final":
                    final_result = (
                        event.get("result") if isinstance(event.get("result"), dict) else None
                    )

            if final_result is None:
                raise RuntimeError("Streaming invocation completed without a final result.")

            response = self._persist_and_build_response(
                request=request,
                identity=identity,
                session_id=session_id,
                session_data=session_data,
                history=history,
                result=final_result,
                agent=agent,
            )
            yield {"type": "final", "response": response.model_dump(mode="json")}

        except HTTPException as e:
            logger.warning(
                "agent_stream_invocation_blocked",
                agent_id=agent_id,
                status_code=e.status_code,
                detail=e.detail,
            )
            response = AgentInvocationResponse(
                status="blocked" if e.status_code == 403 else "error",
                answer=str(e.detail),
                session_id=session_id,
                correlation_id=identity.correlation_id,
            )
            yield {"type": "final", "response": response.model_dump(mode="json")}
        except Exception as e:
            logger.error(
                "agent_stream_invocation_failed",
                agent_id=agent_id,
                error=str(e),
            )
            response = AgentInvocationResponse(
                status="error",
                answer=(
                    "Sorry, I couldn't complete that request. "
                    "Please try again or contact support if it continues."
                ),
                session_id=session_id,
                correlation_id=identity.correlation_id,
            )
            yield {"type": "final", "response": response.model_dump(mode="json")}
