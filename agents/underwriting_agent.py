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
LOB_CLARIFICATION_QUESTION = "Are you inquiring about Property or General Liability coverage?"
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
    "solar panel",
    "solar panels",
)
RETRIEVAL_MATCH_STOPWORDS = {
    "about",
    "and",
    "are",
    "coverage",
    "define",
    "details",
    "does",
    "explain",
    "for",
    "from",
    "give",
    "guide",
    "info",
    "information",
    "is",
    "manual",
    "mean",
    "me",
    "of",
    "on",
    "overview",
    "please",
    "summary",
    "summarize",
    "tell",
    "the",
    "this",
    "what",
}
PROPERTY_QUERY_SIGNALS = {
    "bpp",
    "business income",
    "business personal property",
    "cp ",
    "property",
    "tenant improvement",
    "tenant improvements",
    "tiv",
}
PROPERTY_QUERY_PATTERNS = (
    r"\bbuilding\s+limits?\b",
    r"\bbuilding\s+values?\b",
    r"\bvalue\s+of\s+.+\bbuilding\s+limits?\b",
    r"\bvalues?\s+in\s+the\s+building\s+limits?\b",
    r"\battached\s+to\s+a\s+building\b",
    r"\bAOP\s+deductible\b",
    r"\bACV\b",
    r"\bRCV\b",
)
GL_QUERY_SIGNALS = {
    "cg ",
    "class code",
    "general liability",
    "gl ",
    "liability",
}
LOB_SELECTION_DIRECTIVE_TOKENS = {
    "coverage",
    "correct",
    "for",
    "from",
    "general",
    "gl",
    "i",
    "liability",
    "line",
    "manual",
    "mean",
    "meant",
    "one",
    "please",
    "property",
    "right",
    "the",
    "yeah",
    "yep",
    "yes",
}
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


def _split_trailing_numbered_followups(answer: str) -> tuple[str, list[str]]:
    """Strip model-emitted follow-up questions even when it forgot the header."""
    lines = answer.rstrip().splitlines()
    if not lines:
        return answer, []

    followups_reversed: list[str] = []
    saw_numbered_question = False
    idx = len(lines) - 1
    while idx >= 0:
        line = lines[idx].strip()
        if not line:
            idx -= 1
            continue

        numbered_match = re.match(r"^\d+\.\s*(.+\?)\s*$", line)
        if numbered_match:
            followups_reversed.append(numbered_match.group(1).strip())
            saw_numbered_question = True
            idx -= 1
            continue

        if saw_numbered_question and line.endswith("?"):
            followups_reversed.append(line)
            idx -= 1
            continue

        break

    if not saw_numbered_question:
        return answer, []

    clean_answer = "\n".join(lines[: idx + 1]).strip()
    return clean_answer, list(reversed(followups_reversed))[:3]


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


