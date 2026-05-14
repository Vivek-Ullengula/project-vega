#!/usr/bin/env python3
"""CLI query tool for the Coaction Binding Authority Bot.

Usage:
  python scripts/query.py "your question here"
  python scripts/query.py "follow-up question" --session-id <sid>
  python scripts/query.py "question" --role agent
  python scripts/query.py --interactive

This runs the agent directly (no FastAPI server needed).
Uses the new UnderwritingAgent from the reference architecture.
"""

import asyncio
import os
import sys
import uuid
import argparse

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()


async def invoke_agent(query: str, session_id: str, role: str) -> dict:
    """Invoke the underwriting agent directly."""
    from domain.models import ExecutionProfile, ModelProfile, RetrievalProfile
    from agents.underwriting_agent import UnderwritingAgent

    # Build profile from environment
    kb_id = os.environ.get("BEDROCK_KB_ID", "")
    model_id = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
    region = os.environ.get("AWS_REGION", "us-east-1")

    profile = ExecutionProfile(
        agent_id="coaction-underwriting",
        version="1.0",
        prompt_template_id="underwriting_system_v1",
        model_profile=ModelProfile(model_id=model_id, temperature=0.0, max_tokens=4096),
        retrieval_profile=RetrievalProfile(
            knowledge_base_ids=[kb_id] if kb_id else [],
        ),
    )

    agent = UnderwritingAgent(profile=profile, region=region)
    result = await agent.invoke(query=query, role=role)

    return {
        "answer": result.get("answer", ""),
        "sources": result.get("sources", []),
        "citations": result.get("citations", []),
        "follow_up_questions": result.get("follow_up_questions", []),
        "model_id": model_id,
        "session_id": session_id,
    }


async def main():
    parser = argparse.ArgumentParser(description="Coaction Binding Authority Bot - CLI")
    parser.add_argument("query", nargs="*", help="The question to ask")
    parser.add_argument(
        "--session-id", "-s", default=None, help="Session ID for multi-turn (reuse for follow-ups)"
    )
    parser.add_argument(
        "--role",
        "-r",
        default="underwriter",
        choices=["underwriter", "agent", "external"],
        help="User role (default: underwriter)",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true", help="Interactive multi-turn mode"
    )
    args = parser.parse_args()

    session_id = args.session_id or str(uuid.uuid4())
    model_id = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
    kb_id = os.environ.get("BEDROCK_KB_ID", "")

    print(f"\n{'-' * 60}")
    print("  Coaction Binding Authority Bot - CLI")
    print(f"{'-' * 60}")
    print(f"  Model:      bedrock / {model_id}")
    print(f"  KB ID:      {kb_id}")
    print(f"  Role:       {args.role}")
    print(f"  Session:    {session_id}")
    print(f"{'-' * 60}\n")

    if args.interactive:
        print("  Type 'quit' or 'exit' to end.\n")
        turn = 0
        while True:
            try:
                query = input(f"You [{turn + 1}]: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break

            if not query or query.lower() in ("quit", "exit", "q"):
                print(f"\nSession ID for resume: {session_id}")
                break

            turn += 1
            result = await invoke_agent(query, session_id, args.role)

            print(f"\n{'=' * 60}")
            print(result["answer"])
            print(f"{'=' * 60}")

            if result["sources"]:
                print("\nSources:")
                for i, url in enumerate(result["sources"][:3], 1):
                    print(f"   {i}. {url}")

            if result["follow_up_questions"]:
                print("\nYou might also want to ask:")
                for i, q in enumerate(result["follow_up_questions"], 1):
                    print(f"   {i}. {q}")
            print()

    else:
        if not args.query:
            parser.print_help()
            sys.exit(1)

        query = " ".join(args.query)
        print(f"Query: {query}\n")

        result = await invoke_agent(query, session_id, args.role)

        print("=" * 60)
        print("ANSWER")
        print("=" * 60)
        print(result["answer"])
        print()

        if result["sources"]:
            print("=" * 60)
            print("SOURCES")
            print("=" * 60)
            for i, url in enumerate(result["sources"], 1):
                print(f"{i}. {url}")
            print()

        print(f"Session ID (for follow-ups): {session_id}")
        print(f'  > python scripts/query.py "follow-up question" --session-id {session_id}')
        print()


if __name__ == "__main__":
    asyncio.run(main())
