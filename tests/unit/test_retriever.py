from agents.tools.retriever import (
    RetrieverConfig,
    _augment_with_local_public_manual_sections,
    _build_ranking_query,
    _build_search_queries,
    _expand_query,
    _extract_form_references,
    _format_retrieved_documents,
    _matching_property_section_headings,
    _requested_manual_family,
    _reranking_enabled,
    _retrieve_manual_context,
    build_scoped_search_manuals_tool,
)


PROPERTY_URL = "https://bindingauthority.coactionspecialty.com/manuals/property.html"


def _result(content: str, score: float) -> dict:
    return {
        "content": {"text": content},
        "metadata": {"source_url": PROPERTY_URL},
        "score": score,
    }


def _s3_result(content: str, score: float, uri: str, metadata: dict | None = None) -> dict:
    return {
        "content": {"text": content},
        "location": {"s3Location": {"uri": uri}},
        "metadata": metadata or {},
        "score": score,
    }


OPTIONAL_COVERAGES = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
SECTION: Optional Coverages
---
#  _Optional Coverages_
| **Coverage Option** | **Form Number(s)** |
| Property Extension | PR 0275 0123 |
"""


APPETITE = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
SECTION: Appetite
---
#  _Appetite_
Coaction Specialty is currently a market for package and monoline property business for Building, Business Personal Property, Business Income and Tenant Improvements. All occupants must fit our GL appetite.
"""


SOLAR_PANELS = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
SECTION: Solar Panels
---
#  _Solar Panels_
If Solar Panels are attached to a building, include the values in the building limit.
"""


TRIPLE_NET_PROPERTY = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
SECTION: Triple Net Lease
---
#  _Triple Net Lease_
Buildings with a triple net lease should be referred to your Coaction underwriter.
Attach form CP 12 19 Additional Insured - Building Owner.
"""


GL_CLASS_91580 = """SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/91580.html
MANUAL_TYPE: General Liability
CLASS_CODE: 91580
---
# Class Code 91580
Contractors - subcontracted work - in connection with building construction.
"""


GL_CLASS_60010 = """SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/60010.html
MANUAL_TYPE: General Liability
CLASS_CODE: 60010
---
# Class Code 60010
60010 - Apartment Buildings

# Submit
- 25%+ Subsidized housing (Does not apply in New York)

# Prohibited
- Apartments in NY or NJ
"""


GL_VACANT_BUILDING = """SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/68604.html
MANUAL_TYPE: General Liability
CLASS_CODE: 68604
---
# Class Code 68604
68604 - Vacant Building - Factories

Property Notes:
1180 Vacant Building
- We are not a market for vacant buildings in California.
"""


PROPERTY_VACANT_BUILDINGS = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
SECTION: Vacant Buildings
---
#  _Vacant Buildings_
Buildings continuously vacant for more than 24-months require Coaction underwriter approval.
Structural renovations require Coaction underwriter approval.
Vacant Buildings in CA are prohibited.
"""


WHOLE_PROPERTY_MANUAL = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
---
# Property Manual

## Appetite
Coaction Specialty is currently a market for package and monoline property business for Building, Business Personal Property, Business Income and Tenant Improvements. All occupants must fit our GL appetite.

## Inspections
Physical inspections are required on all buildings and for any TIV at one location over $250,000.
Inspections are required at a minimum of every three (3) years.

## Optional Coverages
Spoilage Coverage is optional.

## Vacant Buildings
Buildings continuously vacant for more than 24-months require Coaction underwriter approval.
Structural renovations require Coaction underwriter approval.

## Wildfire Guide
Risk meter reports are required on all West Coast property exposures.
"""


CG_2294_GUIDE = """SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/guide.html
SECTION: _Additional Insured and Coverage Options_
---
# _Additional Insured and Coverage Options_
| CG 2294 - Exclusion - Damage to Work Performed By Subcontractors On Your Behalf | 10% credit added (91580); 5% credit added (All other contractors) |
"""


INTERNAL_INSPECTIONS = """# Binding Authority and Brokerage Light Internal Guidelines

# Inspections
- Internal inspection rules.
"""


def test_expand_query_adds_solar_panel_property_terms():
    expanded = _expand_query("Solar Panels in Property").lower()

    assert "photovoltaic panels" in expanded
    assert "attached building" in expanded
    assert "building limit" in expanded


def test_build_search_queries_includes_tool_query_and_current_user_turn():
    queries = _build_search_queries(
        "Property optional coverages",
        "What does GL class code 91580 cover?",
    )

    expanded_queries = [query for _, query in queries]
    assert any("Property optional coverages" in query for query in expanded_queries)
    assert any("91580" in query for query in expanded_queries)


