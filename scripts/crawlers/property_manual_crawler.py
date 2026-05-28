"""Crawl the Coaction Property manual page into Bedrock-ready markdown.

Usage:
    python -m scripts.crawlers.property_manual_crawler

The output file starts with metadata consumed by the retriever:
    SOURCE_URL: https://bindingauthority.coactionspecialty.com/manuals/property.html
    MANUAL_TYPE: Property
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup, Comment, NavigableString, Tag


PROPERTY_MANUAL_URL = "https://bindingauthority.coactionspecialty.com/manuals/property.html"
DEFAULT_OUTPUT_PATH = Path("data/bedrock_ingest/property/property.md")

JUNK_CONTAINER_TERMS = {
    "admonition",
    "anchor",
    "banner",
    "breadcrumb",
    "cookie",
    "feedback",
    "footer",
    "header",
    "menu",
    "nav",
    "navigation",
    "pagination",
    "search",
    "sidebar",
    "skip",
    "toc",
    "toolbar",
}

JUNK_LINE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"^ai-generated content may be incorrect\.?$",
        r"^back to top$",
        r"^copy link$",
        r"^edit this page$",
        r"^last updated\b",
        r"^next$",
        r"^on this page$",
        r"^previous$",
        r"^search$",
        r"^table of contents$",
        r"^was this helpful\??$",
    ]
]

BLOCK_TAGS = {
    "article",
    "blockquote",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "main",
    "ol",
    "p",
    "section",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}


def fetch_html(url: str, timeout: int) -> str:
    """Fetch manual HTML from the public Binding Authority site."""
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; ProjectVegaCrawler/1.0; "
                "+https://bindingauthority.coactionspecialty.com)"
            )
        },
    )
    response.raise_for_status()
    return response.text


def _normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _clean_markdown_cell(value: str) -> str:
    value = _normalize_space(value)
    return value.replace("|", r"\|")


def _class_id_tokens(tag: Tag) -> set[str]:
    raw_values = []
    raw_values.extend(tag.get("class", []))
    raw_values.append(str(tag.get("id", "")))
    raw_values.append(str(tag.get("role", "")))
    raw_values.append(str(tag.get("aria-label", "")))
    return {
        token
        for raw_value in raw_values
        for token in re.split(r"[^a-z0-9]+", raw_value.lower())
        if token
    }


def _looks_like_junk_container(tag: Tag) -> bool:
    tokens = _class_id_tokens(tag)
    return bool(tokens & JUNK_CONTAINER_TERMS)


def _remove_junk(soup: BeautifulSoup) -> None:
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    for tag in soup.find_all(
        [
            "button",
            "canvas",
            "footer",
            "form",
            "header",
            "iframe",
            "img",
            "input",
            "nav",
            "noscript",
            "picture",
            "script",
            "select",
            "style",
            "svg",
        ]
    ):
        tag.decompose()

    for tag in list(soup.find_all(True)):
        if _looks_like_junk_container(tag):
            tag.decompose()

    for anchor in soup.find_all("a"):
        anchor.unwrap()


def _select_content_root(soup: BeautifulSoup) -> Tag:
    for selector in (
        "main",
        "article",
        "[role='main']",
        ".markdown-body",
        ".docs-content",
        ".content",
    ):
        root = soup.select_one(selector)
        if root and _normalize_space(root.get_text(" ")):
            return root
    if soup.body:
        return soup.body
    return soup


def _has_block_child(tag: Tag) -> bool:
    return any(isinstance(child, Tag) and child.name in BLOCK_TAGS for child in tag.children)


def _inline_text(tag: Tag) -> str:
    return _normalize_space(tag.get_text(" "))


def _render_table(tag: Tag) -> list[str]:
    rows = []
    for tr in tag.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        rows.append([_clean_markdown_cell(cell.get_text(" ")) for cell in cells])

    if not rows:
        return []

    column_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (column_count - len(row)) for row in rows]
    header = normalized_rows[0]
    separator = ["---"] * column_count
    body = normalized_rows[1:]
    rendered = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    rendered.extend("| " + " | ".join(row) + " |" for row in body)
    return rendered


def _render_list(tag: Tag, ordered: bool) -> list[str]:
    lines = []
    for idx, item in enumerate(tag.find_all("li", recursive=False), start=1):
        prefix = f"{idx}." if ordered else "-"
        text = _inline_text(item)
        if text:
            lines.append(f"{prefix} {text}")
    return lines


def _render_tag(tag: Tag) -> list[str]:
    name = tag.name.lower()

    if name in {"h1", "h2", "h3", "h4", "h5", "h6"}:
        level = min(int(name[1]), 4)
        text = _inline_text(tag)
        return [f"{'#' * level} {text}"] if text else []

    if name == "table":
        return _render_table(tag)

    if name in {"ul", "ol"}:
        return _render_list(tag, ordered=name == "ol")

    if name in {"p", "blockquote"} and not _has_block_child(tag):
        text = _inline_text(tag)
        return [text] if text else []

    lines = []
    for child in tag.children:
        if isinstance(child, NavigableString):
            text = _normalize_space(str(child))
            if text and not _has_block_child(tag):
                lines.append(text)
        elif isinstance(child, Tag):
            child_lines = _render_tag(child)
            if child_lines:
                lines.extend(child_lines)
    return lines


def _drop_junk_lines(lines: Iterable[str]) -> list[str]:
    cleaned = []
    previous = None
    for line in lines:
        line = _normalize_space(line)
        if not line:
            continue
        if any(pattern.search(line) for pattern in JUNK_LINE_PATTERNS):
            continue
        line = re.sub(r"^o\s+", "- ", line)
        if line == previous:
            continue
        cleaned.append(line)
        previous = line
    return cleaned


def _drop_leading_manual_index(lines: list[str]) -> list[str]:
    """Remove a leading manual index while preserving real section content."""
    first_heading_index = next(
        (idx for idx, line in enumerate(lines) if line.startswith("# ")),
        None,
    )
    if first_heading_index is None or first_heading_index == 0:
        return lines

    leading_lines = lines[:first_heading_index]
    if leading_lines and all(line.startswith("- ") for line in leading_lines):
        return lines[first_heading_index:]
    return lines


def _is_table_line(line: str) -> bool:
    return line.startswith("| ") and line.endswith(" |")


def _is_list_line(line: str) -> bool:
    return line.startswith("- ") or bool(re.match(r"^\d+\. ", line))


def _join_markdown_lines(lines: list[str]) -> str:
    parts = []
    for line in lines:
        if not parts:
            parts.append(line)
            continue

        previous = parts[-1].strip()
        if (_is_table_line(previous) and _is_table_line(line)) or (
            _is_list_line(previous) and _is_list_line(line)
        ):
            parts.append("\n" + line)
        else:
            parts.append("\n\n" + line)
    return "".join(parts)


def html_to_manual_markdown(html: str, source_url: str = PROPERTY_MANUAL_URL) -> str:
    """Extract clean markdown from Property manual HTML and inject metadata."""
    soup = BeautifulSoup(html, "html.parser")
    _remove_junk(soup)
    root = _select_content_root(soup)

    lines = _drop_leading_manual_index(_drop_junk_lines(_render_tag(root)))
    content = _join_markdown_lines(lines).strip()
    if not content:
        raise ValueError("No Property manual content extracted from HTML.")

    return f"SOURCE_URL: {source_url}\nMANUAL_TYPE: Property\n---\n\n{content}\n"


def crawl_property_manual(
    *,
    url: str = PROPERTY_MANUAL_URL,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    timeout: int = 30,
) -> Path:
    """Fetch, clean, and save the Property manual markdown ingest file."""
    html = fetch_html(url, timeout=timeout)
    markdown = html_to_manual_markdown(html, source_url=url)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding="utf-8")
    return output_path


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Crawl Coaction property.html into Bedrock-ready markdown."
    )
    parser.add_argument("--url", default=PROPERTY_MANUAL_URL, help="Property manual URL to crawl.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output markdown path.",
    )
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    output_path = crawl_property_manual(
        url=args.url,
        output_path=args.output,
        timeout=args.timeout,
    )
    print(f"Property manual saved to {output_path}")


if __name__ == "__main__":
    main()
