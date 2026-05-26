# agents/underwriting_agent.py
"""Strands-based underwriting agent — fully configurable via ExecutionProfile."""

import json
import re
import structlog
from collections.abc import AsyncIterator
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
    build_scoped_search_manuals_tool,
    configure_retriever,
    set_retriever_context,
    reset_retriever_context,
    get_last_retrieval_sources,
    clear_retrieval_sources,
)

logger = structlog.get_logger(__name__)

UNDERWRITER_GUIDANCE = "For authoritative guidance, please contact your Coaction underwriter."
CITATION_LIMIT = 3
STREAM_HOLDBACK_CHARS = 160
STREAM_STOP_PATTERNS = [
    r"\*{0,2}\s*You might also want to ask:?\s*\*{0,2}",
    r"\*{0,2}\s*Follow-up questions:?\s*\*{0,2}",
    r"\*{0,2}\s*Recommended questions:?\s*\*{0,2}",
    r"\*{0,2}\s*Suggested questions:?\s*\*{0,2}",
    r"\*{0,2}\s*Other questions you may ask:?\s*\*{0,2}",
    r"<used_sources\b",
]
OFF_TOPIC_RESPONSE = (
    "I can only answer binding authority and underwriting related questions. "
    "How can I help you with insurance today?"
)
INSURANCE_SCOPE_TERMS = {
    "insurance",
    "underwriting",
    "underwriter",
    "binding authority",
    "coaction",
    "coverage",
    "manual",
    "guideline",
    "policy",
    "endorsement",
    "form",
    "class code",
    "general liability",
    "property",
    "business income",
    "gl ",
    "cg ",
    "bp ",
    "cp ",
    "il ",
}
GENERIC_REWRITE_TERMS = {
    "rephrase",
    "rewrite",
    "paraphrase",
    "polish",
    "summarize",
    "grammar",
    "translate",
}
COVERAGE_AVAILABILITY_TERMS = (
    "extended period of indemnity",
    "business income",
    "contractor pak",
    "builders risk",
    "agreed value",
    "ordinance or law",
    "inland marine pac",
    "outdoor property",
    "property in the open",
)


class SafeBedrockModel(BedrockModel):
    """BedrockModel wrapper that strips reasoningContent blocks.

    Some models (e.g. openai.gpt-oss-safeguard-120b) don't support the
    reasoningContent.reasoningText.signature field. The strands SDK may inject
    these blocks during multi-turn tool-call loops, causing ValidationException.
    This subclass intercepts the converse call and strips them out.
    """

    @staticmethod
    def _strip_reasoning(messages: Any) -> list[Any]:
        """Remove reasoningContent blocks and intermediate assistant text thoughts."""
        if not isinstance(messages, list):
            return messages
        cleaned: list[Any] = []
        for msg in messages:
            if not isinstance(msg, dict):
                cleaned.append(msg)
                continue
            content = msg.get("content")
            role = msg.get("role")
            if isinstance(content, list):
                # 1. Filter out native reasoningContent blocks
                filtered = [
                    block
                    for block in content
                    if not (isinstance(block, dict) and "reasoningContent" in block)
                ]
                # 2. For assistant messages that contain a toolUse block,
                # strip out any text blocks (model's intermediate thoughts/reasoning text)
                if role == "assistant" and any(
                    isinstance(b, dict) and "toolUse" in b for b in filtered
                ):
                    filtered = [b for b in filtered if not (isinstance(b, dict) and "text" in b)]
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


def _has_insurance_scope_signal(text: str) -> bool:
    """Return whether the request appears related to the underwriting assistant domain."""
    normalized = f" {text.strip().lower()} "
    if re.search(r"\b[A-Z]{2}\s*[-]?\s*\d{3,4}(?:\s*[-]?\s*\d{2,4})?\b", text.upper()):
        return True
    return any(term in normalized for term in INSURANCE_SCOPE_TERMS)


def _is_generic_rewrite_request(text: str) -> bool:
    """Block generic editing requests unless they are clearly insurance-related."""
    normalized = text.strip().lower()
    if not any(term in normalized for term in GENERIC_REWRITE_TERMS):
        return False
    return not _has_insurance_scope_signal(text)


