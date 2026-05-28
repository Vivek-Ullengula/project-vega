# coaction_agent_platform/agents/tools/retriever.py
"""Configurable Bedrock KB retrieval tool for the Strands agent.

Ported from coactionbot/app/services/bedrock_retriever.py with full configurability.
Accepts KB IDs at runtime from ExecutionProfile instead of hardcoded env vars.
"""

import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

import boto3
import structlog
from strands import tool

logger = structlog.get_logger(__name__)

# Minimum relevance score — chunks below this are discarded as noise.
MIN_RELEVANCE_SCORE = 0.25
LEXICAL_MATCH_MIN_SCORE = 2

_SEARCH_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "our",
    "the",
    "to",
    "we",
    "what",
    "where",
    "with",
}

_CONTEXT_FOLLOWUP_REFERENCE_WORDS = {
    "above",
    "it",
    "prior",
    "previous",
    "same",
    "that",
    "these",
    "this",
    "those",
}

_CONTEXT_FOLLOWUP_DIRECTIVE_TOKENS = {
    "about",
    "andgive",
    "answer",
    "coaction",
    "correct",
    "explain",
    "give",
    "general",
    "guide",
    "info",
    "information",
    "liability",
    "manual",
    "need",
    "overview",
    "please",
    "property",
    "regarding",
    "related",
    "right",
    "summary",
    "summarize",
    "tell",
    "yeah",
    "yep",
    "yes",
}

PROPERTY_MANUAL_URL = "https://bindingauthority.coactionspecialty.com/manuals/property.html"
GL_GUIDE_MANUAL_URL = "https://bindingauthority.coactionspecialty.com/manuals/guide.html"

# ── US State Data ────────────────────────────────────────────────────────

US_STATE_ABBREVS = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

_STATE_NAME_TO_ABBREV = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE_ABBREV_TO_NAME = {abbrev: name.title() for name, abbrev in _STATE_NAME_TO_ABBREV.items()}

# ── Module State ─────────────────────────────────────────────────────────

# Default config is used by direct CLI/script invocations. FastAPI requests set a
# request-local context so concurrent agents cannot leak KB IDs or citations.
_reranker_model_id: str = os.getenv("RERANKER_MODEL_ID", "cohere.rerank-v3-5:0")
_bedrock_clients: dict[str, Any] = {}
_default_retriever_config: "RetrieverConfig | None" = None
_rerank_disabled_kb_ids: set[str] = set()
_retriever_config: ContextVar["RetrieverConfig | None"] = ContextVar(
    "vega_retriever_config",
    default=None,
)
_last_retrieval_sources: ContextVar[list[dict] | None] = ContextVar(
    "vega_last_retrieval_sources",
    default=None,
)


@dataclass(frozen=True)
class RetrieverConfig:
    knowledge_base_ids: tuple[str, ...]
    region: str = "us-east-1"
    reranking_enabled: bool = True
    top_k: int = 5
    current_query: str | None = None
    raw_retrieval_mode: bool = False
    search_type: str = "HYBRID"


def _get_bedrock_client(region: str):
    if region not in _bedrock_clients:
        _bedrock_clients[region] = boto3.client("bedrock-agent-runtime", region_name=region)
    return _bedrock_clients[region]


def _clamp_top_k(top_k: int | None) -> int:
    if top_k is None:
        return 5
    return max(1, min(int(top_k), 20))


def _active_config() -> RetrieverConfig | None:
    return _retriever_config.get() or _default_retriever_config


def _reranking_enabled(requested: bool) -> bool:
    # Default to disabled because the deployed role currently lacks bedrock:Rerank.
    # Set VEGA_RERANKING_ENABLED=1 only after the IAM policy is updated.
    override = os.getenv("VEGA_RERANKING_ENABLED", "0")
    return requested and override.strip().lower() in {"1", "true", "yes", "on"}


def configure_retriever(
    knowledge_base_ids: list[str],
    region: str = "us-east-1",
    reranking_enabled: bool = True,
    top_k: int = 5,
    raw_retrieval_mode: bool = False,
    search_type: str = "HYBRID",
) -> None:
    """Configure the retriever with KB IDs from an ExecutionProfile."""
    global _default_retriever_config
    effective_reranking_enabled = _reranking_enabled(reranking_enabled)
    _default_retriever_config = RetrieverConfig(
        knowledge_base_ids=tuple(knowledge_base_ids),
        region=region,
        reranking_enabled=effective_reranking_enabled,
        top_k=_clamp_top_k(top_k),
        raw_retrieval_mode=raw_retrieval_mode,
        search_type=search_type.upper(),
    )
    _get_bedrock_client(region)
    logger.info(
        "retriever_configured",
        kb_ids=knowledge_base_ids,
        region=region,
        reranking_enabled=effective_reranking_enabled,
        top_k=_clamp_top_k(top_k),
        raw_retrieval_mode=raw_retrieval_mode,
        search_type=search_type.upper(),
    )