def test_build_search_queries_adds_exact_property_section_heading_hint():
    queries = _build_search_queries("Wildfire Guide", "Wildfire Guide")

    assert any(
        label == "property_section_exact" and "SECTION: Wildfire Guide" in query
        for label, query in queries
    )


def test_build_search_queries_adds_appetite_property_hint():
    queries = _build_search_queries("Details about Appetite", "Details about Appetite")

    assert any(
        label == "property_section_exact"
        and "SECTION: Appetite" in query
        and "package and monoline property business" in query
        for label, query in queries
    )


def test_build_search_queries_adds_vacant_buildings_property_hint():
    queries = _build_search_queries("Vacant building", "Vacant building")

    assert any(
        label == "property_section_exact"
        and "SECTION: Vacant Buildings" in query
        and "continuously vacant" in query
        for label, query in queries
    )


def test_build_search_queries_adds_inspections_property_hint_for_lob_followup():
    queries = _build_search_queries("Property", "Inspections Property")

    assert any(
        label == "property_section_exact"
        and "SECTION: Inspections" in query
        and "Physical inspections required" in query
        for label, query in queries
    )


def test_matching_property_section_headings_detects_known_property_sections():
    assert _matching_property_section_headings("Tell me about Wildfire Guide") == ["Wildfire Guide"]


def test_exact_property_section_heading_requests_property_family():
    assert _requested_manual_family("Inspections") == "Property"


def test_exact_property_section_with_extra_words_requests_property_family():
    assert _requested_manual_family("Details about Appetite") == "Property"


def test_build_ranking_query_uses_tool_context_for_short_followup():
    ranking_query = _build_ranking_query("Solar Panels Property", "property")

    assert ranking_query == "Solar Panels Property property"


def test_build_ranking_query_keeps_topic_for_yes_property_followup():
    ranking_query = _build_ranking_query("Inspections Property", "yes property")

    assert ranking_query == "Inspections Property yes property"


def test_build_ranking_query_keeps_topic_for_context_dependent_property_followup():
    current_query = "I need it from the property and give me an overview of it"
    ranking_query = _build_ranking_query("Triple Net Lease Property overview", current_query)

    assert ranking_query == f"Triple Net Lease Property overview {current_query}"


def test_build_ranking_query_uses_current_turn_for_explicit_gl_switch():
    ranking_query = _build_ranking_query(
        "Property optional coverages",
        "What does GL class code 91580 cover?",
    )

    assert ranking_query == "What does GL class code 91580 cover?"


def test_extract_form_references_normalizes_spaced_and_compact_forms():
    references = _extract_form_references("What is CG 22 94?")

    assert "CG2294" in references
    assert "CG 2294" in references
    assert "CG 22 94" in references


def test_extract_form_references_splits_four_digit_forms_into_official_spacing():
    references = _extract_form_references("What is CG 2294?")

    assert "CG2294" in references
    assert "CG 2294" in references
    assert "CG 22 94" in references


def test_expand_query_adds_compact_form_variant_for_spaced_form():
    expanded = _expand_query("What is CG 22 94?")

    assert "CG2294" in expanded
    assert "CG 2294" in expanded


def test_expand_query_adds_known_form_title_hint_for_tricky_forms():
    expanded = _expand_query("What is CG 2294?")

    assert "Damage to Work Performed By Subcontractors" in expanded
    assert "contractors credit 91580" in expanded


def test_build_search_queries_adds_gl_guide_form_section_hint():
    queries = _build_search_queries("What is CG 2294?", "What is CG 2294?")

    assert any(
        label == "gl_guide_form_exact"
        and "Additional Insured and Coverage Options" in query
        and "CG 2294" in query
        and "Damage to Work Performed By Subcontractors" in query
        for label, query in queries
    )


def test_format_promotes_exact_solar_section_over_generic_property_result():
    context, sources = _format_retrieved_documents(
        [
            _result(OPTIONAL_COVERAGES, 0.95),
            _result(SOLAR_PANELS, 0.05),
        ],
        "Solar Panels in Property",
        max_results=1,
    )

    assert "Heading: Solar Panels" in context
    assert "If Solar Panels are attached to a building" in context
    assert "Optional Coverages" not in context
    assert sources[0]["heading"] == "Solar Panels"
    assert sources[0]["source_id"] == "S1"


