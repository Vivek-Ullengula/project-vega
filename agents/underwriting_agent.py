# agents/underwriting_agent.py
"""Strands-based underwriting agent — fully configurable via ExecutionProfile."""

import re
import structlog
from typing import Any

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.agent.conversation_manager import NullConversationManager

from domain.models import (
    ExecutionProfile,
    SourceCitation,
)
from agents.prompts import get_prompt
from agents.tools.retriever import (
    search_manuals,
    configure_retriever,
    get_last_retrieval_sources,
    clear_retrieval_sources,
)

logger = structlog.get_logger(__name__)


class SafeBedrockModel(BedrockModel):
    """BedrockModel wrapper that strips reasoningContent blocks.
    
    Some models (e.g. openai.gpt-oss-safeguard-120b) don't support the
    reasoningContent.reasoningText.signature field. The strands SDK may inject
    these blocks during multi-turn tool-call loops, causing ValidationException.
    This subclass intercepts the converse call and strips them out.
    """

    @staticmethod
    def _strip_reasoning(messages: Any) -> list[Any]:
        """Remove reasoningContent blocks from message content arrays."""
        if not isinstance(messages, list):
            return messages
        cleaned: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                cleaned.append(msg)
                continue
            content = msg.get("content")
            if isinstance(content, list):
                filtered = [
                    block for block in content
                    if not (isinstance(block, dict) and "reasoningContent" in block)
                ]
                if filtered:
                    cleaned.append({**msg, "content": filtered})
            else:
                cleaned.append(msg)
        return cleaned

    def format_request(  # type: ignore[override]
        self,
        messages: Any,
        tool_specs: Any = None,
        system_prompt: Any = None,
    ) -> dict[str, Any]:
        """Override to strip reasoning blocks before formatting the request."""
        clean_messages = self._strip_reasoning(messages)
        return super().format_request(clean_messages, tool_specs, system_prompt)


def _normalize_question(text: str) -> str:
    """Normalize question text for stable dedup checks."""
    if not text:
        return ""
    normalized = text.strip().lower()
    normalized = re.sub(r"^\d+\.\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"[^\w\s]", "", normalized)
    return normalized


def _extract_followups_from_text(content: str) -> list[str]:
    """Extract follow-up questions from assistant response text."""
    if not content:
        return []
    fu_pattern = r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}"
    if not re.search(fu_pattern, content):
        return []
    section = re.split(fu_pattern, content, maxsplit=1)[1]
    matches = re.findall(r"\d+\.\s*(.+)", section)
    return [m.strip() for m in matches if m.strip()]