def set_retriever_context(
    knowledge_base_ids: list[str],
    region: str = "us-east-1",
    reranking_enabled: bool = True,
    top_k: int = 5,
    current_query: str | None = None,
    raw_retrieval_mode: bool = False,
    search_type: str = "HYBRID",
) -> Token:
    """Set request-local retriever config and return a token for reset."""
    return _retriever_config.set(
        RetrieverConfig(
            knowledge_base_ids=tuple(knowledge_base_ids),
            region=region,
            reranking_enabled=_reranking_enabled(reranking_enabled),
            top_k=_clamp_top_k(top_k),
            current_query=current_query,
            raw_retrieval_mode=raw_retrieval_mode,
            search_type=search_type.upper(),
        )
    )


def reset_retriever_context(token: Token) -> None:
    """Reset request-local retriever config."""
    _retriever_config.reset(token)


def get_last_retrieval_sources() -> list[dict]:
    """Return source metadata from the most recent search_manuals call."""
    return list(_last_retrieval_sources.get() or [])


def clear_retrieval_sources() -> None:
    """Clear stale retrieval sources before a new invocation."""
    _last_retrieval_sources.set([])


def _set_retrieval_sources(sources: list[dict]) -> None:
    """Store retrieval sources for legacy/global tool paths."""
    _last_retrieval_sources.set(list(sources))


# ── Helper Functions ─────────────────────────────────────────────────────


def _extract_state_abbreviations(content: str) -> set[str]:
    """Extract all US state abbreviations found in the document text."""
    found = set()
    for match in re.finditer(r"\b([A-Z]{2})\b", content):
        abbrev = match.group(1)
        if abbrev in US_STATE_ABBREVS:
            found.add(abbrev)
    return found


def _extract_queried_states(query: str) -> list[tuple[str, str]]:
    """Detect US state names or abbreviations in the user's query."""
    query_lower = query.lower()
    found = []
    seen_abbrevs: set[str] = set()

    for name, abbrev in sorted(_STATE_NAME_TO_ABBREV.items(), key=lambda x: -len(x[0])):
        if name in query_lower and abbrev not in seen_abbrevs:
            found.append((name.title(), abbrev))
            seen_abbrevs.add(abbrev)

    for match in re.finditer(r"\b([A-Z]{2})\b", query):
        abbrev = match.group(1)
        if abbrev in US_STATE_ABBREVS and abbrev not in seen_abbrevs:
            name = next(
                (n.title() for n, a in _STATE_NAME_TO_ABBREV.items() if a == abbrev), abbrev
            )
            found.append((name, abbrev))
            seen_abbrevs.add(abbrev)

    return found


def _line_mentions_state(line: str, state_name: str, state_abbrev: str) -> bool:
    return bool(
        re.search(rf"\b{re.escape(state_abbrev)}\b", line)
        or re.search(rf"\b{re.escape(state_name)}\b", line, flags=re.IGNORECASE)
    )


def _section_heading_from_line(line: str) -> str:
    heading_match = re.match(r"^#{1,4}\s*(.+)", line.strip())
    if not heading_match:
        return ""
    return _normalize_search_text(heading_match.group(1))


def _state_mentions_from_content(
    content: str,
    queried_states: list[tuple[str, str]],
) -> list[str]:
    """Summarize queried state mentions without turning mentions into eligibility verdicts."""
    if not queried_states:
        return []

    active_section = ""
    state_results: dict[str, dict[str, str]] = {
        abbrev: {
            "name": name,
            "abbrev": abbrev,
            "status": "NOT MENTIONED",
            "detail": "not found in retrieved text",
        }
        for name, abbrev in queried_states
    }

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        heading = _section_heading_from_line(line)
        if heading:
            active_section = heading
            continue

        in_prohibited_section = "prohibited" in active_section
        in_submit_section = any(term in active_section for term in ("submit", "refer", "referral"))
        line_prohibits = bool(
            in_prohibited_section
            or re.search(
                r"\b(prohibited|ineligible|not\s+a\s+market|not\s+eligible|excluded)\b",
                line,
                flags=re.IGNORECASE,
            )
        )
        line_requires_review = bool(
            in_submit_section
            or re.search(
                r"\b(submit|refer|approval|required|does\s+not\s+apply)\b",
                line,
                flags=re.IGNORECASE,
            )
        )

        for state_name, state_abbrev in queried_states:
            if not _line_mentions_state(line, state_name, state_abbrev):
                continue

            result = state_results[state_abbrev]
            if line_prohibits:
                result["status"] = "PROHIBITED"
                result["detail"] = line
            elif result["status"] != "PROHIBITED" and line_requires_review:
                result["status"] = "REFER/SUBMIT"
                result["detail"] = line
            elif result["status"] == "NOT MENTIONED":
                result["status"] = "MENTIONED"
                result["detail"] = line

    return [
        f"  - {item['name']} ({item['abbrev']}): {item['status']} - {item['detail']}"
        for item in state_results.values()
    ]