def test_format_keeps_same_url_property_sections_as_distinct_sources():
    context, sources = _format_retrieved_documents(
        [
            _result(OPTIONAL_COVERAGES, 0.5),
            _result(SOLAR_PANELS, 0.5),
        ],
        "Property optional coverages and solar panels",
        max_results=2,
    )

    assert "Citation ID: S1" in context
    assert "Citation ID: S2" in context
    assert {source["heading"] for source in sources} == {"Optional Coverages", "Solar Panels"}
    assert len({source["source_id"] for source in sources}) == 2


def test_format_maps_full_property_ingest_chunk_to_public_property_url():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                "# Wildfire Guide\nRisk meter reports are required on all West Coast property exposures.",
                0.95,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            )
        ],
        "Wildfire Guide",
        max_results=1,
    )

    assert "Manual: Property Manual" in context
    assert (
        "Source URL: https://bindingauthority.coactionspecialty.com/manuals/property.html"
        in context
    )
    assert sources[0]["url"] == PROPERTY_URL
    assert sources[0]["heading"] == "Wildfire Guide"


def test_format_prefers_property_manual_when_user_explicitly_requests_property():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                GL_VACANT_BUILDING,
                0.95,
                "s3://vega-binding-authority/data/full_manuals/68604.md",
            ),
            _s3_result(
                PROPERTY_VACANT_BUILDINGS,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Vacant building I want it from property",
        max_results=1,
    )

    assert "Manual: Property Manual" in context
    assert "Heading: Vacant Buildings" in context
    assert "Class Code 68604" not in context
    assert sources[0]["manual_name"] == "Property Manual"


def test_format_extracts_requested_property_side_heading_from_whole_manual_chunk():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                WHOLE_PROPERTY_MANUAL,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Vacant building property",
        max_results=1,
    )

    assert "Heading: Vacant Buildings" in context
    assert "# Property Manual" not in context
    assert "Optional Coverages" not in context
    assert "Buildings continuously vacant" in context
    assert sources[0]["heading"] == "Vacant Buildings"
    assert sources[0]["content_text"].startswith("## Vacant Buildings")


def test_format_extracts_inspections_side_heading_from_whole_manual_chunk():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                WHOLE_PROPERTY_MANUAL,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Inspections Property",
        max_results=1,
    )

    assert "Heading: Inspections" in context
    assert "Physical inspections are required" in context
    assert "Optional Coverages" not in context
    assert sources[0]["heading"] == "Inspections"


def test_format_extracts_appetite_side_heading_from_whole_manual_chunk():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                WHOLE_PROPERTY_MANUAL,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Details about Appetite",
        max_results=1,
    )

    assert "Heading: Appetite" in context
    assert "package and monoline property business" in context
    assert "Inspections" not in context
    assert sources[0]["heading"] == "Appetite"


def test_format_prefers_public_property_section_over_internal_for_exact_heading():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                INTERNAL_INSPECTIONS,
                0.99,
                "s3://vega-binding-authority/internal-docs/binding-authority.md",
            ),
            _s3_result(
                WHOLE_PROPERTY_MANUAL,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Inspections",
        max_results=1,
    )

    assert "Manual: Property Manual" in context
    assert "Heading: Inspections" in context
    assert "Physical inspections are required" in context
    assert "Internal Guidelines" not in context
    assert sources[0]["manual_name"] == "Property Manual"


def test_format_prefers_public_property_appetite_over_internal_or_generic_results():
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                "# Binding Authority and Brokerage Light Internal Guidelines\n\n# Appetite\n- Internal appetite rules.",
                0.99,
                "s3://vega-binding-authority/internal-docs/binding-authority.md",
            ),
            _result(OPTIONAL_COVERAGES, 0.8),
            _s3_result(
                WHOLE_PROPERTY_MANUAL,
                0.35,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
            ),
        ],
        "Details about Appetite",
        max_results=1,
    )

    assert "Manual: Property Manual" in context
    assert "Heading: Appetite" in context
    assert "package and monoline property business" in context
    assert "Internal Guidelines" not in context
    assert "Optional Coverages" not in context
    assert sources[0]["heading"] == "Appetite"


def test_format_uses_clean_markdown_heading_over_noisy_bedrock_heading_metadata():
    noisy_heading = (
        "Premium Modification Debits and increases in Minimum Premium are within your authority. "
        "Credits or lowering of Minimum Premium require Coaction underwriter approval. "
        "# Prohibited Property risks in Lava Zones 1 or 2"
    )
    context, sources = _format_retrieved_documents(
        [
            _s3_result(
                (
                    "Premium Modification\n\n"
                    "Debits and increases in Minimum Premium are within your authority.\n\n"
                    "# Prohibited\n\n"
                    "- Property risks in Lava Zones 1 or 2"
                ),
                0.95,
                "s3://vega-binding-authority/data/bedrock_ingest/property/property.md",
                metadata={"heading": noisy_heading},
            )
        ],
        "Is this prohibited Property risks in Lava Zones 1 or 2",
        max_results=1,
    )

    assert "Heading: Prohibited" in context
    assert sources[0]["heading"] == "Prohibited"