def _coverage_terms_from_query(query: str) -> list[str]:
    """Extract specific coverage terms that must be explicitly present in retrieved evidence."""
    normalized = query.lower()
    terms = [term for term in COVERAGE_AVAILABILITY_TERMS if term in normalized]
    if "extended period" in normalized and "extended period of indemnity" not in terms:
        terms.append("extended period of indemnity")
    return terms


def _retrieval_evidence_text(retrieval_sources: list[dict]) -> str:
    """Build normalized evidence text from retrieved source metadata."""
    parts: list[str] = []
    for src in retrieval_sources:
        for key in ("heading", "manual_name", "content_text", "snippet"):
            value = src.get(key)
            if value:
                parts.append(str(value))
    return " ".join(parts).lower()


def _asks_for_availability(query: str) -> bool:
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in (
            "do we offer",
            "do we provide",
            "is there",
            "is it available",
            "available",
            "offer",
            "provide",
            "include",
            "includes",
            "coverage option",
        )
    )


def _asks_where_mentioned(query: str) -> bool:
    normalized = query.lower()
    return any(word in normalized for word in ("where", "mentioned", "listed", "shown"))


def _is_affirmative_answer(answer: str) -> bool:
    normalized = answer.strip().lower()
    return normalized.startswith(("yes", "yes.", "yes,", "the manual includes")) or any(
        phrase in normalized
        for phrase in (
            " is available",
            " are available",
            " as an available",
            " includes ",
            " is listed",
            " listed as",
            " we offer",
        )
    )


def _enforce_explicit_evidence(
    *,
    answer: str,
    query: str,
    retrieval_sources: list[dict],
) -> tuple[str, bool]:
    """Prevent affirmative availability/location answers without exact retrieved evidence.

    Returns the possibly replaced answer and whether citations should be suppressed.
    """
    terms = _coverage_terms_from_query(query)
    if not terms:
        return answer, False

    evidence = _retrieval_evidence_text(retrieval_sources)
    missing_terms = [term for term in terms if term not in evidence]
    if not missing_terms:
        return answer, False

    if _asks_for_availability(query) and _is_affirmative_answer(answer):
        term = missing_terms[0]
        return (
            f"I could not confirm that {term} is offered from the retrieved manual content. "
            f"The exact coverage term was not found in the retrieved manual excerpts.\n\n"
            f"{UNDERWRITER_GUIDANCE}",
            True,
        )

    if _asks_where_mentioned(query):
        term = missing_terms[0]
        return (
            f"I could not find {term} in the retrieved manual content, so I cannot point to "
            f"where it is mentioned in the manual.\n\n{UNDERWRITER_GUIDANCE}",
            True,
        )

    return answer, False


def _openai_generation_params(model_id: str, temperature: float, max_tokens: int) -> dict[str, Any]:
    """Build OpenAI Chat Completions params for legacy and newer model families."""
    normalized = model_id.lower()
    uses_completion_tokens = normalized.startswith(("gpt-5", "o1", "o3", "o4"))
    token_param = "max_completion_tokens" if uses_completion_tokens else "max_tokens"
    if normalized.startswith(("o1", "o3", "o4")):
        return {token_param: max_tokens}
    return {
        "temperature": temperature,
        token_param: max_tokens,
    }


