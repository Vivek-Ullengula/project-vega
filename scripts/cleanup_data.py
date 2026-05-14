"""
Data Cleanup Script for Coaction Bedrock Knowledge Base
========================================================
Run this script BEFORE re-ingesting data into Bedrock.

What it does:
  1. Deletes duplicate _underscore_wrapped_ files in property_sections/
  2. Deletes junk table-of-contents file that pollutes retrieval
  3. Fixes bullet formatting: 'oSomething' → '- Something'
  4. Strips the MANUAL_TYPE metadata line from clean_content headers

Usage:
  python scripts/cleanup_data.py            # Dry run (shows what would change)
  python scripts/cleanup_data.py --apply    # Actually apply changes
"""

import os
import re
import sys

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "bedrock_ingest")

# ── Files to delete ──
JUNK_FILES = [
    os.path.join(
        DATA_DIR, "property_sections", "source_url_httpsbindingauthoritycoactionspecialtyc.md"
    ),
    os.path.join(
        DATA_DIR,
        "property_sections",
        "source_url_httpsbindingauthoritycoactionspecialtycommanualspropertyhtml.md",
    ),
]


def find_underscore_duplicates(folder: str) -> list[str]:
    """Find files like _appetite_.md that have a non-underscore twin appetite.md."""
    duplicates = []
    files = os.listdir(folder)
    for f in files:
        if f.startswith("_") and f.endswith("_.md"):
            # _appetite_.md -> appetite.md
            clean_name = f[1:-4] + ".md"  # strip leading _ and trailing _.md, re-add .md
            if clean_name in files:
                duplicates.append(os.path.join(folder, f))
    return duplicates


def fix_bullet_formatting(filepath: str) -> tuple[str, bool]:
    """Convert 'oSomething' at start of line to '- Something'."""
    with open(filepath, "r", encoding="utf-8") as fh:
        content = fh.read()

    # Match lines starting with 'o' followed by an uppercase letter (bullet items)
    fixed = re.sub(r"^o([A-Z])", r"- \1", content, flags=re.MULTILINE)
    changed = fixed != content
    return fixed, changed


def main():
    dry_run = "--apply" not in sys.argv
    mode = "DRY RUN" if dry_run else "APPLYING"
    print(f"\n{'=' * 60}")
    print(f"  Coaction Data Cleanup — {mode}")
    print(f"{'=' * 60}\n")

    # ── Step 1: Delete underscore duplicates ──
    prop_dir = os.path.join(DATA_DIR, "property_sections")
    duplicates = find_underscore_duplicates(prop_dir)
    print(f"[1] Found {len(duplicates)} underscore duplicate(s) to delete:")
    for d in duplicates:
        print(f"    [x] {os.path.basename(d)}")
        if not dry_run:
            os.remove(d)

    # ── Step 2: Delete junk files ──
    print("\n[2] Junk table-of-contents files to delete:")
    for jf in JUNK_FILES:
        if os.path.exists(jf):
            print(f"    [x] {os.path.basename(jf)}")
            if not dry_run:
                os.remove(jf)
        else:
            print(f"    (already gone) {os.path.basename(jf)}")

    # ── Step 3: Fix bullet formatting ──
    print("\n[3] Fixing bullet formatting (oXxx -> - Xxx):")
    fixed_count = 0
    for folder in ["full_manuals", "guide_sections", "property_sections"]:
        folder_path = os.path.join(DATA_DIR, folder)
        if not os.path.isdir(folder_path):
            continue
        for filename in sorted(os.listdir(folder_path)):
            if not filename.endswith(".md"):
                continue
            filepath = os.path.join(folder_path, filename)
            fixed_content, changed = fix_bullet_formatting(filepath)
            if changed:
                fixed_count += 1
                print(f"    [ok] {folder}/{filename}")
                if not dry_run:
                    with open(filepath, "w", encoding="utf-8") as fh:
                        fh.write(fixed_content)

    print(f"    -> {fixed_count} file(s) with bullet fixes")

    # ── Summary ──
    print(f"\n{'=' * 60}")
    if dry_run:
        print("  DRY RUN complete. Run with --apply to make changes.")
        print(f"  python {sys.argv[0]} --apply")
    else:
        total = len(duplicates) + len([j for j in JUNK_FILES if os.path.exists(j)]) + fixed_count
        print(f"  DONE. {total} changes applied.")
        print("  Next steps:")
        print("    1. Sync data/ to your S3 bucket")
        print("    2. Re-ingest the data source in Bedrock console")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