def test_state_mentions_do_not_mark_prohibited_state_mentions_as_eligible():
    context, sources = _format_retrieved_documents(
        [
            {
                "content": {"text": GL_CLASS_60010},
                "metadata": {
                    "source_url": "https://bindingauthority.coactionspecialty.com/manuals/60010.html"
                },
                "score": 0.95,
            }
        ],
        "Are you currently in market for Apartments in NY or NJ",
        max_results=1,
    )

    assert "STATE MENTION CHECK" in context
    assert "New York (NY): PROHIBITED - - Apartments in NY or NJ" in context
    assert "New Jersey (NJ): PROHIBITED - - Apartments in NY or NJ" in context
    assert "ELIGIBLE (found in document)" not in context
    assert "PRE-COMPUTED STATE ELIGIBILITY" not in context
    assert sources[0]["class_code"] == "60010"


def test_local_public_manual_fallback_adds_missing_solar_section_when_enabled(monkeypatch):
    monkeypatch.setenv("VEGA_LOCAL_MANUAL_FALLBACK", "1")

    augmented = _augment_with_local_public_manual_sections(
        [_result(OPTIONAL_COVERAGES, 0.95)],
        "Solar Panels Property",
        limit=5,
    )

    assert any("SECTION: Solar Panels" in item["content"]["text"] for item in augmented)


def test_local_public_manual_fallback_adds_missing_guide_form_section_when_enabled(
    monkeypatch,
):
    monkeypatch.setenv("VEGA_LOCAL_MANUAL_FALLBACK", "1")

    augmented = _augment_with_local_public_manual_sections(
        [],
        "What is CG 22 94?",
        limit=5,
    )

    assert any("CG 2294" in item["content"]["text"] for item in augmented)


def test_local_public_manual_fallback_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VEGA_LOCAL_MANUAL_FALLBACK", raising=False)
    augmented = _augment_with_local_public_manual_sections(
        [],
        "Solar Panels Property",
        limit=5,
    )

    assert augmented == []


def test_local_public_manual_fallback_can_be_disabled(monkeypatch):
    monkeypatch.setenv("VEGA_LOCAL_MANUAL_FALLBACK", "0")

    augmented = _augment_with_local_public_manual_sections(
        [],
        "Solar Panels Property",
        limit=5,
    )

    assert augmented == []


def test_reranking_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VEGA_RERANKING_ENABLED", raising=False)

    assert not _reranking_enabled(True)


def test_reranking_requires_explicit_env_opt_in(monkeypatch):
    monkeypatch.setenv("VEGA_RERANKING_ENABLED", "1")

    assert _reranking_enabled(True)
    assert not _reranking_enabled(False)


def test_scoped_search_tool_disables_reranking_by_default(monkeypatch):
    monkeypatch.delenv("VEGA_RERANKING_ENABLED", raising=False)

    class FakeBedrockClient:
        def __init__(self):
            self.retrieval_configs = []

        def retrieve(self, **kwargs):
            self.retrieval_configs.append(kwargs["retrievalConfiguration"])
            return {"retrievalResults": []}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    tool = build_scoped_search_manuals_tool(
        knowledge_base_ids=["kb-public"],
        region="us-east-1",
        reranking_enabled=True,
        top_k=5,
        source_sink=[],
        current_query="Vacant building",
    )

    tool("Vacant building")

    assert fake_client.retrieval_configs
    assert all(
        "rerankingConfiguration" not in config["vectorSearchConfiguration"]
        for config in fake_client.retrieval_configs
    )


def test_format_promotes_guide_form_match_for_spaced_form_query():
    context, sources = _format_retrieved_documents(
        [
            _result(OPTIONAL_COVERAGES, 0.95),
            _result(CG_2294_GUIDE, 0.05),
        ],
        "What is CG 22 94?",
        max_results=1,
    )

    assert "Heading: Additional Insured and Coverage Options" in context
    assert "CG 2294 - Exclusion" in context
    assert sources[0]["manual_name"] == "General Liability Guide Manual"