def _normalize_search_text(value: str) -> str:
    """Normalize text for lightweight lexical matching."""
    normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _compact_search_text(value: str) -> str:
    """Normalize text for compact form/code matching."""
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_form_references(value: str) -> set[str]:
    """Return compact/spaced variants for form IDs like CG 22 94 and CG2294."""
    references: set[str] = set()
    for prefix, number, edition in re.findall(
        r"\b([A-Z]{2})\s*[-]?\s*(\d{2,5})(?:\s*[-]?\s*(\d{2,4}))?\b",
        value.upper(),
    ):
        prefixes = [prefix]
        if prefix in {"GL", "CG"}:
            prefixes.append("CG" if prefix == "GL" else "GL")

        digit_variants = {number}
        if edition:
            digit_variants.add(f"{number}{edition}")
            if len(number) <= 2:
                digit_variants.add(f"{number.zfill(2)}{edition}")

        for form_prefix in prefixes:
            for digits in digit_variants:
                references.add(f"{form_prefix}{digits}")
                references.add(f"{form_prefix} {digits}")
                if len(digits) == 4:
                    references.add(f"{form_prefix} {digits[:2]} {digits[2:]}")
            if edition:
                references.add(f"{form_prefix} {number} {edition}")

    return references


def _singularize_token(token: str) -> str:
    if token.endswith("s") and len(token) > 4:
        return token[:-1]
    return token


def _search_tokens(value: str) -> set[str]:
    tokens = set()
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        if len(token) < 3 or token in _SEARCH_STOPWORDS:
            continue
        tokens.add(_singularize_token(token))
    return tokens


def _important_query_phrases(query: str) -> set[str]:
    words = [
        word
        for word in _normalize_search_text(query).split()
        if len(word) >= 3 and word not in _SEARCH_STOPWORDS
    ]
    phrases = set()
    for phrase_len in range(2, min(4, len(words)) + 1):
        for idx in range(0, len(words) - phrase_len + 1):
            phrases.add(" ".join(words[idx : idx + phrase_len]))
    return phrases


def _is_context_dependent_followup(query: str) -> bool:
    """Detect follow-ups that refine prior context without naming the topic again."""
    query_norm = _normalize_search_text(query)
    if not query_norm:
        return False

    tokens = _search_tokens(query)
    topic_tokens = tokens - _CONTEXT_FOLLOWUP_DIRECTIVE_TOKENS
    has_reference = any(
        re.search(rf"\b{re.escape(word)}\b", query_norm)
        for word in _CONTEXT_FOLLOWUP_REFERENCE_WORDS
    )

    if has_reference:
        return True

    # Examples: "property", "from property", "give me the property overview".
    return bool(tokens) and not topic_tokens


def _lexical_match_score(query: str, content: str, heading: str | None = None) -> int:
    """Score exact heading/content term overlap so named sections survive reranking noise."""
    heading_text = heading or ""
    heading_norm = _normalize_search_text(heading_text)
    content_norm = _normalize_search_text(content)
    haystack_norm = f"{heading_norm} {content_norm}".strip()

    score = 0
    query_norm = _normalize_search_text(query)
    if query_norm and query_norm in haystack_norm:
        score += 8

    for phrase in _important_query_phrases(query):
        if phrase in heading_norm:
            score += 6
        elif phrase in content_norm:
            score += 4

    query_tokens = _search_tokens(query)
    if query_tokens:
        score += len(query_tokens & _search_tokens(heading_text)) * 3
        score += len(query_tokens & _search_tokens(content))

    haystack_compact = _compact_search_text(haystack_norm)
    for form_reference in _extract_form_references(query.upper()):
        form_reference_norm = _normalize_search_text(form_reference)
        form_reference_compact = _compact_search_text(form_reference)
        if form_reference_norm and form_reference_norm in heading_norm:
            score += 12
        elif form_reference_norm and form_reference_norm in content_norm:
            score += 10
        elif form_reference_compact and form_reference_compact in haystack_compact:
            score += 10

    return score


