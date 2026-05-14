# coaction_agent_platform/agents/tools/retriever.py
"""Configurable Bedrock KB retrieval tool for the Strands agent.

Ported from coactionbot/app/services/bedrock_retriever.py with full configurability.
Accepts KB IDs at runtime from ExecutionProfile instead of hardcoded env vars.
"""

import re
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

# Configured at agent initialization time
_bedrock_client = None
_knowledge_base_ids: list[str] = []
_region: str = "us-east-1"
_last_retrieval_sources: list[dict] = []


def configure_retriever(
    knowledge_base_ids: list[str],
    region: str = "us-east-1",
) -> None:
    """Configure the retriever with KB IDs from an ExecutionProfile."""
    global _knowledge_base_ids, _region, _bedrock_client
    _knowledge_base_ids = knowledge_base_ids
    _region = region
    _bedrock_client = boto3.client("bedrock-agent-runtime", region_name=region)
    logger.info("retriever_configured", kb_ids=knowledge_base_ids, region=region)


def get_last_retrieval_sources() -> list[dict]:
    """Return source metadata from the most recent search_manuals call."""
    return list(_last_retrieval_sources)


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
    """Expand shorthand terms and eligibility keywords."""
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

    eligibility_keywords = [
        "acceptable",
        "eligible",
        "appetite",
        "suitability",
        "cover",
        "prohibited",
    ]
    if any(k in query_lower for k in eligibility_keywords):
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

    if manual_type:
        manual_name = f"{manual_type} Manual"
    elif "property" in url.lower():
        manual_name = "Property Manual"
    elif "guide" in url.lower():
        manual_name = "General Liability Guide"
    else:
        manual_name = "Binding Authority Manual"

    return {"url": url, "heading": heading, "manual_name": manual_name}


def _format_retrieved_documents(results: list, original_query: str) -> tuple[str, list[dict]]:
    """Format retrieved chunks into context for the LLM."""
    specific_codes = re.findall(r"(\d{4,})", original_query)

    context_parts = []
    source_metadata = []
    seen_urls: set[str] = set()

    for res in results:
        score = res.get("score", 0)
        if score < MIN_RELEVANCE_SCORE:
            continue

        content = res.get("content", {}).get("text", "")
        metadata = res.get("metadata", {})

        if specific_codes:
            found_code = any(code in content.replace(" ", "") for code in specific_codes)
            if not found_code:
                continue

        s3_uri = metadata.get("source_url") or metadata.get("sourceUrl") or ""
        chunk_meta = _extract_chunk_metadata(content, metadata, s3_uri)

        clean_content = re.sub(
            r"^(SOURCE_URL|CLASS_CODE|MANUAL_TYPE|SECTION):.*\n?", "", content, flags=re.MULTILINE
        ).strip()
        clean_content = re.sub(r"^---\s*\n", "", clean_content).strip()

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
            f"Source: {chunk_meta['url']}",
            f"Manual: {chunk_meta['manual_name']}",
            f"Heading: {chunk_meta['heading']}",
            states_line,
        ]
        if eligibility_verdict:
            parts_lines.append(eligibility_verdict)
        parts_lines.append(f"Content:\n{clean_content}")

        context_parts.append("\n".join(parts_lines))

        if chunk_meta["url"] not in seen_urls:
            seen_urls.add(chunk_meta["url"])
            source_metadata.append(chunk_meta)

    if not context_parts:
        return "No relevant information found in the manuals.", []

    return "\n\n".join(context_parts), source_metadata


# ── Strands Tool ─────────────────────────────────────────────────────────


@tool
def search_manuals(query: str) -> str:
    """Search the Coaction underwriting manuals (General Liability and Property) using the AWS Knowledge Base.

    Args:
        query: The search query to find relevant manual content.
    """
    global _last_retrieval_sources

    if not _bedrock_client or not _knowledge_base_ids:
        _last_retrieval_sources = []
        return "Error: Retriever not configured. No Knowledge Base IDs available."

    try:
        search_query = _expand_query(query)
        all_results: list[dict] = []

        # Query all configured KBs and merge results
        for kb_id in _knowledge_base_ids:
            try:
                response = _bedrock_client.retrieve(
                    knowledgeBaseId=kb_id,
                    retrievalQuery={"text": search_query},
                    retrievalConfiguration={
                        "vectorSearchConfiguration": {
                            "numberOfResults": 10,
                            "overrideSearchType": "HYBRID",
                        }
                    },
                )
                all_results.extend(response.get("retrievalResults", []))
            except Exception as e:
                logger.error("kb_retrieval_failed", kb_id=kb_id, error=str(e))

        logger.info(
            "retrieval_complete", result_count=len(all_results), kb_count=len(_knowledge_base_ids)
        )
        context, sources = _format_retrieved_documents(all_results, query)
        _last_retrieval_sources = sources
        return context

    except Exception as e:
        logger.error("search_manuals_failed", error=str(e))
        _last_retrieval_sources = []
        return f"Error searching manuals: {str(e)}"
