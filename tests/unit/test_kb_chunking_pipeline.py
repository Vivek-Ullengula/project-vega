from pathlib import Path

from scripts.kb_chunking_pipeline import (
    build_chunks_for_source,
    detect_document_kind,
    load_source_document,
    run_pipeline,
)


PROPERTY_URL = "https://bindingauthority.coactionspecialty.com/manuals/property.html"
GUIDE_URL = "https://bindingauthority.coactionspecialty.com/manuals/guide.html"


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_property_manual_chunks_have_stable_headers_and_table_row_context(tmp_path):
    source = _write(
        tmp_path / "property.md",
        f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
---

# Appetite
Coaction Specialty is currently a market for package and monoline property business.

# Coastline Map

# Optional Coverages
| Coverage Option | Form Number(s) | Scheduled Limit | Pricing Rules |
| --- | --- | --- | --- |
| Spoilage Coverage | CP 04 40 12 20 | $10,000; $20,000 | $100; $200 |

# Solar Panels
If Solar Panels are attached to a building, include the values in the building limit.
""",
    )

    chunks = build_chunks_for_source(source, max_chars=800)

    assert {chunk.section for chunk in chunks} >= {
        "Appetite",
        "Coastline Map",
        "Optional Coverages",
        "Solar Panels",
    }
    solar = next(chunk for chunk in chunks if chunk.section == "Solar Panels")
    assert solar.document_text().startswith(f"SOURCE_URL: {PROPERTY_URL}\n")
    assert "MANUAL_TYPE: Property" in solar.document_text()
    assert "SECTION: Solar Panels" in solar.document_text()
    assert "CHUNK_ID:" in solar.document_text()
    assert "ALIASES:" in solar.document_text()
    assert "SOURCE_HASH:" in solar.document_text()
    assert "photovoltaic panels" in solar.document_text()

    coastline = next(chunk for chunk in chunks if chunk.section == "Coastline Map")
    assert "Coastline Map image" in coastline.content
    assert "gulf coast" in coastline.document_text().lower()

    spoilage = next(
        chunk
        for chunk in chunks
        if chunk.section == "Optional Coverages" and "Spoilage Coverage" in chunk.content
    )
    assert "| Coverage Option | Form Number(s) |" in spoilage.content
    assert "CP 04 40 12 20" in spoilage.content
    assert "CP04401220" in spoilage.document_text()


def test_gl_guide_form_row_gets_exact_form_aliases(tmp_path):
    source = _write(
        tmp_path / "guide_additional_insured_and_coverage_options.md",
        f"""SOURCE_URL: {GUIDE_URL}
MANUAL_TYPE: General Liability Guide
SECTION: Additional Insured and Coverage Options
---
# Additional Insured and Coverage Options
| Coverage Options | Pricing Rules |
| --- | --- |
| CG 2294 - Exclusion - Damage to Work Performed By Subcontractors On Your Behalf | 10% credit added (91580)
5% credit added (All other contractors) |
""",
    )

    chunks = build_chunks_for_source(source, max_chars=600)
    cg_chunks = [chunk for chunk in chunks if "CG 2294" in chunk.content]

    assert cg_chunks
    assert any("CG2294" in chunk.document_text() for chunk in cg_chunks)
    assert any("CG 22 94" in chunk.document_text() for chunk in cg_chunks)
    assert all(chunk.section == "Additional Insured and Coverage Options" for chunk in cg_chunks)


def test_document_kind_detection_supports_class_codes_and_internal_guidelines(tmp_path):
    class_source = _write(
        tmp_path / "60010.md",
        """SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/60010.html
CLASS_CODE: 60010
---
# Submit
- More than 300 units
""",
    )
    internal_source = _write(
        tmp_path / "internal.md",
        """MANUAL_TYPE: Internal Guidelines
---
# Inspections
Internal inspection rules.
""",
    )

    class_doc = load_source_document(class_source)
    internal_doc = load_source_document(internal_source)

    assert detect_document_kind(class_source, class_doc.metadata, class_doc.body) == (
        "gl_class_code",
        "General Liability",
    )
    assert detect_document_kind(internal_source, internal_doc.metadata, internal_doc.body) == (
        "internal_guidelines",
        "Internal Guidelines",
    )


def test_run_pipeline_writes_manifest_and_dedupes_duplicate_content(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    output_dir = tmp_path / "out"
    content = f"""SOURCE_URL: {PROPERTY_URL}
MANUAL_TYPE: Property
---
# Solar Panels
If Solar Panels are attached to a building, include the values in the building limit.
"""
    _write(source_dir / "property_a.md", content)
    _write(source_dir / "property_b.md", content)

    manifest = run_pipeline([source_dir], output_dir, clean=True)

    assert manifest["chunk_count"] == 2
    assert manifest["written_chunk_count"] == 1
    assert manifest["duplicate_content_hashes"]
    assert (output_dir / "manifest.json").exists()
    assert list((output_dir / "property").glob("*.md"))


def test_csv_chunks_keep_sheet_and_row_locator_metadata(tmp_path):
    source = _write(
        tmp_path / "rates.csv",
        "Coverage,Limit,Premium\nSpoilage Coverage,$10000,$100\nOutdoor Signs,$5000,$250\n",
    )

    chunks = build_chunks_for_source(source, max_chars=800)

    assert chunks
    first = chunks[0].document_text()
    assert "SOURCE_TYPE: spreadsheet" in first
    assert "SHEET: rates" in first
    assert "ROW_RANGE: 2-3" in first
    assert "LOCATOR: block=1; piece=1; parser=deterministic; sheet=rates; rows=2-3" in first
    assert "| Coverage | Limit | Premium |" in first