def test_retrieval_uses_gl_guide_form_hint_for_cg_2294(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "Additional Insured and Coverage Options" in query_text:
                return {"retrievalResults": [_result(CG_2294_GUIDE, 0.45)]}
            return {"retrievalResults": []}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "What is CG 2294?",
        RetrieverConfig(
            knowledge_base_ids=("kb-public",),
            reranking_enabled=False,
            current_query="What is CG 2294?",
        ),
    )

    assert any("CG 22 94" in query for query in fake_client.queries)
    assert any("Additional Insured and Coverage Options" in query for query in fake_client.queries)
    assert "Heading: Additional Insured and Coverage Options" in context
    assert "CG 2294 - Exclusion" in context
    assert sources[0]["manual_name"] == "General Liability Guide Manual"


def test_retrieval_uses_current_turn_when_tool_query_is_stale(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "91580" in query_text:
                return {"retrievalResults": [_result(GL_CLASS_91580, 0.95)]}
            return {"retrievalResults": [_result(OPTIONAL_COVERAGES, 0.95)]}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "Property optional coverages",
        RetrieverConfig(
            knowledge_base_ids=("kb-public",),
            reranking_enabled=False,
            current_query="What does GL class code 91580 cover?",
        ),
    )

    assert any("91580" in query for query in fake_client.queries)
    assert "Heading: Class Code 91580" in context
    assert "Optional Coverages" not in context
    assert sources[0]["class_code"] == "91580"


def test_retrieval_keeps_prior_topic_for_context_dependent_property_followup(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "Triple Net Lease" in query_text:
                return {"retrievalResults": [_result(TRIPLE_NET_PROPERTY, 0.35)]}
            return {"retrievalResults": [_result(OPTIONAL_COVERAGES, 0.95)]}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "Triple Net Lease Property overview",
        RetrieverConfig(
            knowledge_base_ids=("kb-public",),
            reranking_enabled=False,
            current_query="I need it from the property and give me an overview of it",
        ),
    )

    assert any("Triple Net Lease" in query for query in fake_client.queries)
    assert "Heading: Triple Net Lease" in context
    assert "Attach form CP 12 19" in context
    assert sources[0]["heading"] == "Triple Net Lease"


def test_retrieval_uses_property_section_hint_for_yes_property_followup(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "SECTION: Inspections" in query_text:
                return {"retrievalResults": [_result(WHOLE_PROPERTY_MANUAL, 0.35)]}
            return {"retrievalResults": []}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "Property",
        RetrieverConfig(
            knowledge_base_ids=("kb-public",),
            reranking_enabled=False,
            current_query="Inspections Property",
        ),
    )

    assert any("SECTION: Inspections" in query for query in fake_client.queries)
    assert "Heading: Inspections" in context
    assert "Physical inspections are required" in context
    assert sources[0]["heading"] == "Inspections"


def test_retrieval_prefers_property_inspections_over_internal_match(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "SECTION: Inspections" in query_text:
                return {"retrievalResults": [_result(WHOLE_PROPERTY_MANUAL, 0.35)]}
            return {
                "retrievalResults": [
                    _s3_result(
                        INTERNAL_INSPECTIONS,
                        0.99,
                        "s3://vega-binding-authority/internal-docs/binding-authority.md",
                    )
                ]
            }

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "Inspections",
        RetrieverConfig(
            knowledge_base_ids=("kb-public", "kb-internal"),
            reranking_enabled=False,
            current_query="Inspections",
        ),
    )

    assert any("SECTION: Inspections" in query for query in fake_client.queries)
    assert "Manual: Property Manual" in context
    assert "Heading: Inspections" in context
    assert "Physical inspections are required" in context
    assert "Internal Guidelines" not in context
    assert sources[0]["manual_name"] == "Property Manual"


def test_retrieval_uses_property_section_hint_for_appetite(monkeypatch):
    class FakeBedrockClient:
        def __init__(self):
            self.queries = []

        def retrieve(self, **kwargs):
            query_text = kwargs["retrievalQuery"]["text"]
            self.queries.append(query_text)
            if "SECTION: Appetite" in query_text:
                return {"retrievalResults": [_result(APPETITE, 0.35)]}
            return {"retrievalResults": [_result(OPTIONAL_COVERAGES, 0.8)]}

    fake_client = FakeBedrockClient()
    monkeypatch.setattr(
        "agents.tools.retriever._get_bedrock_client",
        lambda _region: fake_client,
    )

    context, sources = _retrieve_manual_context(
        "Details about Appetite",
        RetrieverConfig(
            knowledge_base_ids=("kb-public",),
            reranking_enabled=False,
            current_query="Details about Appetite",
        ),
    )

    assert any("SECTION: Appetite" in query for query in fake_client.queries)
    assert "Heading: Appetite" in context
    assert "package and monoline property business" in context
    assert "Optional Coverages" not in context
    assert sources[0]["heading"] == "Appetite"