def _normalize_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _match_tokens(text: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", text.lower()):
        if len(token) < 3 or token in RETRIEVAL_MATCH_STOPWORDS:
            continue
        if token.endswith("s") and len(token) > 4:
            token = token[:-1]
        tokens.add(token)
    return tokens


def _query_mentions_line_of_business(query: str) -> bool:
    query_lower = f" {query.lower()} "
    if any(signal in query_lower for signal in PROPERTY_QUERY_SIGNALS | GL_QUERY_SIGNALS):
        return True
    return any(
        re.search(pattern, query, flags=re.IGNORECASE) for pattern in PROPERTY_QUERY_PATTERNS
    )


def _query_is_lob_selection_only(query: str) -> bool:
    normalized = _normalize_match_text(query)
    if not normalized:
        return False
    mentions_lob = bool(re.search(r"\b(property|gl|general liability|liability)\b", normalized))
    if not mentions_lob:
        return False
    tokens = set(normalized.split())
    return not (tokens - LOB_SELECTION_DIRECTIVE_TOKENS)


def _selected_lob_from_query(query: str) -> str | None:
    normalized = _normalize_match_text(query)
    if re.search(r"\bproperty\b", normalized):
        return "Property"
    if re.search(r"\b(gl|general liability|liability)\b", normalized):
        return "General Liability"
    return None


def _latest_user_topic(history: list[dict] | None) -> str | None:
    if not history:
        return None
    for msg in reversed(history):
        if not isinstance(msg, dict):
            continue
        if (msg.get("role") or "").strip().lower() != "user":
            continue
        content = str(msg.get("content") or "").strip()
        if not content or _query_is_lob_selection_only(content):
            continue
        return content
    return None


def _retrieval_current_query(query: str, history: list[dict] | None) -> str:
    """Carry the prior topic into short LOB-selection follow-ups."""
    if not _query_is_lob_selection_only(query):
        return query
    selected_lob = _selected_lob_from_query(query)
    prior_topic = _latest_user_topic(history)
    if not selected_lob or not prior_topic:
        return query
    return f"{prior_topic} {selected_lob}"


def _public_source_line_of_business(source: dict) -> str | None:
    manual_name = (source.get("manual_name") or "").lower()
    manual_type = (source.get("manual_type") or "").lower()
    url = (source.get("url") or "").lower()
    combined = f"{manual_name} {manual_type} {url}"
    if "internal" in combined:
        return None
    if "property" in combined:
        return "Property"
    if (
        "general liability" in combined
        or "guide" in combined
        or re.search(r"/manuals/\d{4,}\.html", url)
    ):
        return "General Liability"
    return None


def _source_evidence_text(source: dict) -> str:
    return " ".join(
        str(source.get(key) or "")
        for key in ("heading", "manual_name", "manual_type", "aliases", "content_text", "snippet")
    )


def _source_matches_query_topic(source: dict, query: str) -> bool:
    query_tokens = _match_tokens(query)
    if len(query_tokens) < 2:
        return False

    evidence = _source_evidence_text(source)
    evidence_tokens = _match_tokens(evidence)
    if query_tokens <= evidence_tokens:
        return True

    query_norm = _normalize_match_text(query)
    evidence_norm = _normalize_match_text(evidence)
    important_words = [
        word
        for word in query_norm.split()
        if len(word) >= 3 and word not in RETRIEVAL_MATCH_STOPWORDS
    ]
    for phrase_len in range(min(4, len(important_words)), 1, -1):
        for idx in range(0, len(important_words) - phrase_len + 1):
            phrase = " ".join(important_words[idx : idx + phrase_len])
            if phrase in evidence_norm:
                return True

    return False


def _requires_cross_manual_clarification(query: str, retrieval_sources: list[dict]) -> bool:
    """Require clarification when the same topic appears in public GL and Property results."""
    if _query_mentions_line_of_business(query):
        return False

    matched_lobs = set()
    for source in retrieval_sources:
        lob = _public_source_line_of_business(source)
        if not lob:
            continue
        if _source_matches_query_topic(source, query):
            matched_lobs.add(lob)

    return {"Property", "General Liability"} <= matched_lobs


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

    guidance_sentence_re = (
        r"[^.!?\n]*\b("
        r"contact|consult|ask|reach\s+out\s+to|talk\s+to|check\s+with|"
        r"please\s+contact|for\s+authoritative\s+guidance"
        r")\b[^.!?\n]*\bCoaction underwriter\b[^.!?\n]*(?:[.!?]|$)"
    )
    normalized = re.sub(
        guidance_sentence_re,
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
        if "internal" in str(src.get("manual_name") or src.get("manual_type") or "").lower():
            retrieved_internal_guidelines = True
            continue

        url = (src.get("url") or "").strip().rstrip("/")
        source_id = (src.get("source_id") or "").strip()
        if not url or url == "N/A" or not source_id:
            continue

        normalized = {**src, "url": url, "source_id": source_id}
        source_id_to_meta[source_id] = normalized
        url_to_source_id.setdefault(url, source_id)

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


def _citation_support_score(answer: str, query: str, source: dict) -> int:
    evidence = _source_evidence_text(source)
    answer_tokens = _match_tokens(answer)
    query_tokens = _match_tokens(query)
    evidence_tokens = _match_tokens(evidence)

    score = len((answer_tokens | query_tokens) & evidence_tokens)
    heading_tokens = _match_tokens(str(source.get("heading") or ""))
    score += len(query_tokens & heading_tokens) * 3

    answer_norm = _normalize_match_text(answer)
    evidence_norm = _normalize_match_text(evidence)
    heading_norm = _normalize_match_text(str(source.get("heading") or ""))
    if heading_norm and heading_norm in answer_norm:
        score += 8
    for phrase in ("risk meter", "triple net lease", "solar panels", "spoilage coverage"):
        if phrase in answer_norm and phrase in evidence_norm:
            score += 5

    return score


def _retrieved_citation_fallback(
    *,
    answer: str,
    query: str,
    source_id_to_meta: dict[str, dict],
) -> list[str]:
    """Pick a citation from retrieved public sources when the model omits source IDs."""
    conservative = _conservative_citation_fallback(answer, source_id_to_meta)
    if conservative:
        return conservative

    scored_sources = []
    for source_id, source in source_id_to_meta.items():
        score = _citation_support_score(answer, query, source)
        if score > 0:
            scored_sources.append((score, source_id))

    if not scored_sources:
        return []

    scored_sources.sort(reverse=True)
    top_score, top_source_id = scored_sources[0]
    second_score = scored_sources[1][0] if len(scored_sources) > 1 else 0
    if top_score >= 4 and top_score >= second_score + 2:
        return [top_source_id]
    return []


def _answer_looks_hollow(answer: str) -> bool:
    """Detect answers that contain only a heading, empty markdown, or no facts."""
    normalized = _normalize_match_text(answer)
    tokens = _match_tokens(answer)
    if not normalized or not tokens:
        return True
    if "** **" in answer and len(tokens) <= 5:
        return True
    if len(tokens) <= 3 and not re.search(r"[.!?]\s*$", answer.strip()):
        return True
    return False


def _answer_is_retrieval_refusal(answer: str) -> bool:
    normalized = answer.lower()
    return bool(
        re.search(
            r"\b(can't|cannot|could not|not able|no matches|no relevant|not found)\b",
            normalized,
        )
        and re.search(r"\b(retrieve|retrieved|knowledge base|manual content|search)\b", normalized)
    )


def _plain_source_line(line: str, heading: str) -> str:
    line = line.strip()
    if not line:
        return ""
    if re.match(r"^\|?\s*:?-{3,}", line):
        return ""
    heading_norm = _normalize_match_text(heading)
    line_without_heading = re.sub(r"^#{1,6}\s*", "", line).strip()
    line_without_heading = line_without_heading.strip("_*` ")
    if heading_norm and _normalize_match_text(line_without_heading) == heading_norm:
        return ""
    if line.startswith("|") and line.endswith("|"):
        cells = [cell.strip(" _*`") for cell in line.strip("|").split("|")]
        cells = [cell for cell in cells if cell and not re.fullmatch(r":?-{3,}:?", cell)]
        return " - ".join(cells)
    line = re.sub(r"^[-*+]\s+", "", line)
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = line.strip("_*` ")
    return re.sub(r"\s+", " ", line).strip()


def _source_fact_lines(source: dict, *, max_lines: int = 8) -> list[str]:
    content = str(source.get("content_text") or source.get("snippet") or "")
    heading = str(source.get("heading") or "")
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in content.splitlines():
        clean_line = _plain_source_line(raw_line, heading)
        normalized = _normalize_question(clean_line)
        if not clean_line or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        lines.append(clean_line)
        if len(lines) == max_lines:
            break
    return lines


def _manual_label_for_answer(source: dict) -> str:
    manual_name = str(source.get("manual_name") or "Manual").strip()
    return re.sub(r"\s+Manual$", "", manual_name).strip() or "Manual"


def _compose_answer_from_source(source: dict) -> str | None:
    lines = _source_fact_lines(source)
    if not lines:
        return None
    heading = str(source.get("heading") or "Manual Section").strip() or "Manual Section"
    title = f"{heading} ({_manual_label_for_answer(source)})"
    if len(lines) == 1:
        return f"{title}\n\n{lines[0]}"
    return f"{title}\n\n" + "\n".join(f"- {line}" for line in lines)


def _source_backfill_answer(
    *,
    answer: str,
    query: str,
    cited_source_ids: list[str],
    source_id_to_meta: dict[str, dict],
) -> tuple[str, list[str], bool]:
    """Replace hollow/refusal answers when retrieved public source text is usable."""
    looks_hollow = _answer_looks_hollow(answer)
    is_uncited_retrieval_refusal = _answer_is_retrieval_refusal(answer) and not cited_source_ids
    if not (looks_hollow or is_uncited_retrieval_refusal):
        return answer, cited_source_ids, False

    candidate_ids = list(cited_source_ids) or list(source_id_to_meta)
    if not candidate_ids:
        return answer, cited_source_ids, False

    scored_candidates = []
    query_tokens = _match_tokens(query)
    for source_id in candidate_ids:
        source = source_id_to_meta.get(source_id)
        if not source:
            continue
        if _public_source_line_of_business(source) not in {"Property", "General Liability"}:
            continue
        if not _source_fact_lines(source, max_lines=1):
            continue
        score = _citation_support_score(answer, query, source)
        heading_tokens = _match_tokens(str(source.get("heading") or ""))
        score += len(query_tokens & heading_tokens) * 3
        if str(source.get("manual_name") or "").lower().startswith("property"):
            score += 2
        scored_candidates.append((score, source_id, source))

    if not scored_candidates:
        return answer, cited_source_ids, False

    scored_candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, source_id, source = scored_candidates[0]
    composed = _compose_answer_from_source(source)
    if not composed:
        return answer, cited_source_ids, False

    return composed, [source_id], True


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
            raw_retrieval_mode=profile.retrieval_profile.raw_retrieval_mode,
            search_type=profile.retrieval_profile.search_type,
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
        current_query: str | None = None,
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
            current_query=current_query,
            raw_retrieval_mode=self.profile.retrieval_profile.raw_retrieval_mode,
            search_type=self.profile.retrieval_profile.search_type,
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
        if self.profile.retrieval_profile.raw_retrieval_mode:
            return self._finalize_raw_kb_test_response(
                raw_answer=raw_answer,
                retrieval_sources=retrieval_sources,
                query=query,
            )

        source_id_to_meta, url_to_source_id, retrieved_internal_guidelines = (
            _build_public_source_maps(retrieval_sources)
        )
        answer, used_sources_block = _strip_used_sources_block(raw_answer)
        answer = _strip_thinking_blocks(answer)
        answer = _strip_inline_source_markers(answer)
        answer = _normalize_underwriter_guidance(answer)

        if _requires_cross_manual_clarification(query, retrieval_sources):
            logger.info("cross_manual_clarification_required", query=query)
            return {
                "answer": LOB_CLARIFICATION_QUESTION,
                "citations": [],
                "follow_up_questions": [],
                "sources": [],
            }

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
        if not cited_source_ids and not suppress_citations:
            cited_source_ids = _retrieved_citation_fallback(
                answer=answer,
                query=query,
                source_id_to_meta=source_id_to_meta,
            )
            citation_fallback_used = bool(cited_source_ids)

        source_backfill_used = False
        if not suppress_citations:
            answer, cited_source_ids, source_backfill_used = _source_backfill_answer(
                answer=answer,
                query=query,
                cited_source_ids=cited_source_ids,
                source_id_to_meta=source_id_to_meta,
            )

        cited_source_ids = cited_source_ids[:CITATION_LIMIT]

        logger.info(
            "citation_resolution",
            cited_count=len(cited_source_ids),
            retriever_count=len(source_id_to_meta),
            cited_source_ids=cited_source_ids,
            has_used_sources_block=used_sources_block is not None,
            citation_fallback_used=citation_fallback_used,
            source_backfill_used=source_backfill_used,
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
            answer, trailing_followups = _split_trailing_numbered_followups(answer)
            follow_up_questions.extend(trailing_followups)

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

    def _finalize_raw_kb_test_response(
        self,
        *,
        raw_answer: str,
        retrieval_sources: list[dict],
        query: str,
    ) -> dict:
        """Minimal response shaping for raw KB UI tests."""
        source_id_to_meta, url_to_source_id, retrieved_internal_guidelines = (
            _build_public_source_maps(retrieval_sources)
        )
        answer, used_sources_block = _strip_used_sources_block(raw_answer)
        answer = _strip_thinking_blocks(answer)
        answer = _strip_inline_source_markers(answer).strip()

        cited_source_ids = _parse_used_source_ids(
            used_sources_block,
            source_id_to_meta=source_id_to_meta,
            url_to_source_id=url_to_source_id,
        )
        citation_fallback_used = False
        if not cited_source_ids:
            cited_source_ids = _retrieved_citation_fallback(
                answer=answer,
                query=query,
                source_id_to_meta=source_id_to_meta,
            )
            citation_fallback_used = bool(cited_source_ids)

        cited_source_ids = cited_source_ids[:CITATION_LIMIT]

        logger.info(
            "raw_kb_test_citation_resolution",
            cited_count=len(cited_source_ids),
            retriever_count=len(source_id_to_meta),
            cited_source_ids=cited_source_ids,
            has_used_sources_block=used_sources_block is not None,
            citation_fallback_used=citation_fallback_used,
            internal_sources_retrieved=retrieved_internal_guidelines,
        )

        sources = [source_id_to_meta[source_id]["url"] for source_id in cited_source_ids]
        citations: list[SourceCitation] = []
        for source_id in cited_source_ids:
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

        return {
            "answer": answer,
            "citations": citations,
            "follow_up_questions": [],
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

        retrieval_query = _retrieval_current_query(query, history)
        retriever_token = set_retriever_context(
            knowledge_base_ids=self.profile.retrieval_profile.knowledge_base_ids,
            region=self.region,
            reranking_enabled=self.profile.retrieval_profile.reranking_enabled,
            top_k=top_k,
            current_query=retrieval_query,
            raw_retrieval_mode=self.profile.retrieval_profile.raw_retrieval_mode,
            search_type=self.profile.retrieval_profile.search_type,
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
                current_query=retrieval_query,
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

        retrieval_query = _retrieval_current_query(query, history)
        retriever_token = set_retriever_context(
            knowledge_base_ids=self.profile.retrieval_profile.knowledge_base_ids,
            region=self.region,
            reranking_enabled=self.profile.retrieval_profile.reranking_enabled,
            top_k=top_k,
            current_query=retrieval_query,
            raw_retrieval_mode=self.profile.retrieval_profile.raw_retrieval_mode,
            search_type=self.profile.retrieval_profile.search_type,
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
                current_query=retrieval_query,
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