def select_best_citations(cited_urls: list[str], query: str) -> list[str]:
    """Intelligently filter and prioritize citations per user specifications:
    - Default to a single absolute most relevant citation for most queries.
    - Keep up to 3 citations only for comparison or compound queries.
    - Prioritize class codes (numeric filename) as the top citation.
    """
    if not cited_urls:
        return []

    # Deduplicate while preserving order
    unique_urls = []
    for u in cited_urls:
        if u not in unique_urls:
            unique_urls.append(u)

    # Classify URLs (class codes are purely numeric filenames)
    class_code_urls = []
    other_urls = []
    for url in unique_urls:
        filename = url.rstrip("/").split("/")[-1].replace(".html", "")
        if filename.isdigit():
            class_code_urls.append(url)
        else:
            other_urls.append(url)

    # Normalize query
    query_lower = query.lower()

    # Determine if comparison or compound query
    needs_multiple = (
        any(
            w in query_lower
            for w in [
                "compare",
                "difference",
                "versus",
                "vs",
                "both",
                "multiple",
                "list of",
                "all classes",
                "and",
                "or",
            ]
        )
        and len(unique_urls) > 1
    )

    if needs_multiple:
        # Return up to 3 citations, prioritizing class codes first
        combined = class_code_urls + other_urls
        return combined[:CITATION_LIMIT]
    else:
        # Single citation preferred
        # A. If the query mentions a specific class code, try to find a matching URL
        class_codes_in_query = re.findall(r"\b\d{5}\b", query)
        if class_codes_in_query:
            for code in class_codes_in_query:
                for url in unique_urls:
                    if code in url:
                        return [url]

        # B. Prioritize any class code URL if retrieved
        if class_code_urls:
            return [class_code_urls[0]]

        # C. Default to the absolute top/first citation
        return [unique_urls[0]]


def _strip_used_sources_block(raw_answer: str) -> tuple[str, str | None]:
    """Remove the hidden citation block and return its raw contents."""
    used_sources_match = re.search(
        r"<used_sources>\s*(.*?)\s*</used_sources>",
        raw_answer,
        re.DOTALL | re.IGNORECASE,
    )
    if not used_sources_match:
        dangling_match = re.search(
            r"<used_sources>\s*(.*)$",
            raw_answer,
            re.DOTALL | re.IGNORECASE,
        )
        if not dangling_match:
            return raw_answer, None
        answer = raw_answer[: dangling_match.start()].strip()
        return answer, dangling_match.group(1).strip()

    answer = re.sub(
        r"<used_sources>.*?</used_sources>",
        "",
        raw_answer,
        flags=re.DOTALL | re.IGNORECASE,
    ).strip()
    return answer, used_sources_match.group(1).strip()


def _normalize_underwriter_guidance(answer: str) -> str:
    """Normalize underwriter referral wording to the approved sentence."""
    if not re.search(r"\bCoaction underwriter\b", answer, flags=re.IGNORECASE):
        return answer

    normalized = re.sub(
        r"[^.!?\n]*\bCoaction underwriter\b[^.!?\n]*(?:[.!?]|$)",
        "",
        answer,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"[ \t]{2,}", " ", normalized).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)

    if not normalized:
        return UNDERWRITER_GUIDANCE
    return f"{normalized}\n\n{UNDERWRITER_GUIDANCE}"