def _retrieval_score(result: dict) -> float:
    try:
        return float(result.get("score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _result_source_uri(result: dict) -> str:
    """Return the best available source URI from Bedrock retrieval metadata."""
    metadata = result.get("metadata", {}) or {}
    location = result.get("location", {}) or {}
    return (
        metadata.get("source_url")
        or metadata.get("sourceUrl")
        or metadata.get("source_uri")
        or metadata.get("sourceUri")
        or metadata.get("s3_uri")
        or metadata.get("s3Uri")
        or metadata.get("x-amz-bedrock-kb-source-uri")
        or ((location.get("s3Location") or {}).get("uri"))
        or ((location.get("webLocation") or {}).get("url"))
        or ""
    )


def _canonical_public_manual_url(source_uri: str) -> str | None:
    """Map S3/object source paths to stable public manual URLs when possible."""
    normalized = source_uri.replace("\\", "/").lower().rstrip("/")
    if not normalized:
        return None

    if normalized.startswith("http://") or normalized.startswith("https://"):
        if "bindingauthority.coactionspecialty.com/manuals/" in normalized:
            return source_uri.rstrip("/")
        if "property.html" in normalized or normalized.endswith("/property.md"):
            return PROPERTY_MANUAL_URL
        if "guide.html" in normalized or normalized.endswith("/guide.md"):
            return GL_GUIDE_MANUAL_URL

    class_code_match = re.search(r"(?:^|/)(\d{4,})\.(?:md|html)$", normalized)
    if class_code_match:
        class_code = class_code_match.group(1)
        return f"https://bindingauthority.coactionspecialty.com/manuals/{class_code}.html"

    if (
        normalized.endswith("/property.md")
        or "/property/" in normalized
        or "/property_sections/" in normalized
    ):
        return PROPERTY_MANUAL_URL

    if (
        normalized.endswith("/guide.md")
        or "/guide/" in normalized
        or "/guide_sections/" in normalized
    ):
        return GL_GUIDE_MANUAL_URL

    return None


def _source_uri_indicates_property(source_uri: str, url: str) -> bool:
    url_lower = url.replace("\\", "/").lower()
    if "property.html" in url_lower:
        return True
    if "guide.html" in url_lower or re.search(r"/\d{4,}\.html$", url_lower):
        return False
    text = f"{source_uri} {url}".replace("\\", "/").lower()
    return "property.html" in text or "/property/" in text or "/property_sections/" in text


def _source_uri_indicates_guide(source_uri: str, url: str) -> bool:
    url_lower = url.replace("\\", "/").lower()
    if "guide.html" in url_lower:
        return True
    if "property.html" in url_lower or re.search(r"/\d{4,}\.html$", url_lower):
        return False
    text = f"{source_uri} {url}".replace("\\", "/").lower()
    return "guide.html" in text or "/guide/" in text or "/guide_sections/" in text


def _clean_heading_candidate(value: Any) -> str:
    """Normalize a candidate heading and reject chunk excerpts masquerading as headings."""
    heading = re.sub(r"\s+", " ", str(value or "")).strip().strip("_*# ")
    heading = re.sub(r"^o\s+", "", heading, flags=re.IGNORECASE).strip()
    if not heading:
        return ""
    if len(heading) > 120:
        return ""
    if "|" in heading:
        return ""
    if heading.count(".") > 1:
        return ""
    return heading


def _first_markdown_heading(content: str) -> str:
    header_match = re.search(r"^#{1,4}\s*(.+)", content, re.MULTILINE)
    if not header_match:
        return ""
    return _clean_heading_candidate(header_match.group(1))


def _public_source_key(chunk_meta: dict) -> str:
    """Identify a public source at section granularity, not just URL granularity."""
    return "|".join(
        [
            str(chunk_meta.get("url") or "").strip().rstrip("/").lower(),
            str(chunk_meta.get("heading") or "").strip().lower(),
            str(chunk_meta.get("class_code") or "").strip(),
        ]
    )


def _build_search_queries(
    tool_query: str, current_query: str | None = None
) -> list[tuple[str, str]]:
    """Build a small set of retrieval queries from the model tool call and current turn."""
    query_pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add_query(label: str, raw_query: str) -> None:
        raw_query = raw_query.strip()
        if not raw_query:
            return
        expanded_query = _expand_query(raw_query)
        normalized = _normalize_search_text(expanded_query)
        if normalized and normalized not in seen:
            seen.add(normalized)
            query_pairs.append((label, expanded_query))

    add_query("ranking", _build_ranking_query(tool_query, current_query))
    add_query("tool", tool_query)
    add_query("current", current_query or "")

    return query_pairs


def _build_ranking_query(tool_query: str, current_query: str | None = None) -> str:
    """Choose the query used to rank merged KB results."""
    current_query = (current_query or "").strip()
    tool_query = tool_query.strip()
    if not current_query:
        return tool_query

    has_explicit_current_key = bool(
        re.search(r"\b(class\s*code\s*)?\d{4,}\b", current_query, flags=re.IGNORECASE)
        or re.search(
            r"\b(GL|CG|BP|CP|IL)\b",
            current_query,
            flags=re.IGNORECASE,
        )
    )
    if has_explicit_current_key:
        return current_query

    if _is_context_dependent_followup(current_query):
        return f"{tool_query} {current_query}".strip()

    if len(_search_tokens(current_query)) >= 2:
        return current_query

    return f"{tool_query} {current_query}".strip()


def _requested_manual_family(query: str) -> str | None:
    query_lower = f" {query.lower()} "
    if re.search(r"\b(cg|gl)\s*[-]?\s*\d", query_lower) or any(
        signal in query_lower
        for signal in (
            " general liability ",
            " gl ",
            " class code ",
            " liability ",
        )
    ):
        return "General Liability"

    if any(
        signal in query_lower
        for signal in (
            " property manual ",
            " property ",
            " from property ",
            " in property ",
            " for property ",
            " property coverage ",
        )
    ):
        return "Property"

    return None


def _manual_family_from_meta(chunk_meta: dict) -> str | None:
    manual_name = str(chunk_meta.get("manual_name") or "").lower()
    manual_type = str(chunk_meta.get("manual_type") or "").lower()
    combined = f"{manual_name} {manual_type}"
    if "property" in combined:
        return "Property"
    if "general liability" in combined or "guide" in combined:
        return "General Liability"
    return None


def _expand_query(query: str) -> str:
    """Expand common shorthand without steering toward hand-picked sections."""
    search_query = query
    query_lower = query.lower()
    shorthand_map = {
        "paper": "paperhanging",
        "hnoa": "hired and non-owned auto",
        "ebl": "employee benefits liability",
        "pv": "photovoltaic",
        "tria": "terrorism risk insurance",
        "bor": "broker of record",
    }
    for short, full in shorthand_map.items():
        if re.search(rf"\b{re.escape(short)}\b", query_lower) and full not in query_lower:
            search_query = f"{search_query} {full}"

    form_references = _extract_form_references(query.upper())
    if form_references:
        form_terms = ["form", "endorsement", "policy form", "coverage option"]
        form_terms.extend(sorted(form_references))
        search_query = f"{search_query} {' '.join(dict.fromkeys(form_terms))}"

    if re.search(r"(\d+)\s*(year|yr)s?\s*old", query_lower) or any(
        phrase in query_lower for phrase in ("age of", "year built")
    ):
        search_query = f"{search_query} building age eligibility year built restriction"

    for feature in (
        "extended period of indemnity",
        "builders risk",
        "agreed value",
        "ordinance or law",
        "contractor pak",
        "inland marine pac",
    ):
        if feature in query_lower:
            search_query = f"{search_query} coverage option {feature}"
            break

    if any(k in query_lower for k in ("eligible", "appetite", "prohibited", "submit")):
        search_query = f"{search_query} prohibited submit requirements eligibility"

    return search_query


def _extract_chunk_metadata(content: str, metadata: dict, s3_uri: str) -> dict:
    """Extract structured metadata (url, heading, manual_type) from a retrieved chunk."""
    injected_url_match = re.search(r"^SOURCE_URL:\s*(https?://\S+)", content, re.MULTILINE)
    if injected_url_match:
        url = injected_url_match.group(1).strip()
    elif public_url := _canonical_public_manual_url(s3_uri):
        url = public_url
    elif "full-page-crawl/" in s3_uri:
        filename = s3_uri.split("/")[-1].replace(".md", ".html")
        url = f"https://bindingauthority.coactionspecialty.com/manuals/{filename}"
    else:
        url = s3_uri or "N/A"

    manual_type_match = re.search(r"^MANUAL_TYPE:\s*(.+)", content, re.MULTILINE)
    manual_type = (
        manual_type_match.group(1).strip()
        if manual_type_match
        else (
            metadata.get("manual_type")
            or metadata.get("manualType")
            or metadata.get("MANUAL_TYPE")
            or None
        )
    )

    injected_code_match = re.search(r"^CLASS_CODE:\s*(\d+)", content, re.MULTILINE)
    section_match = re.search(r"^SECTION:\s*(.+)", content, re.MULTILINE)
    class_code = None

    if injected_code_match:
        class_code = injected_code_match.group(1)
        heading = f"Class Code {class_code}"
        if not manual_type:
            manual_type = "General Liability"
    elif section_match:
        heading = _clean_heading_candidate(section_match.group(1))
        if not manual_type:
            if _source_uri_indicates_property(s3_uri, url):
                manual_type = "Property"
            elif _source_uri_indicates_guide(s3_uri, url):
                manual_type = "General Liability Guide"
    else:
        markdown_heading = _first_markdown_heading(content)
        metadata_heading = _clean_heading_candidate(metadata.get("heading"))
        heading = metadata_heading or markdown_heading or "Manual Section"

    if not class_code:
        heading_code_match = re.search(r"\bClass Code\s+(\d{4,})\b", heading, re.IGNORECASE)
        filename = url.rstrip("/").split("/")[-1].replace(".html", "")
        if heading_code_match:
            class_code = heading_code_match.group(1)
        elif filename.isdigit():
            class_code = filename

    if manual_type and "internal" in manual_type.lower():
        manual_name = "Internal Guidelines"
    elif manual_type:
        manual_name = f"{manual_type} Manual"
    elif "internal-docs/" in s3_uri:
        manual_name = "Internal Guidelines"
    elif _source_uri_indicates_property(s3_uri, url):
        manual_name = "Property Manual"
    elif _source_uri_indicates_guide(s3_uri, url):
        manual_name = "General Liability Guide"
    else:
        manual_name = "Binding Authority Manual"

    return {
        "url": url,
        "heading": heading,
        "manual_name": manual_name,
        "manual_type": manual_type,
        "class_code": class_code,
    }


def _format_retrieved_documents(
    results: list,
    original_query: str,
    max_results: int | None = None,
) -> tuple[str, list[dict]]:
    """Format retrieved chunks into context for the LLM."""
    specific_codes = re.findall(r"(\d{4,})", original_query)

    # 1. Filter out results that do not match the specific class codes requested
    candidate_results = []
    for res in results:
        content = res.get("content", {}).get("text", "")
        if specific_codes:
            found_code = any(code in content.replace(" ", "") for code in specific_codes)
            if not found_code:
                continue
        candidate_results.append(res)

    requested_manual_family = _requested_manual_family(original_query)
    if requested_manual_family:
        family_results = []
        for res in candidate_results:
            metadata = res.get("metadata", {})
            content = res.get("content", {}).get("text", "")
            s3_uri = _result_source_uri(res)
            chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
            if _manual_family_from_meta(chunk_meta) == requested_manual_family:
                family_results.append(res)
        if family_results:
            candidate_results = family_results

    def result_lexical_score(res: dict) -> int:
        metadata = res.get("metadata", {})
        content = res.get("content", {}).get("text", "")
        s3_uri = _result_source_uri(res)
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        return _lexical_match_score(original_query, content, chunk_meta.get("heading"))

    # 2. Filter based on score, while preserving exact lexical section matches.
    sorted_candidates = sorted(
        candidate_results,
        key=lambda x: (result_lexical_score(x), _retrieval_score(x)),
        reverse=True,
    )

    filtered_results = []
    for res in sorted_candidates:
        score = _retrieval_score(res)
        lexical_score = result_lexical_score(res)
        # Keep strong vector matches, exact lexical section matches, or a small safety floor.
        if (
            score >= MIN_RELEVANCE_SCORE
            or lexical_score >= LEXICAL_MATCH_MIN_SCORE
            or len(filtered_results) < 3
        ):
            filtered_results.append(res)

    # 3. Sort deterministically, but keep exact query/section matches ahead of generic chunks.
    def get_sort_key(res):
        metadata = res.get("metadata", {})
        content = res.get("content", {}).get("text", "")
        s3_uri = _result_source_uri(res)
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        return (
            -result_lexical_score(res),
            -_retrieval_score(res),
            chunk_meta.get("url", ""),
            chunk_meta.get("heading", ""),
            content,
        )

    filtered_results.sort(key=get_sort_key)
    if max_results is not None:
        filtered_results = filtered_results[: _clamp_top_k(max_results)]

    context_parts = []
    source_metadata = []
    seen_source_keys: set[str] = set()
    source_key_to_id: dict[str, str] = {}

    for res in filtered_results:
        content = res.get("content", {}).get("text", "")
        metadata = res.get("metadata", {})
        s3_uri = _result_source_uri(res)
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        clean_content = re.sub(
            r"^(SOURCE_URL|CLASS_CODE|MANUAL_TYPE|SECTION):.*\n?", "", content, flags=re.MULTILINE
        ).strip()
        clean_content = re.sub(r"^---\s*\n", "", clean_content).strip()

        is_internal = "internal" in str(chunk_meta["manual_name"]).lower()
        if is_internal:
            source_id = "INTERNAL_DO_NOT_CITE"
        else:
            source_key = _public_source_key(chunk_meta)
            source_key_to_id.setdefault(source_key, f"S{len(source_key_to_id) + 1}")
            source_id = source_key_to_id[source_key]
            chunk_meta["source_id"] = source_id

        chunk_meta["content_text"] = clean_content

        states_found = _extract_state_abbreviations(content)
        states_line = (
            f"States Found in Document: {', '.join(sorted(states_found))}"
            if states_found
            else "States Found in Document: NONE"
        )

        queried_states = _extract_queried_states(original_query)
        state_mention_check = ""
        if queried_states:
            state_lines = _state_mentions_from_content(clean_content, queried_states)
            state_mention_check = (
                "STATE MENTION CHECK (not an eligibility verdict; explicit prohibited/refer "
                "rules override simple mentions):\n" + "\n".join(state_lines)
            )

        parts_lines = [
            f"Citation ID: {source_id}",
            f"Source URL: {chunk_meta['url'] if not is_internal else 'INTERNAL'}",
            f"Manual: {chunk_meta['manual_name']}",
            f"Heading: {chunk_meta['heading']}",
            states_line,
        ]
        if state_mention_check:
            parts_lines.append(state_mention_check)
        parts_lines.append(f"Content:\n{clean_content}")

        context_parts.append("\n".join(parts_lines))

        if not is_internal:
            source_key = _public_source_key(chunk_meta)
        if not is_internal and source_key not in seen_source_keys:
            seen_source_keys.add(source_key)
            source_metadata.append(chunk_meta)

    if not context_parts:
        return "No relevant information found in the manuals.", []

    return "\n\n".join(context_parts), source_metadata


def _format_raw_retrieved_documents(
    results: list,
    max_results: int | None = None,
) -> tuple[str, list[dict]]:
    """Format retrieved chunks without app-side ranking/filtering helpers."""
    limited_results = results[: _clamp_top_k(max_results)] if max_results is not None else results
    context_parts = []
    source_metadata = []
    source_key_to_id: dict[str, str] = {}
    seen_source_keys: set[str] = set()

    for res in limited_results:
        content = res.get("content", {}).get("text", "")
        metadata = res.get("metadata", {})
        s3_uri = _result_source_uri(res)
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        is_internal = "internal" in str(chunk_meta["manual_name"]).lower()

        if is_internal:
            source_id = "INTERNAL_DO_NOT_CITE"
        else:
            source_key = _public_source_key(chunk_meta)
            source_key_to_id.setdefault(source_key, f"S{len(source_key_to_id) + 1}")
            source_id = source_key_to_id[source_key]
            chunk_meta["source_id"] = source_id

        chunk_meta["content_text"] = content
        context_parts.append(
            "\n".join(
                [
                    f"Citation ID: {source_id}",
                    f"Source URL: {chunk_meta['url'] if not is_internal else 'INTERNAL'}",
                    f"Manual: {chunk_meta['manual_name']}",
                    f"Heading: {chunk_meta['heading']}",
                    f"Class Code: {chunk_meta.get('class_code') or 'N/A'}",
                    f"Retrieval Score: {_retrieval_score(res)}",
                    f"Content:\n{content}",
                ]
            )
        )

        if not is_internal:
            source_key = _public_source_key(chunk_meta)
            if source_key not in seen_source_keys:
                seen_source_keys.add(source_key)
                source_metadata.append(chunk_meta)

    if not context_parts:
        return "No relevant information found in the manuals.", []

    return "\n\n".join(context_parts), source_metadata


# ── Strands Tool ─────────────────────────────────────────────────────────


def _retrieve_manual_context(query: str, config: RetrieverConfig | None) -> tuple[str, list[dict]]:
    """Search configured KBs and return model context plus public source metadata."""
    if not config or not config.knowledge_base_ids:
        return "Error: Retriever not configured. No Knowledge Base IDs available.", []

    try:
        ranking_query = query if config.raw_retrieval_mode else _build_ranking_query(
            query, config.current_query
        )
        search_queries = (
            [("tool", query.strip())]
            if config.raw_retrieval_mode
            else _build_search_queries(query, config.current_query)
        )
        all_results: list[dict] = []
        bedrock_client = _get_bedrock_client(config.region)
        top_k = _clamp_top_k(config.top_k)
        candidate_count = top_k if config.raw_retrieval_mode else max(top_k * 4, top_k)
        search_type = config.search_type.upper()
        if search_type not in {"HYBRID", "SEMANTIC"}:
            search_type = "HYBRID"

        # Query all configured KBs and merge results
        for kb_id in config.knowledge_base_ids:
            for query_label, search_query in search_queries:
                if not search_query:
                    continue
                try:
                    # 1. Build the vector search configuration block
                    vector_config = {
                        "numberOfResults": candidate_count,
                        "overrideSearchType": search_type,
                    }
                    reranking_allowed = (
                        not config.raw_retrieval_mode
                        and config.reranking_enabled
                        and kb_id not in _rerank_disabled_kb_ids
                    )

                    # Only add reranking configuration if enabled globally
                    if reranking_allowed:
                        vector_config["rerankingConfiguration"] = {
                            "type": "BEDROCK_RERANKING_MODEL",
                            "bedrockRerankingConfiguration": {
                                "modelConfiguration": {
                                    "modelArn": (
                                        f"arn:aws:bedrock:{config.region}"
                                        f"::foundation-model/{_reranker_model_id}"
                                    ),
                                },
                                "numberOfRerankedResults": candidate_count,
                            },
                        }

                    retrieval_config = {"vectorSearchConfiguration": vector_config}

                    try:
                        response = bedrock_client.retrieve(
                            knowledgeBaseId=kb_id,
                            retrievalQuery={"text": search_query},
                            retrievalConfiguration=retrieval_config,
                        )
                        all_results.extend(response.get("retrievalResults", []))
                    except Exception as e:
                        # If reranking failed (e.g. AccessDeniedException for bedrock:Rerank),
                        # attempt self-healing fallback by retrying without reranking.
                        err_msg = str(e)
                        if reranking_allowed and (
                            "Rerank" in err_msg
                            or "AccessDenied" in err_msg
                            or "Forbidden" in err_msg
                            or "403" in err_msg
                        ):
                            _rerank_disabled_kb_ids.add(kb_id)
                            logger.warning(
                                "kb_retrieval_rerank_failed_falling_back",
                                kb_id=kb_id,
                                query_label=query_label,
                                error=err_msg,
                            )
                            fallback_vector_config = {
                                "numberOfResults": candidate_count,
                                "overrideSearchType": search_type,
                            }
                            fallback_retrieval_config = {
                                "vectorSearchConfiguration": fallback_vector_config
                            }

                            response = bedrock_client.retrieve(
                                knowledgeBaseId=kb_id,
                                retrievalQuery={"text": search_query},
                                retrievalConfiguration=fallback_retrieval_config,
                            )
                            all_results.extend(response.get("retrievalResults", []))
                        else:
                            raise e
                except Exception as e:
                    logger.error(
                        "kb_retrieval_failed",
                        kb_id=kb_id,
                        query_label=query_label,
                        error=str(e),
                    )

        logger.info(
            "retrieval_complete",
            result_count=len(all_results),
            kb_count=len(config.knowledge_base_ids),
            top_k=top_k,
            query_count=len(search_queries),
            raw_retrieval_mode=config.raw_retrieval_mode,
            search_type=search_type,
        )
        if config.raw_retrieval_mode:
            context, sources = _format_raw_retrieved_documents(all_results, max_results=top_k)
        else:
            context, sources = _format_retrieved_documents(
                all_results,
                ranking_query,
                max_results=top_k,
            )
        logger.info(
            "retrieval_context_prepared",
            source_count=len(sources),
            source_headings=[source.get("heading") for source in sources],
        )
        return context, sources

    except Exception as e:
        logger.error("search_manuals_failed", error=str(e))
        return f"Error searching manuals: {str(e)}", []


@tool
def search_manuals(query: str) -> str:
    """Search the Coaction underwriting manuals (General Liability and Property) using the AWS Knowledge Base.

    Args:
        query: The search query to find relevant manual content. Use this for class codes,
            eligibility, underwriting rules, form numbers, endorsements, coverage questions,
            and Coaction binding authority or manual questions.
    """
    context, sources = _retrieve_manual_context(query, _active_config())
    _set_retrieval_sources(sources)
    return context


def build_scoped_search_manuals_tool(
    *,
    knowledge_base_ids: list[str],
    region: str,
    reranking_enabled: bool,
    top_k: int,
    source_sink: list[dict],
    current_query: str | None = None,
    raw_retrieval_mode: bool = False,
    search_type: str = "HYBRID",
) -> Any:
    """Create a per-invocation search_manuals tool that writes sources into source_sink."""
    config = RetrieverConfig(
        knowledge_base_ids=tuple(knowledge_base_ids),
        region=region,
        reranking_enabled=_reranking_enabled(reranking_enabled),
        top_k=_clamp_top_k(top_k),
        current_query=current_query,
        raw_retrieval_mode=raw_retrieval_mode,
        search_type=search_type.upper(),
    )

    @tool(name="search_manuals")
    def scoped_search_manuals(query: str) -> str:
        """Search Coaction underwriting manuals for class codes, forms, endorsements, and rules."""
        context, sources = _retrieve_manual_context(query, config)
        source_sink.clear()
        source_sink.extend(sources)
        _set_retrieval_sources(sources)
        return context

    return scoped_search_manuals
