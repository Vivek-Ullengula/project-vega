"""
Split a monolithic markdown manual into granular sections for Bedrock KB ingestion.
Usage: python -m scripts.split_manual
"""

import os
import re


def split_manual(input_file, output_dir, source_url, manual_type="GL"):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
    with open(input_file, "r", encoding="utf-8") as f:
        content = f.read()
    sections = re.split(r"\n(?=#+\s*|_)", content)
    print(f"Detected {len(sections)} potential sections in {input_file}.")
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        first_line = section.split("\n")[0]
        filename_base = re.sub(r"[^\w\s-]", "", first_line).strip().lower().replace(" ", "_")
        filename_base = filename_base[:50].strip("_")
        if not filename_base or len(filename_base) < 3:
            filename_base = f"section_{i + 1}"
        filename = f"{filename_base}.md"
        filepath = os.path.join(output_dir, filename)
        injected_content = [
            f"SOURCE_URL: {source_url}",
            f"MANUAL_TYPE: {manual_type}",
            f"SECTION: {first_line.strip('# _').strip()}",
            "---",
            section,
        ]
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(injected_content))
    print(f"Done! Split into {len(os.listdir(output_dir))} files in {output_dir}")


if __name__ == "__main__":
    split_manual(
        input_file="data/property.md",
        output_dir="data/bedrock_ingest/property_sections",
        source_url="https://bindingauthority.coactionspecialty.com/manuals/property.html",
        manual_type="Property",
    )
