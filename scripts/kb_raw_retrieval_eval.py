#!/usr/bin/env python3
"""Raw Bedrock KB retrieval evaluator.

This intentionally bypasses the app retriever, query expansion, local fallback,
reranking, clarification rules, and production prompt. It answers one question:
does this KB retrieve the expected pre-chunked documents by itself?
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()


MINIMAL_SYSTEM_PROMPT = """You answer only from the retrieved chunks.
If the answer is not present in the retrieved chunks, say it was not found.
Use citations only from the provided source IDs.
Do not use outside knowledge."""


@dataclass(frozen=True)
class EvalCase:
    query: str
    expected_any: tuple[tuple[str, ...], ...]


GOLDEN_CASES: tuple[EvalCase, ...] = (
    EvalCase("Solar Panels", (("Solar Panels", "building limit"),)),
    EvalCase("Inspections", (("Physical inspections", "$250,000"),)),
    EvalCase("Appetite", (("market for package", "monoline property"),)),
    EvalCase("Triple Net Lease", (("triple net lease", "CP 12 19"),)),
    EvalCase("Vacant building", (("vacant", "24"),)),
    EvalCase("Wildfire Guide", (("Risk meter", "High or Very High"),)),
    EvalCase("Property risks in Lava Zones 1 or 2", (("Lava Zones 1 or 2", "Prohibited"),)),
    EvalCase("Spoilage Coverage", (("Spoilage Coverage", "CP 04 40"),)),
    EvalCase(
        "minimum premium for Apartments without Mercantile Occupancies up to 10 units",
        (("0311", "$750"),),
    ),
    EvalCase("CG 2294", (("Damage to Work Performed By Subcontractors",),)),
    EvalCase("CG 22 94", (("Damage to Work Performed By Subcontractors",),)),
    EvalCase(
        "Apartments in NY or NJ",
        (("Apartments in NY or NJ", "Prohibited"), ("Apartment risks in NY or NJ", "Prohibited")),
    ),
    EvalCase("Body piercing jewelry manufacturing", (("Body piercing", "Prohibited"),)),
)


def _client(service_name: str, region: str):
    import boto3

    return boto3.client(service_name, region_name=region)


def _retrieve(
    *,
    kb_id: str,
    query: str,
    region: str,
    top_k: int,
    search_type: str,
) -> list[dict[str, Any]]:
    client = _client("bedrock-agent-runtime", region)
    response = client.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": top_k,
                "overrideSearchType": search_type,
            }
        },
    )
    return list(response.get("retrievalResults", []))


def _text(result: dict[str, Any]) -> str:
    content = result.get("content") or {}
    return str(content.get("text") or "")


def _location(result: dict[str, Any]) -> str:
    location = result.get("location") or {}
    for key in ("s3Location", "webLocation", "customDocumentLocation"):
        value = location.get(key)
        if isinstance(value, dict):
            return str(value.get("uri") or value.get("id") or "")
    return ""


def _parse_headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in text.splitlines()[:20]:
        match = re.match(r"^([A-Z][A-Z0-9_]*):\s*(.*)$", line.strip())
        if match:
            headers[match.group(1)] = match.group(2).strip()
    return headers


def _snippet(text: str, limit: int = 260) -> str:
    body = re.sub(r"\s+", " ", text).strip()
    if len(body) <= limit:
        return body
    return f"{body[: limit - 3].rstrip()}..."


def _matches(
    result: dict[str, Any],
    expected_any: tuple[tuple[str, ...], ...],
    required_uri_prefix: str = "",
) -> bool:
    if required_uri_prefix and not _location(result).startswith(required_uri_prefix):
        return False
    haystack = _text(result).lower()
    for required_terms in expected_any:
        if all(term.lower() in haystack for term in required_terms):
            return True
    return False


def _case_status(
    results: list[dict[str, Any]],
    case: EvalCase,
    required_uri_prefix: str = "",
) -> str:
    return (
        "PASS"
        if any(_matches(result, case.expected_any, required_uri_prefix) for result in results)
        else "FAIL"
    )


def _source_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, result in enumerate(results, 1):
        text = _text(result)
        headers = _parse_headers(text)
        rows.append(
            {
                "source_id": f"S{index}",
                "score": result.get("score"),
                "manual_type": headers.get("MANUAL_TYPE") or result.get("metadata", {}).get("MANUAL_TYPE"),
                "section": headers.get("SECTION") or result.get("metadata", {}).get("SECTION"),
                "class_code": headers.get("CLASS_CODE") or result.get("metadata", {}).get("CLASS_CODE"),
                "chunk_id": headers.get("CHUNK_ID") or result.get("metadata", {}).get("CHUNK_ID"),
                "uri": _location(result),
                "snippet": _snippet(text),
            }
        )
    return rows


def _print_case(case: EvalCase, results: list[dict[str, Any]], required_uri_prefix: str = "") -> None:
    status = _case_status(results, case, required_uri_prefix)
    print(f"\n[{status}] {case.query}")
    for row in _source_rows(results):
        score = row["score"]
        score_text = f"{score:.4f}" if isinstance(score, float) else str(score)
        title_bits = [
            bit
            for bit in (row["manual_type"], row["section"], row["class_code"], row["chunk_id"])
            if bit
        ]
        print(f"  {row['source_id']} score={score_text} | {' | '.join(title_bits)}")
        print(f"     {row['uri']}")
        print(f"     {row['snippet']}")


def _build_context(results: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row, result in zip(_source_rows(results), results, strict=False):
        title = " | ".join(
            bit
            for bit in (row["manual_type"], row["section"], row["class_code"], row["chunk_id"])
            if bit
        )
        parts.append(f"[{row['source_id']}] {title}\n{_text(result)}")
    return "\n\n".join(parts)


def _answer_with_openai(*, model_id: str, question: str, results: list[dict[str, Any]]) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Retrieved chunks:\n{_build_context(results)}\n\n"
        "Answer concisely. End with a <used_sources> JSON block containing only source IDs used."
    )
    response = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": MINIMAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0,
    )
    return response.choices[0].message.content or ""


def _answer_with_bedrock(*, model_id: str, region: str, question: str, results: list[dict[str, Any]]) -> str:
    client = _client("bedrock-runtime", region)
    user_prompt = (
        f"Question:\n{question}\n\n"
        f"Retrieved chunks:\n{_build_context(results)}\n\n"
        "Answer concisely. End with a <used_sources> JSON block containing only source IDs used."
    )
    response = client.converse(
        modelId=model_id,
        system=[{"text": MINIMAL_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={"temperature": 0, "maxTokens": 900},
    )
    output = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(block.get("text", "") for block in output if isinstance(block, dict)).strip()


def _answer(*, model_id: str, region: str, question: str, results: list[dict[str, Any]]) -> str:
    if model_id.startswith("gpt-"):
        return _answer_with_openai(model_id=model_id, question=question, results=results)
    return _answer_with_bedrock(model_id=model_id, region=region, question=question, results=results)


def _load_cases(path: Path | None, query: str | None) -> list[EvalCase]:
    if query:
        return [EvalCase(query, ((),))]
    if not path:
        return list(GOLDEN_CASES)

    data = json.loads(path.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for item in data:
        expected_any = tuple(tuple(group) for group in item.get("expected_any", []))
        cases.append(EvalCase(query=item["query"], expected_any=expected_any or ((),)))
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate raw Bedrock KB retrieval.")
    parser.add_argument("--kb-id", required=True, help="Bedrock Knowledge Base ID")
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--search-type",
        choices=["SEMANTIC", "HYBRID"],
        default="SEMANTIC",
        help="Bedrock retrieval overrideSearchType. No reranking is used.",
    )
    parser.add_argument("--query", default="", help="Run one ad hoc query instead of golden cases")
    parser.add_argument("--cases", type=Path, default=None, help="Optional JSON eval cases file")
    parser.add_argument("--json-output", type=Path, default=None, help="Optional report path")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero if any golden case fails")
    parser.add_argument(
        "--require-uri-prefix",
        default="",
        help="Only count matches whose retrieved URI starts with this prefix.",
    )
    parser.add_argument("--answer", action="store_true", help="Generate minimal RAG answers after retrieval")
    parser.add_argument(
        "--answer-model",
        default=os.getenv("BEDROCK_MODEL_ID") or "gpt-5.2",
        help="Model for optional minimal answer generation",
    )
    args = parser.parse_args()

    cases = _load_cases(args.cases, args.query or None)
    report: list[dict[str, Any]] = []

    print(
        f"Raw KB retrieval eval | kb={args.kb_id} | search={args.search_type} "
        f"| top_k={args.top_k} | cases={len(cases)}"
    )
    for case in cases:
        results = _retrieve(
            kb_id=args.kb_id,
            query=case.query,
            region=args.region,
            top_k=args.top_k,
            search_type=args.search_type,
        )
        status = _case_status(results, case, args.require_uri_prefix)
        _print_case(case, results, args.require_uri_prefix)

        answer = ""
        if args.answer:
            answer = _answer(
                model_id=args.answer_model,
                region=args.region,
                question=case.query,
                results=results,
            )
            print("\n  Minimal answer:")
            print("  " + answer.replace("\n", "\n  "))

        report.append(
            {
                "query": case.query,
                "status": status,
                "search_type": args.search_type,
                "top_k": args.top_k,
                "sources": _source_rows(results),
                "answer": answer,
            }
        )

    pass_count = sum(1 for item in report if item["status"] == "PASS")
    print(f"\nSummary: {pass_count}/{len(report)} passed")

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Report written: {args.json_output}")

    if args.strict and pass_count != len(report):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
