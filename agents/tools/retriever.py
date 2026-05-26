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

# ── Module State ─────────────────────────────────────────────────────────

# Default config is used by direct CLI/script invocations. FastAPI requests set a
# request-local context so concurrent agents cannot leak KB IDs or citations.
_reranker_model_id: str = os.getenv("RERANKER_MODEL_ID", "cohere.rerank-v3-5:0")
_bedrock_clients: dict[str, Any] = {}
_default_retriever_config: "RetrieverConfig | None" = None
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


def configure_retriever(
    knowledge_base_ids: list[str],
    region: str = "us-east-1",
    reranking_enabled: bool = True,
    top_k: int = 5,
) -> None:
    """Configure the retriever with KB IDs from an ExecutionProfile."""
    global _default_retriever_config
    _default_retriever_config = RetrieverConfig(
        knowledge_base_ids=tuple(knowledge_base_ids),
        region=region,
        reranking_enabled=reranking_enabled,
        top_k=_clamp_top_k(top_k),
    )
    _get_bedrock_client(region)
    logger.info(
        "retriever_configured",
        kb_ids=knowledge_base_ids,
        region=region,
        reranking_enabled=reranking_enabled,
        top_k=_clamp_top_k(top_k),
    )


def set_retriever_context(
    knowledge_base_ids: list[str],
    region: str = "us-east-1",
    reranking_enabled: bool = True,
    top_k: int = 5,
) -> Token:
    """Set request-local retriever config and return a token for reset."""
    return _retriever_config.set(
        RetrieverConfig(
            knowledge_base_ids=tuple(knowledge_base_ids),
            region=region,
            reranking_enabled=reranking_enabled,
            top_k=_clamp_top_k(top_k),
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


def _expand_query(query: str) -> str:
    """Expand shorthand terms and eligibility keywords.

    Applies context-aware expansion:
    - Property coverage feature queries → property-specific terms (not GL class codes)
    - Age eligibility queries → normalized age terms
    - Named coverage features → exact feature name emphasis
    - General eligibility queries → class code terms
    """
    search_query = query
    shorthand_map = {
        "paper": "paperhanging",
        "hnoa": "hired and non-owned auto",
        "ebl": "employee benefits liability",
        "tria": "terrorism risk insurance",
        "bor": "broker of record",
    }
    query_lower = query.lower()
    for short, full in shorthand_map.items():
        if short in query_lower and full not in query_lower:
            search_query = f"{search_query} {full}"

    form_matches = re.findall(
        r"\b([A-Z]{2})\s*[-]?\s*(\d{3,4})(?:\s*[-]?\s*(\d{2,4}))?\b",
        query.upper(),
    )
    if form_matches:
        form_terms = ["form", "endorsement", "policy form", "class-specific forms", "purpose"]
        for prefix, number, edition in form_matches:
            compact = f"{prefix} {number} {edition}".strip()
            form_terms.extend([compact, number])
            if prefix in {"GL", "CG"}:
                alternate_prefix = "CG" if prefix == "GL" else "GL"
                form_terms.append(f"{alternate_prefix} {number} {edition}".strip())
        search_query = f"{search_query} {' '.join(dict.fromkeys(form_terms))}"

    # --- Property coverage feature detection ---
    # Questions like "Do you provide coverage for fences?" are about Property
    # coverage features, NOT GL class codes. Route accordingly.
    property_feature_items = [
        "fence",
        "outdoor property",
        "equipment",
        "inland marine",
        "property in the open",
        "builder",
        "property extension",
    ]
    coverage_verbs = ["coverage for", "cover for", "provide coverage", "offer property"]
    is_property_feature = any(item in query_lower for item in property_feature_items) and any(
        verb in query_lower for verb in coverage_verbs
    )

    # --- Age eligibility normalization ---
    age_match = re.search(r"(\d+)\s*(year|yr)s?\s*old", query_lower)
    has_age_query = age_match or "age of" in query_lower or "year built" in query_lower
    if has_age_query:
        search_query = f"{search_query} building age eligibility year built restriction"

    # --- Named coverage features ---
    named_features = [
        "extended period of indemnity",
        "builders risk",
        "agreed value",
        "ordinance or law",
        "contractor pak",
        "inland marine pac",
    ]
    for feature in named_features:
        if feature in query_lower:
            search_query = f"{search_query} coverage option {feature}"
            break

    # --- Eligibility expansion ---
    eligibility_keywords = [
        "acceptable",
        "eligible",
        "appetite",
        "suitability",
        "cover",
        "prohibited",
    ]
    if any(k in query_lower for k in eligibility_keywords):
        if is_property_feature:
            # Property feature queries → steer toward property extensions
            search_query = f"{search_query} property extension coverage option included"
        else:
            # Business eligibility queries → steer toward GL class codes
            search_query = f"{search_query} class code prohibited submit requirements eligibility"
    return search_query


def _extract_chunk_metadata(content: str, metadata: dict, s3_uri: str) -> dict:
    """Extract structured metadata (url, heading, manual_type) from a retrieved chunk."""
    injected_url_match = re.search(r"^SOURCE_URL:\s*(https?://\S+)", content, re.MULTILINE)
    if injected_url_match:
        url = injected_url_match.group(1).strip()
    elif "full-page-crawl/" in s3_uri:
        filename = s3_uri.split("/")[-1].replace(".md", ".html")
        url = f"https://bindingauthority.coactionspecialty.com/manuals/{filename}"
    else:
        url = s3_uri or "N/A"

    manual_type_match = re.search(r"^MANUAL_TYPE:\s*(.+)", content, re.MULTILINE)
    manual_type = manual_type_match.group(1).strip() if manual_type_match else None

    injected_code_match = re.search(r"^CLASS_CODE:\s*(\d+)", content, re.MULTILINE)
    section_match = re.search(r"^SECTION:\s*(.+)", content, re.MULTILINE)
    class_code = None

    if injected_code_match:
        class_code = injected_code_match.group(1)
        heading = f"Class Code {class_code}"
        if not manual_type:
            manual_type = "General Liability"
    elif section_match:
        heading = section_match.group(1).strip().strip("_")
        if not manual_type:
            if "property" in url.lower():
                manual_type = "Property"
            elif "guide" in url.lower():
                manual_type = "General Liability Guide"
    else:
        header_match = re.search(r"^#+\s*(.+)", content, re.MULTILINE)
        heading = metadata.get("heading") or (
            header_match.group(1).strip().strip("_*") if header_match else "Manual Section"
        )

    if not class_code:
        heading_code_match = re.search(r"\bClass Code\s+(\d{4,})\b", heading, re.IGNORECASE)
        filename = url.rstrip("/").split("/")[-1].replace(".html", "")
        if heading_code_match:
            class_code = heading_code_match.group(1)
        elif filename.isdigit():
            class_code = filename

    if manual_type:
        manual_name = f"{manual_type} Manual"
    elif "internal-docs/" in s3_uri:
        manual_name = "Internal Guidelines"
    elif "property" in url.lower():
        manual_name = "Property Manual"
    elif "guide" in url.lower():
        manual_name = "General Liability Guide"
    else:
        manual_name = "Binding Authority Manual"

    return {
        "url": url,
        "heading": heading,
        "manual_name": manual_name,
        "class_code": class_code,
    }


def _format_retrieved_documents(results: list, original_query: str) -> tuple[str, list[dict]]:
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

    # 2. Fix C: Filter based on MIN_RELEVANCE_SCORE, but ensure we keep a minimum of 3 results (if available)
    # to protect against score jitter throwing out all context.
    sorted_candidates = sorted(candidate_results, key=lambda x: x.get("score", 0), reverse=True)

    filtered_results = []
    for res in sorted_candidates:
        score = res.get("score", 0)
        # Keep if meets minimum relevance threshold, OR if we have less than 3 results so far
        if score >= MIN_RELEVANCE_SCORE or len(filtered_results) < 3:
            filtered_results.append(res)

    # 3. Fix B: Sort the filtered results deterministically to ensure identical prompt ordering
    # across runs, eliminating "lost in the middle" attention shifting.
    def get_sort_key(res):
        metadata = res.get("metadata", {})
        content = res.get("content", {}).get("text", "")
        s3_uri = metadata.get("source_url") or metadata.get("sourceUrl") or ""
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        return (chunk_meta.get("url", ""), chunk_meta.get("heading", ""), content)

    filtered_results.sort(key=get_sort_key)

    context_parts = []
    source_metadata = []
    seen_urls: set[str] = set()
    url_to_source_id: dict[str, str] = {}

    for res in filtered_results:
        content = res.get("content", {}).get("text", "")
        metadata = res.get("metadata", {})
        s3_uri = metadata.get("source_url") or metadata.get("sourceUrl") or ""
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)
        is_internal = chunk_meta["manual_name"] == "Internal Guidelines"
        if is_internal:
            source_id = "INTERNAL_DO_NOT_CITE"
        else:
            url_to_source_id.setdefault(chunk_meta["url"], f"S{len(url_to_source_id) + 1}")
            source_id = url_to_source_id[chunk_meta["url"]]
            chunk_meta["source_id"] = source_id

        clean_content = re.sub(
            r"^(SOURCE_URL|CLASS_CODE|MANUAL_TYPE|SECTION):.*\n?", "", content, flags=re.MULTILINE
        ).strip()
        clean_content = re.sub(r"^---\s*\n", "", clean_content).strip()
        chunk_meta["content_text"] = clean_content

        states_found = _extract_state_abbreviations(content)
        states_line = (
            f"States Found in Document: {', '.join(sorted(states_found))}"
            if states_found
            else "States Found in Document: NONE"
        )

        queried_states = _extract_queried_states(original_query)
        eligibility_verdict = ""
        if queried_states:
            verdicts = []
            for state_name, state_abbrev in queried_states:
                if state_abbrev in states_found:
                    verdicts.append(
                        f"  - {state_name} ({state_abbrev}): ELIGIBLE (found in document)"
                    )
                else:
                    verdicts.append(
                        f"  - {state_name} ({state_abbrev}): NOT ELIGIBLE (not found in document)"
                    )
            eligibility_verdict = (
                "PRE-COMPUTED STATE ELIGIBILITY (authoritative, do not override):\n"
                + "\n".join(verdicts)
            )

        parts_lines = [
            f"Citation ID: {source_id}",
            f"Source URL: {chunk_meta['url'] if not is_internal else 'INTERNAL'}",
            f"Manual: {chunk_meta['manual_name']}",
            f"Heading: {chunk_meta['heading']}",
            states_line,
        ]
        if eligibility_verdict:
            parts_lines.append(eligibility_verdict)
        parts_lines.append(f"Content:\n{clean_content}")

        context_parts.append("\n".join(parts_lines))

        if not is_internal and chunk_meta["url"] not in seen_urls:
            seen_urls.add(chunk_meta["url"])
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
        search_query = _expand_query(query)
        all_results: list[dict] = []
        bedrock_client = _get_bedrock_client(config.region)
        top_k = _clamp_top_k(config.top_k)
        candidate_count = max(top_k * 4, top_k)

        # Query all configured KBs and merge results
        for kb_id in config.knowledge_base_ids:
            try:
                # 1. Build the vector search configuration block
                vector_config = {
                    "numberOfResults": candidate_count,
                    "overrideSearchType": "HYBRID",
                }

                # Only add reranking configuration if enabled globally
                if config.reranking_enabled:
                    vector_config["rerankingConfiguration"] = {
                        "type": "BEDROCK_RERANKING_MODEL",
                        "bedrockRerankingConfiguration": {
                            "modelConfiguration": {
                                "modelArn": (
                                    f"arn:aws:bedrock:{config.region}"
                                    f"::foundation-model/{_reranker_model_id}"
                                ),
                            },
                            "numberOfRerankedResults": top_k,
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
                    # attempt self-healing fallback by retrying without reranking
                    err_msg = str(e)
                    if config.reranking_enabled and (
                        "Rerank" in err_msg
                        or "AccessDenied" in err_msg
                        or "Forbidden" in err_msg
                        or "403" in err_msg
                    ):
                        logger.warning(
                            "kb_retrieval_rerank_failed_falling_back",
                            kb_id=kb_id,
                            error=err_msg,
                        )
                        fallback_vector_config = {
                            "numberOfResults": top_k,
                            "overrideSearchType": "HYBRID",
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
                logger.error("kb_retrieval_failed", kb_id=kb_id, error=str(e))

        logger.info(
            "retrieval_complete",
            result_count=len(all_results),
            kb_count=len(config.knowledge_base_ids),
            top_k=top_k,
        )
        context, sources = _format_retrieved_documents(all_results, query)
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
) -> Any:
    """Create a per-invocation search_manuals tool that writes sources into source_sink."""
    config = RetrieverConfig(
        knowledge_base_ids=tuple(knowledge_base_ids),
        region=region,
        reranking_enabled=reranking_enabled,
        top_k=_clamp_top_k(top_k),
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