def _strip_inline_source_markers(answer: str) -> str:
    """Remove inline [S1] source markers; the UI renders citations separately."""
    stripped = re.sub(r"(?:\s*\[S\d+\])+", "", answer, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", stripped).strip()


def _strip_thinking_blocks(answer: str) -> str:
    """Remove reasoning/thinking blocks from user-visible output."""
    return re.sub(r"<thinking>.*?</thinking>", "", answer, flags=re.DOTALL | re.IGNORECASE).strip()


def _stream_visible_answer(raw_answer: str, *, final: bool = False) -> str:
    """Return the safe user-visible prefix while a response is streaming."""
    visible = raw_answer
    for pattern in STREAM_STOP_PATTERNS:
        match = re.search(pattern, visible, flags=re.IGNORECASE)
        if match:
            visible = visible[: match.start()]
            break
    else:
        if not final:
            if len(visible) <= STREAM_HOLDBACK_CHARS:
                return ""
            visible = visible[:-STREAM_HOLDBACK_CHARS]

    visible = _strip_thinking_blocks(visible)
    visible = _strip_inline_source_markers(visible)
    return visible


def _build_public_source_maps(
    retrieval_sources: list[dict],
) -> tuple[dict[str, dict], dict[str, str], bool]:
    """Build source-id and URL lookup maps for retrieved public sources only."""
    source_id_to_meta: dict[str, dict] = {}
    url_to_source_id: dict[str, str] = {}
    retrieved_internal_guidelines = False

    for src in retrieval_sources:
        if src.get("manual_name") == "Internal Guidelines":
            retrieved_internal_guidelines = True
            continue

        url = (src.get("url") or "").strip().rstrip("/")
        source_id = (src.get("source_id") or "").strip()
        if not url or url == "N/A" or not source_id:
            continue

        normalized = {**src, "url": url, "source_id": source_id}
        source_id_to_meta[source_id] = normalized
        url_to_source_id[url] = source_id

    return source_id_to_meta, url_to_source_id, retrieved_internal_guidelines


def _parse_used_source_ids(
    used_sources_block: str | None,
    source_id_to_meta: dict[str, dict],
    url_to_source_id: dict[str, str],
) -> list[str]:
    """Parse a structured citation block and keep only retrieved public source IDs."""
    if used_sources_block is None:
        return []

    ordered_ids: list[str] = []

    def add_source_id(value: str | None) -> None:
        source_id = (value or "").strip().upper()
        if source_id in source_id_to_meta and source_id not in ordered_ids:
            ordered_ids.append(source_id)

    def add_url(value: str | None) -> None:
        url = (value or "").strip().strip("[]()\"'").rstrip("/")
        source_id = url_to_source_id.get(url)
        if source_id:
            add_source_id(source_id)

    def collect(item: Any) -> None:
        if isinstance(item, dict):
            add_source_id(
                item.get("source_id")
                or item.get("citation_id")
                or item.get("id")
                or item.get("source")
            )
            add_url(item.get("url") or item.get("uri"))
        elif isinstance(item, str):
            for match in re.findall(r"\bS\d+\b", item, flags=re.IGNORECASE):
                add_source_id(match)
            for match in re.findall(r"https?://[^\s\]\"')>,]+", item):
                add_url(match)

    try:
        parsed = json.loads(used_sources_block)
        if isinstance(parsed, dict):
            parsed = parsed.get("used_sources") or parsed.get("sources") or parsed.get("citations")
        if isinstance(parsed, list):
            for item in parsed:
                collect(item)
        else:
            collect(parsed)
    except json.JSONDecodeError:
        for raw_line in used_sources_block.splitlines():
            clean_line = re.sub(r"^[-*+\d\.\s]+", "", raw_line.strip())
            collect(clean_line)

    return ordered_ids[:CITATION_LIMIT]


def _conservative_citation_fallback(
    answer: str,
    source_id_to_meta: dict[str, dict],
) -> list[str]:
    """Cite a single retrieved public source only when it is the sole possible source."""
    if len(source_id_to_meta) != 1:
        return []
    normalized_answer = answer.strip().lower()
    if not normalized_answer or "i can only answer binding authority" in normalized_answer:
        return []
    if normalized_answer.startswith("hello!"):
        return []
    return [next(iter(source_id_to_meta))]


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
            reranking_enabled=profile.retrieval_profile.reranking_enabled,
        )

        logger.info(
            "underwriting_agent_initialized",
            agent_id=profile.agent_id,
            model=profile.model_profile.model_id,
            kb_ids=profile.retrieval_profile.knowledge_base_ids,
        )

    def _build_agent(
        self,
        role: str,
        messages: list[dict] | None = None,
        model_id: str | None = None,
        top_k: int = 5,
        retrieval_sources_sink: list[dict] | None = None,
    ) -> Agent:
        """Build a Strands Agent for the given user role.

        Args:
            role: User role for prompt selection.
            messages: Optional conversation history to pre-load.
            model_id: Optional model_id override.
        """
        mp = self.profile.model_profile
        effective_model_id = model_id or mp.model_id

        if effective_model_id.startswith("gpt-"):
            from strands.models.openai import OpenAIModel
            import os

            openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not openai_key:
                raise ValueError(
                    f"OpenAI model '{effective_model_id}' was selected but no OPENAI_API_KEY is configured. "
                    "Please set a valid OPENAI_API_KEY in your .env file or environment variables, "
                    "or choose a Bedrock model (e.g. Amazon Nova Pro) instead."
                )

            model = OpenAIModel(
                model_id=effective_model_id,
                client_args={"api_key": openai_key},
                params=_openai_generation_params(
                    effective_model_id,
                    temperature=mp.temperature,
                    max_tokens=mp.max_tokens or 4096,
                ),
            )
        else:
            model = SafeBedrockModel(
                model_id=effective_model_id,
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
                        restored_messages.append(
                            {
                                "role": msg_role,
                                "content": [{"text": msg_content}],
                            }
                        )
                    elif isinstance(msg_content, list):
                        # Strip any reasoningContent blocks from content list
                        clean_content = [
                            block
                            for block in msg_content
                            if not (isinstance(block, dict) and "reasoningContent" in block)
                        ]
                        if clean_content:
                            restored_messages.append(
                                {
                                    "role": msg_role,
                                    "content": clean_content,
                                }
                            )
                    else:
                        restored_messages.append(
                            {
                                "role": msg_role,
                                "content": [{"text": str(msg_content)}],
                            }
                        )

        source_sink = retrieval_sources_sink if retrieval_sources_sink is not None else []
        search_tool = build_scoped_search_manuals_tool(
            knowledge_base_ids=self.profile.retrieval_profile.knowledge_base_ids,
            region=self.region,
            reranking_enabled=self.profile.retrieval_profile.reranking_enabled,
            top_k=top_k,
            source_sink=source_sink,
        )

        return Agent(
            model=model,
            system_prompt=prompt,
            tools=[search_tool],
            conversation_manager=NullConversationManager(),
            messages=restored_messages if restored_messages else None,
        )

    def _greeting_response(self, query: str) -> dict | None:
        """Return a static greeting response when no model invocation is needed."""
        if _is_generic_rewrite_request(query):
            logger.info("off_topic_rewrite_short_circuited", query=query)
            return {
                "answer": OFF_TOPIC_RESPONSE,
                "citations": [],
                "follow_up_questions": [],
                "sources": [],
            }

        normalized_query = re.sub(r"[^\w\s]", "", query.strip().lower())
        greetings = {
            "hi",
            "hello",
            "hey",
            "hello there",
            "good morning",
            "good afternoon",
            "good evening",
            "greetings",
            "hi there",
            "hey there",
            "howdy",
            "hola",
            "hey ya",
            "hi ya",
            "how are you",
            "how are you doing",
            "yo",
        }
        if normalized_query not in greetings and normalized_query:
            return None

        logger.info("greeting_short_circuited", query=query)
        return {
            "answer": (
                "Hello! I am Coaction's Binding Authority underwriting assistant. "
                "How can I help you with underwriting, class codes, or manual guidelines today?"
            ),
            "citations": [],
            "follow_up_questions": [
                "What are the eligibility guidelines?",
                "What operations are prohibited?",
                "How do I search for class codes?",
            ],
            "sources": [],
        }

    def _finalize_response(
        self,
        *,
        raw_answer: str,
        retrieval_sources: list[dict],
        query: str,
        history: list[dict] | None,
    ) -> dict:
        """Convert raw agent output plus retrieval metadata into the public response."""
        source_id_to_meta, url_to_source_id, retrieved_internal_guidelines = (
            _build_public_source_maps(retrieval_sources)
        )
        answer, used_sources_block = _strip_used_sources_block(raw_answer)
        answer = _strip_thinking_blocks(answer)
        answer = _strip_inline_source_markers(answer)
        answer = _normalize_underwriter_guidance(answer)
        answer, suppress_citations = _enforce_explicit_evidence(
            answer=answer,
            query=query,
            retrieval_sources=retrieval_sources,
        )
        cited_source_ids = _parse_used_source_ids(
            used_sources_block,
            source_id_to_meta=source_id_to_meta,
            url_to_source_id=url_to_source_id,
        )
        if suppress_citations:
            cited_source_ids = []
        citation_fallback_used = False
        if not cited_source_ids and used_sources_block is None:
            cited_source_ids = _conservative_citation_fallback(answer, source_id_to_meta)
            citation_fallback_used = bool(cited_source_ids)

        cited_source_ids = cited_source_ids[:CITATION_LIMIT]

        logger.info(
            "citation_resolution",
            cited_count=len(cited_source_ids),
            retriever_count=len(source_id_to_meta),
            cited_source_ids=cited_source_ids,
            has_used_sources_block=used_sources_block is not None,
            citation_fallback_used=citation_fallback_used,
            internal_sources_retrieved=retrieved_internal_guidelines,
        )

        # STEP 2: Extract follow-up questions
        follow_up_questions: list[str] = []

        fu_patterns = [
            r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Follow-up questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Recommended questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Suggested questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Other questions you may ask:?\s*\*{0,2}",
        ]

        matched_pattern = None
        for pattern in fu_patterns:
            if re.search(pattern, answer):
                matched_pattern = pattern
                break

        if matched_pattern:
            parts = re.split(matched_pattern, answer, maxsplit=1)
            clean_answer = parts[0].strip()
            fu_text = parts[1]
            matches = re.findall(r"\b\d+\b\.\s*(.+)", fu_text)
            raw_followups = [m.strip() for m in matches if m.strip()]

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

        if not follow_up_questions:
            query_lower = query.lower()
            if (
                "property" in query_lower
                or "building" in query_lower
                or "limit" in query_lower
                or "tiv" in query_lower
            ):
                follow_up_questions = [
                    "What is the maximum building age for property coverage?",
                    "What are the referral thresholds for high Total Insured Value?",
                    "Are vacant buildings eligible for property insurance?",
                ]
            elif (
                "gl" in query_lower
                or "liability" in query_lower
                or "class" in query_lower
                or "code" in query_lower
            ):
                follow_up_questions = [
                    "What general liability operations are prohibited?",
                    "How do I locate the correct class code for a business?",
                    "What are the referral guidelines for general liability?",
                ]
            elif (
                "guidelines" in query_lower
                or "commission" in query_lower
                or "territory" in query_lower
                or "credit" in query_lower
            ):
                follow_up_questions = [
                    "What is the standard commission rate?",
                    "What are the credit authority thresholds for underwriters?",
                    "Are there specific territorial sales restrictions?",
                ]
            else:
                follow_up_questions = [
                    "What are the eligibility guidelines?",
                    "What operations are prohibited?",
                    "How do I search for class codes?",
                ]

        logger.info(
            "follow_up_extraction", count=len(follow_up_questions), questions=follow_up_questions
        )

        # STEP 3: Build citation objects
        sources = [source_id_to_meta[source_id]["url"] for source_id in cited_source_ids]
        citations: list[SourceCitation] = []
        seen_source_ids: set[str] = set()

        for source_id in cited_source_ids:
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            meta = source_id_to_meta[source_id]
            citations.append(
                SourceCitation(
                    source_id=source_id,
                    title=meta.get("heading", "") or meta["url"],
                    uri=meta["url"],
                    manual_name=meta.get("manual_name", ""),
                    class_code=meta.get("class_code"),
                )
            )

        logger.info("citations_built", count=len(citations))

        return {
            "answer": answer,
            "citations": citations,
            "follow_up_questions": follow_up_questions,
            "sources": sources,
        }

    async def stream(
        self,
        query: str,
        role: str = "agent",
        history: list[dict] | None = None,
        model_id: str | None = None,
        top_k: int = 5,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream visible answer deltas, then yield the final structured response."""
        greeting_response = self._greeting_response(query)
        if greeting_response:
            yield {"type": "final", "result": greeting_response}
            return

        retriever_token = set_retriever_context(
            knowledge_base_ids=self.profile.retrieval_profile.knowledge_base_ids,
            region=self.region,
            reranking_enabled=self.profile.retrieval_profile.reranking_enabled,
            top_k=top_k,
        )

        raw_parts: list[str] = []
        raw_answer = ""
        visible_emitted = ""

        try:
            clear_retrieval_sources()
            retrieval_sources_sink: list[dict] = []
            agent = self._build_agent(
                role,
                messages=history,
                model_id=model_id,
                top_k=top_k,
                retrieval_sources_sink=retrieval_sources_sink,
            )

            async for event in agent.stream_async(query):
                if "data" in event:
                    raw_parts.append(str(event["data"]))
                    raw_so_far = "".join(raw_parts)
                    visible_so_far = _stream_visible_answer(raw_so_far)
                    if visible_so_far.startswith(visible_emitted):
                        delta = visible_so_far[len(visible_emitted) :]
                        if delta:
                            visible_emitted = visible_so_far
                            yield {"type": "delta", "text": delta}
                if "result" in event:
                    raw_answer = str(event["result"])

            if not raw_answer:
                raw_answer = "".join(raw_parts)
            retrieval_sources = retrieval_sources_sink or get_last_retrieval_sources()
        finally:
            reset_retriever_context(retriever_token)

        result = self._finalize_response(
            raw_answer=raw_answer,
            retrieval_sources=retrieval_sources,
            query=query,
            history=history,
        )

        final_answer = result["answer"]
        if final_answer.startswith(visible_emitted):
            remaining = final_answer[len(visible_emitted) :]
            if remaining:
                yield {"type": "delta", "text": remaining}

        yield {"type": "final", "result": result}

    async def invoke(
        self,
        query: str,
        role: str = "agent",
        history: list[dict] | None = None,
        model_id: str | None = None,
        top_k: int = 5,
    ) -> dict:
        """Invoke the agent with a query."""
        static_response = self._greeting_response(query)
        if static_response:
            return static_response

        # Short-circuit greetings & small talk to bypass aggressive off-topic safeguard model
        normalized_query = re.sub(r"[^\w\s]", "", query.strip().lower())
        greetings = {
            "hi",
            "hello",
            "hey",
            "hello there",
            "good morning",
            "good afternoon",
            "good evening",
            "greetings",
            "hi there",
            "hey there",
            "howdy",
            "hola",
            "hey ya",
            "hi ya",
            "how are you",
            "how are you doing",
            "yo",
        }
        if normalized_query in greetings or not normalized_query:
            logger.info("greeting_short_circuited", query=query)
            return {
                "answer": "Hello! I am Coaction's Binding Authority underwriting assistant. How can I help you with underwriting, class codes, or manual guidelines today?",
                "citations": [],
                "follow_up_questions": [
                    "What are the eligibility guidelines?",
                    "What operations are prohibited?",
                    "How do I search for class codes?",
                ],
                "sources": [],
            }

        retriever_token = set_retriever_context(
            knowledge_base_ids=self.profile.retrieval_profile.knowledge_base_ids,
            region=self.region,
            reranking_enabled=self.profile.retrieval_profile.reranking_enabled,
            top_k=top_k,
        )

        try:
            # Clear stale retrieval sources before each invocation
            clear_retrieval_sources()
            retrieval_sources_sink: list[dict] = []

            # Build a fresh agent with conversation history pre-loaded
            agent = self._build_agent(
                role,
                messages=history,
                model_id=model_id,
                top_k=top_k,
                retrieval_sources_sink=retrieval_sources_sink,
            )

            # Execute the agent (synchronous Strands call)
            response = agent(query)
            raw_answer = str(response)

            # ─── STEP 1: Extract <used_sources> from the RAW answer ─────────
            # This MUST happen first because follow-up extraction will strip
            # the tail of the answer where <used_sources> lives.
            retrieval_sources = retrieval_sources_sink or get_last_retrieval_sources()
        finally:
            reset_retriever_context(retriever_token)

        return self._finalize_response(
            raw_answer=raw_answer,
            retrieval_sources=retrieval_sources,
            query=query,
            history=history,
        )

        source_id_to_meta, url_to_source_id, retrieved_internal_guidelines = (
            _build_public_source_maps(retrieval_sources)
        )
        answer, used_sources_block = _strip_used_sources_block(raw_answer)
        answer = _strip_thinking_blocks(answer)
        answer = _strip_inline_source_markers(answer)
        answer = _normalize_underwriter_guidance(answer)
        cited_source_ids = _parse_used_source_ids(
            used_sources_block,
            source_id_to_meta=source_id_to_meta,
            url_to_source_id=url_to_source_id,
        )
        citation_fallback_used = False
        if not cited_source_ids and used_sources_block is None:
            cited_source_ids = _conservative_citation_fallback(answer, source_id_to_meta)
            citation_fallback_used = bool(cited_source_ids)
        cited_source_ids = cited_source_ids[:CITATION_LIMIT]

        logger.info(
            "citation_resolution",
            cited_count=len(cited_source_ids),
            retriever_count=len(source_id_to_meta),
            cited_source_ids=cited_source_ids,
            has_used_sources_block=used_sources_block is not None,
            citation_fallback_used=citation_fallback_used,
            internal_sources_retrieved=retrieved_internal_guidelines,
        )

        # ─── STEP 2: Extract follow-up questions ────────────────────────
        follow_up_questions: list[str] = []

        # Support various headers the LLM might use for follow-ups
        fu_patterns = [
            r"(?i)\*{0,2}\s*You might also want to ask:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Follow-up questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Recommended questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Suggested questions:?\s*\*{0,2}",
            r"(?i)\*{0,2}\s*Other questions you may ask:?\s*\*{0,2}",
        ]

        matched_pattern = None
        for pattern in fu_patterns:
            if re.search(pattern, answer):
                matched_pattern = pattern
                break

        if matched_pattern:
            parts = re.split(matched_pattern, answer, maxsplit=1)
            clean_answer = parts[0].strip()
            fu_text = parts[1]
            matches = re.findall(r"\b\d+\b\.\s*(.+)", fu_text)
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

        # If no follow-ups were returned by the LLM or parsed, generate high-quality fallback questions dynamically
        if not follow_up_questions:
            query_lower = query.lower()
            if (
                "property" in query_lower
                or "building" in query_lower
                or "limit" in query_lower
                or "tiv" in query_lower
            ):
                follow_up_questions = [
                    "What is the maximum building age for property coverage?",
                    "What are the referral thresholds for high Total Insured Value?",
                    "Are vacant buildings eligible for property insurance?",
                ]
            elif (
                "gl" in query_lower
                or "liability" in query_lower
                or "class" in query_lower
                or "code" in query_lower
            ):
                follow_up_questions = [
                    "What general liability operations are prohibited?",
                    "How do I locate the correct class code for a business?",
                    "What are the referral guidelines for general liability?",
                ]
            elif (
                "guidelines" in query_lower
                or "commission" in query_lower
                or "territory" in query_lower
                or "credit" in query_lower
            ):
                follow_up_questions = [
                    "What is the standard commission rate?",
                    "What are the credit authority thresholds for underwriters?",
                    "Are there specific territorial sales restrictions?",
                ]
            else:
                follow_up_questions = [
                    "What are the eligibility guidelines?",
                    "What operations are prohibited?",
                    "How do I search for class codes?",
                ]

        logger.info(
            "follow_up_extraction", count=len(follow_up_questions), questions=follow_up_questions
        )

        # ─── STEP 3: Build citation objects ──────────────────────────────
        sources = [source_id_to_meta[source_id]["url"] for source_id in cited_source_ids]
        citations: list[SourceCitation] = []
        seen_source_ids: set[str] = set()

        for source_id in cited_source_ids:
            if source_id in seen_source_ids:
                continue
            seen_source_ids.add(source_id)
            meta = source_id_to_meta[source_id]
            citations.append(
                SourceCitation(
                    source_id=source_id,
                    title=meta.get("heading", "") or meta["url"],
                    uri=meta["url"],
                    manual_name=meta.get("manual_name", ""),
                    class_code=meta.get("class_code"),
                )
            )

        logger.info("citations_built", count=len(citations))

        return {
            "answer": answer,
            "citations": citations,
            "follow_up_questions": follow_up_questions,
            "sources": sources,
        }