class UnderwritingAgent:
    """Configurable Strands Agent for Coaction underwriting queries.

    Initialized from an ExecutionProfile, which determines:
    - Which Bedrock model to use
    - Which Knowledge Bases to query
    - What system prompt template to apply
    """

    def __init__(self, profile: ExecutionProfile, region: str = "us-east-1"):
        self.profile = profile
        self.region = region
        self._agents: dict[str, Agent] = {}  # keyed by role

        # Configure the retriever tool with KB IDs from the profile
        configure_retriever(
            knowledge_base_ids=profile.retrieval_profile.knowledge_base_ids,
            region=region,
        )

        logger.info(
            "underwriting_agent_initialized",
            agent_id=profile.agent_id,
            model=profile.model_profile.model_id,
            kb_ids=profile.retrieval_profile.knowledge_base_ids,
        )

    def _build_agent(self, role: str, messages: list[dict] | None = None) -> Agent:
        """Build a Strands Agent for the given user role.
        
        Args:
            role: User role for prompt selection.
            messages: Optional conversation history to pre-load.
        """
        mp = self.profile.model_profile

        model = SafeBedrockModel(
            model_id=mp.model_id,
            region_name=self.region,
            temperature=mp.temperature,
            max_tokens=mp.max_tokens or 4096,
        )

        prompt = get_prompt(self.profile.prompt_template_id, role)

        # Build the messages list for history restoration.
        # Each message must be in Bedrock converse format:
        #   {"role": "user"|"assistant", "content": [{"text": "..."}]}
        restored_messages: list[Any] = []
        if messages:
            for msg in messages:
                if isinstance(msg, dict):
                    msg_role = msg.get("role", "user")
                    msg_content = msg.get("content", "")
                    # Skip system messages — Bedrock doesn't accept them in messages array
                    if msg_role == "system":
                        continue
                    # Normalize content to Bedrock converse format
                    if isinstance(msg_content, str):
                        restored_messages.append({
                            "role": msg_role,
                            "content": [{"text": msg_content}],
                        })
                    elif isinstance(msg_content, list):
                        # Strip any reasoningContent blocks from content list
                        clean_content = [
                            block for block in msg_content
                            if not (isinstance(block, dict) and "reasoningContent" in block)
                        ]
                        if clean_content:
                            restored_messages.append({
                                "role": msg_role,
                                "content": clean_content,
                            })
                    else:
                        restored_messages.append({
                            "role": msg_role,
                            "content": [{"text": str(msg_content)}],
                        })

        return Agent(
            model=model,
            system_prompt=prompt,
            tools=[search_manuals],
            conversation_manager=NullConversationManager(),
            messages=restored_messages if restored_messages else None,
        )


    async def invoke(
        self,
        query: str,
        role: str = "agent",
        history: list[dict] | None = None,
    ) -> dict:
        """Invoke the agent with a query."""
        # Clear stale retrieval sources before each invocation
        clear_retrieval_sources()

        # Build a fresh agent with conversation history pre-loaded
        agent = self._build_agent(role, messages=history)

        # Execute the agent (synchronous Strands call)
        response = agent(query)
        raw_answer = str(response)

        # ─── STEP 1: Extract <used_sources> from the RAW answer ─────────
        # This MUST happen first because follow-up extraction will strip
        # the tail of the answer where <used_sources> lives.
        retrieval_sources = get_last_retrieval_sources()
        url_to_meta: dict[str, dict] = {}
        for src in retrieval_sources:
            url = (src.get("url") or "").strip().rstrip("/")
            if url and url != "N/A":
                url_to_meta[url] = src
        all_urls = list(url_to_meta.keys())

        used_sources_match = re.search(
            r"<used_sources>\s*(.*?)\s*</used_sources>",
            raw_answer, re.DOTALL | re.IGNORECASE,
        )

        cited_urls: list[str] = []
        if used_sources_match:
            raw_urls = used_sources_match.group(1).strip().split("\n")
            for raw_url in raw_urls:
                clean_url = raw_url.strip().rstrip("/")
                if not clean_url:
                    continue
                if clean_url in url_to_meta:
                    cited_urls.append(clean_url)
                elif clean_url.startswith("https://bindingauthority.coactionspecialty.com/"):
                    cited_urls.append(clean_url)
                    logger.info("citation_accepted_without_retriever_match", url=clean_url)

            # Remove the hidden block from the answer text
            answer = re.sub(
                r"<used_sources>.*?</used_sources>", "",
                raw_answer, flags=re.DOTALL | re.IGNORECASE,
            ).strip()
        else:
            # Fallback: look for inline URLs in the raw answer
            cited_urls = [url for url in all_urls if url in raw_answer]
            answer = raw_answer

        logger.info(
            "citation_resolution",
            cited_count=len(cited_urls),
            retriever_count=len(all_urls),
            cited_urls=cited_urls,
        )

        # ─── STEP 2: Extract follow-up questions ────────────────────────
        follow_up_questions: list[str] = []
        fu_pattern = r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}"
        if re.search(fu_pattern, answer):
            parts = re.split(fu_pattern, answer, maxsplit=1)
            clean_answer = parts[0].strip()
            fu_text = parts[1]
            matches = re.findall(r"\d+\.\s*(.+)", fu_text)
            raw_followups = [m.strip() for m in matches if m.strip()]

            # Dedup against history
            historical_questions: set[str] = set()
            if history:
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

            answer = clean_answer

        logger.info("follow_up_extraction", count=len(follow_up_questions), questions=follow_up_questions)

        # ─── STEP 3: Build citation objects ──────────────────────────────
        sources = cited_urls
        citations: list[SourceCitation] = []
        seen_urls: set[str] = set()

        for url in sources:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            meta = url_to_meta.get(url)
            if meta:
                citations.append(
                    SourceCitation(
                        source_id=url,
                        title=meta.get("heading", "") or url,
                        uri=url,
                        manual_name=meta.get("manual_name", ""),
                    )
                )
            else:
                # Build citation from URL pattern
                filename = url.rstrip("/").split("/")[-1].replace(".html", "")
                if filename.isdigit():
                    title = f"Class Code {filename}"
                    manual_name = "General Liability Manual"
                elif filename == "guide":
                    title = "Class Codes"
                    manual_name = "General Liability Guide Manual"
                elif "property" in filename.lower():
                    title = filename.replace("-", " ").replace("_", " ").title()
                    manual_name = "Property Manual"
                else:
                    title = filename.replace("-", " ").replace("_", " ").title()
                    manual_name = "Binding Authority Manual"
                citations.append(
                    SourceCitation(
                        source_id=url,
                        title=title,
                        uri=url,
                        manual_name=manual_name,
                    )
                )

        logger.info("citations_built", count=len(citations))

        return {
            "answer": answer,
            "citations": citations,
            "follow_up_questions": follow_up_questions,
            "sources": sources,
        }

