from agents.underwriting_agent import (
    UNDERWRITER_GUIDANCE,
    _normalize_underwriter_guidance,
    _retrieval_current_query,
    _source_backfill_answer,
    _requires_cross_manual_clarification,
    _retrieved_citation_fallback,
    _split_trailing_numbered_followups,
)


PROPERTY_SOURCE = {
    "source_id": "S1",
    "url": "https://bindingauthority.coactionspecialty.com/manuals/property.html",
    "heading": "Triple Net Lease",
    "manual_name": "Property Manual",
    "content_text": (
        "Buildings with a triple net lease should be referred to your Coaction underwriter. "
        "Attach form CP 12 19 Additional Insured - Building Owner."
    ),
}


GL_SOURCE = {
    "source_id": "S2",
    "url": "https://bindingauthority.coactionspecialty.com/manuals/61212.html",
    "heading": "Class Code 61212",
    "manual_name": "General Liability Manual",
    "content_text": (
        "This class applies when the insured is not directly responsible for maintenance "
        "and upkeep, for example a triple net lease arrangement."
    ),
}


GL_VACANT_SOURCE = {
    "source_id": "S3",
    "url": "https://bindingauthority.coactionspecialty.com/manuals/68604.html",
    "heading": "Class Code 68604",
    "manual_name": "General Liability Manual",
    "content_text": "68604 - Vacant Building - Factories",
}


def test_cross_manual_clarification_required_for_unspecified_exact_topic():
    assert _requires_cross_manual_clarification(
        "What is Triple Net Lease?",
        [PROPERTY_SOURCE, GL_SOURCE],
    )


def test_cross_manual_clarification_required_for_known_property_topic_even_if_gl_wins_retrieval():
    property_vacant_source = {
        "source_id": "S4",
        "url": "https://bindingauthority.coactionspecialty.com/manuals/property.html",
        "heading": "Vacant Buildings",
        "manual_name": "Property Manual",
        "content_text": "Buildings continuously vacant for more than 24-months require approval.",
    }

    assert _requires_cross_manual_clarification(
        "Vacant building",
        [GL_VACANT_SOURCE, property_vacant_source],
    )


def test_cross_manual_clarification_not_required_when_only_one_lob_retrieved():
    assert not _requires_cross_manual_clarification(
        "Vacant building",
        [GL_VACANT_SOURCE],
    )


def test_lob_selection_followup_carries_prior_topic_for_retrieval():
    history = [
        {"role": "user", "content": "Inspections"},
        {
            "role": "assistant",
            "content": "Which line are you asking about - Property or GL?",
        },
    ]

    assert _retrieval_current_query("Yes property", history) == "Inspections Property"


def test_lob_selection_without_prior_topic_stays_unchanged():
    assert _retrieval_current_query("Property", []) == "Property"


def test_cross_manual_clarification_not_required_when_property_is_specified():
    assert not _requires_cross_manual_clarification(
        "What is Triple Net Lease in Property?",
        [PROPERTY_SOURCE, GL_SOURCE],
    )


def test_cross_manual_clarification_not_required_for_solar_building_limit_question():
    assert not _requires_cross_manual_clarification(
        "Should the value of solar panels be included in the building limit?",
        [
            {
                "source_id": "S1",
                "url": "https://bindingauthority.coactionspecialty.com/manuals/property.html",
                "heading": "Solar Panels",
                "manual_name": "Property Manual",
                "content_text": (
                    "If Solar Panels are attached to a building, include the values in the "
                    "building limit."
                ),
            },
            {
                "source_id": "S2",
                "url": "https://bindingauthority.coactionspecialty.com/manuals/91583.html",
                "heading": "Class Code 91583",
                "manual_name": "General Liability Manual",
                "content_text": "Solar panel installation contractor.",
            },
        ],
    )


def test_retrieved_citation_fallback_selects_supporting_property_source():
    source_ids = _retrieved_citation_fallback(
        answer=(
            "Wildfire Guide: Risk meter reports are required on all West Coast property exposures."
        ),
        query="Wildfire Guide",
        source_id_to_meta={
            "S1": {
                "source_id": "S1",
                "url": "https://bindingauthority.coactionspecialty.com/manuals/property.html",
                "heading": "Wildfire Guide",
                "manual_name": "Property Manual",
                "content_text": (
                    "Risk meter reports are required on all West Coast property exposures."
                ),
            },
            "S2": GL_SOURCE,
        },
    )

    assert source_ids == ["S1"]


def test_split_trailing_numbered_followups_strips_questions_without_header():
    answer, followups = _split_trailing_numbered_followups(
        "Correct, those statements match the Property Manual.\n\n"
        "1. Are there state-specific restrictions for vacant buildings on Property?\n"
        "2. What inspection requirements apply to vacant buildings on Property?"
    )

    assert answer == "Correct, those statements match the Property Manual."
    assert followups == [
        "Are there state-specific restrictions for vacant buildings on Property?",
        "What inspection requirements apply to vacant buildings on Property?",
    ]


def test_source_backfill_replaces_hollow_property_section_answer():
    answer, cited_source_ids, used = _source_backfill_answer(
        answer="Vacant buildings (Property)\n** **",
        query="Property",
        cited_source_ids=[],
        source_id_to_meta={
            "S1": {
                "source_id": "S1",
                "url": "https://bindingauthority.coactionspecialty.com/manuals/property.html",
                "heading": "Vacant Buildings",
                "manual_name": "Property Manual",
                "content_text": (
                    "# _Vacant Buildings_\n"
                    "Buildings continuously vacant for more than 24-months require "
                    "Coaction underwriter approval.\n"
                    "Structural renovations require Coaction underwriter approval."
                ),
            }
        },
    )

    assert used
    assert cited_source_ids == ["S1"]
    assert answer.startswith("Vacant Buildings (Property)")
    assert "Buildings continuously vacant" in answer
    assert "Structural renovations" in answer


def test_source_backfill_preserves_sourced_negative_answer():
    answer, cited_source_ids, used = _source_backfill_answer(
        answer=(
            "Cyber liability coverage is not explicitly listed as a coverage option for "
            "class code 51970, so I cannot confirm its availability from the retrieved "
            "manual content."
        ),
        query="Is cyber liability coverage available for class code 51970?",
        cited_source_ids=["S1"],
        source_id_to_meta={
            "S1": {
                "source_id": "S1",
                "url": "https://bindingauthority.coactionspecialty.com/manuals/51970.html",
                "heading": "Class Code 51970",
                "manual_name": "General Liability Manual",
                "class_code": "51970",
                "content_text": (
                    "Class Code 51970 - Cosmetics Manufacturing\n"
                    "Coverage Options\n"
                    "Employee Benefits Liability\n"
                    "Product Withdrawal Expense\n"
                    "Worldwide Coverage\n"
                    "Hired & Non-Owned Auto"
                ),
            }
        },
    )

    assert not used
    assert cited_source_ids == ["S1"]
    assert answer.startswith("Cyber liability coverage is not explicitly listed")


def test_underwriter_guidance_normalization_preserves_approval_rules():
    answer = _normalize_underwriter_guidance(
        "For Property - Vacant Buildings:\n\n"
        "- Buildings continuously vacant for more than 24 months require "
        "Coaction underwriter approval.\n"
        "- Structural renovations require Coaction underwriter approval.\n\n"
        "Please contact your Coaction underwriter for details."
    )

    assert "continuously vacant for more than 24 months" in answer
    assert "Structural renovations require Coaction underwriter approval" in answer
    assert "Please contact your Coaction underwriter for details" not in answer
    assert answer.endswith(UNDERWRITER_GUIDANCE)
