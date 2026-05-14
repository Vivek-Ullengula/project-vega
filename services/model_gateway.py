# coaction_agent_platform/services/model_gateway.py
"""Bedrock model gateway per HLD Section 8.

Wraps Strands agent invocation as the model gateway.
"""

import structlog
from typing import Any

from strands import Agent
from strands.models.bedrock import BedrockModel

from domain.models import (
    AgentInvocationRequest,
    ExecutionProfile,
    IdentityContext,
    SourceCitation,
)
from agents.prompts import get_prompt
from agents.tools.retriever import (
    search_manuals,
    configure_retriever,
    get_last_retrieval_sources,
)
from agents.underwriting_agent import (
    _normalize_question,
    _extract_followups_from_text,
)

logger = structlog.get_logger(__name__)


class BedrockModelGateway:
    """Invokes the Strands agent with the Bedrock model specified in the ExecutionProfile."""

    def __init__(self, region: str = "us-east-1"):
        self.region = region
        self._agents: dict[str, Agent] = {}  # keyed by profile version + role

    def _build_agent(self, profile: ExecutionProfile, role: str) -> Agent:
        """Build a Strands Agent from an ExecutionProfile."""
        mp = profile.model_profile

        model = BedrockModel(
            model_id=mp.model_id,
            region_name=self.region,
            temperature=mp.temperature,
            max_tokens=mp.max_tokens or 4096,
        )

        # Configure retriever with KB IDs
        if profile.retrieval_profile.enabled:
            configure_retriever(
                knowledge_base_ids=profile.retrieval_profile.knowledge_base_ids,
                region=self.region,
            )

        prompt = get_prompt(profile.prompt_template_id, role)

        return Agent(
            model=model,
            system_prompt=prompt,
            tools=[search_manuals],
        )

    def _get_or_create_agent(self, profile: ExecutionProfile, role: str) -> Agent:
        """Get or create a cached agent for a profile+role combination."""
        key = f"{profile.agent_id}:{profile.version}:{role}"
        if key not in self._agents:
            self._agents[key] = self._build_agent(profile, role)
        return self._agents[key]

    async def invoke(
        self,
        request: AgentInvocationRequest,
        identity: IdentityContext,
        profile: ExecutionProfile,
        memory_context: dict | None = None,
    ) -> dict[str, Any]:
        """Invoke the Strands agent and return structured results."""
        role = identity.roles[0] if identity.roles else "agent"
        agent = self._get_or_create_agent(profile, role)

        # Restore conversation history from memory
        if memory_context and memory_context.get("messages"):
            agent.state.messages = list(memory_context["messages"])

        # Execute
        import re

        response = agent(request.input_text)
        answer = str(response)

        # Extract follow-up questions
        follow_up_questions = []
        fu_pattern = r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}"
        clean_answer = answer

        if re.search(fu_pattern, answer):
            parts = re.split(fu_pattern, answer, maxsplit=1)
            clean_answer = parts[0].strip()
            fu_text = parts[1]
            matches = re.findall(r"\d+\.\s*(.+)", fu_text)
            raw_followups = [m.strip() for m in matches if m.strip()]

            # Dedup against history
            historical_questions: set[str] = set()
            history = (memory_context or {}).get("messages", [])
            for msg in history:
                msg_role = (msg.get("role") or "").strip().lower()
                content = msg.get("content") or ""
                if msg_role == "user":
                    nq = _normalize_question(content)
                    if nq:
                        historical_questions.add(nq)
                elif msg_role == "assistant":
                    for prev_fu in _extract_followups_from_text(content):
                        nfu = _normalize_question(prev_fu)
                        if nfu:
                            historical_questions.add(nfu)

            seen: set[str] = set()
            for question in raw_followups:
                normalized = _normalize_question(question)
                if not normalized or normalized in historical_questions or normalized in seen:
                    continue
                seen.add(normalized)
                follow_up_questions.append(question)
                if len(follow_up_questions) == 3:
                    break

        # Get source citations
        retrieval_sources = get_last_retrieval_sources()
        all_urls = [s["url"] for s in retrieval_sources if s.get("url") and s["url"] != "N/A"]
        
        # Parse the <used_sources> hidden XML block
        used_sources_match = re.search(r"<used_sources>\s*(.*?)\s*</used_sources>", clean_answer, re.DOTALL | re.IGNORECASE)
        
        cited_urls = []
        if used_sources_match:
            raw_urls = used_sources_match.group(1).strip().split("\n")
            # Exact match against retrieved URLs to avoid hallucinated links
            for raw_url in raw_urls:
                clean_url = raw_url.strip()
                if clean_url in all_urls:
                    cited_urls.append(clean_url)
            
            # Remove the hidden block from the final answer text
            clean_answer = re.sub(r"<used_sources>.*?</used_sources>", "", clean_answer, flags=re.DOTALL | re.IGNORECASE).strip()
        else:
            # Fallback if model fails to output XML: look for inline URLs
            cited_urls = [url for url in all_urls if url in clean_answer]
            
        # If still no citations, fallback to the top 3 retrieved (assuming they were implicitly used)
        sources = cited_urls if cited_urls else all_urls[:3]

        # Build citation objects
        citations = []
        seen_urls = set()
        for src in retrieval_sources:
            url = src.get("url", "")
            if url in sources and url not in seen_urls:
                seen_urls.add(url)
                citations.append(
                    SourceCitation(
                        source_id=url,
                        title=src.get("heading", ""),
                        uri=url,
                    )
                )

        # Current messages for session persistence
        current_messages = agent.state.messages if hasattr(agent.state, "messages") else []

        return {
            "answer": clean_answer,
            "citations": citations,
            "follow_up_questions": follow_up_questions,
            "sources": sources,
            "agent_messages": current_messages,
        }
