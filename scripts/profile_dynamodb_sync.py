#!/usr/bin/env python3
"""Backup, push, and restore agent execution profiles in DynamoDB.

This is intentionally small and admin-oriented. It lets us switch a profile to a
new KB, reload the app, and restore the previous profile from a timestamped JSON
backup if the test does not behave well.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from adapters.aws.dynamodb import DynamoDBAdapter  # noqa: E402
from domain.models import ExecutionProfile  # noqa: E402


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _adapter(args: argparse.Namespace) -> DynamoDBAdapter:
    return DynamoDBAdapter(table_name=args.table_name, region=args.region)


def _load_profile(path: Path) -> ExecutionProfile:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ExecutionProfile.model_validate(data)


def cmd_backup(args: argparse.Namespace) -> None:
    adapter = _adapter(args)
    stored = adapter.get_execution_profile(args.agent_id, args.version)
    if not stored or not stored.get("profile"):
        raise SystemExit(f"No DynamoDB profile found for {args.agent_id}:{args.version}")

    profile = ExecutionProfile.model_validate(_json_safe(stored["profile"]))
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.agent_id}-{args.version}-backup-{timestamp}.json"
    backup = {
        "agent_id": args.agent_id,
        "version": args.version,
        "table_name": args.table_name,
        "region": args.region,
        "backed_up_at": datetime.now(timezone.utc).isoformat(),
        "profile": profile.model_dump(mode="json"),
        "raw_item": _json_safe(stored),
    }
    out_path.write_text(json.dumps(backup, indent=2), encoding="utf-8")
    print(f"Backed up {args.agent_id}:{args.version} to {out_path}")
    print("KB IDs:", ", ".join(profile.retrieval_profile.knowledge_base_ids))


def cmd_push(args: argparse.Namespace) -> None:
    profile = _load_profile(Path(args.profile))
    adapter = _adapter(args)
    adapter.save_execution_profile(
        agent_id=profile.agent_id,
        version=args.version,
        profile_data=profile.model_dump(mode="json"),
    )
    print(f"Pushed {profile.agent_id}:{args.version} to DynamoDB table {args.table_name}")
    print("KB IDs:", ", ".join(profile.retrieval_profile.knowledge_base_ids))


def cmd_restore(args: argparse.Namespace) -> None:
    backup = json.loads(Path(args.backup).read_text(encoding="utf-8"))
    profile_data = backup.get("profile")
    if not profile_data:
        raise SystemExit(f"Backup does not contain a profile: {args.backup}")

    profile = ExecutionProfile.model_validate(profile_data)
    version = args.version or backup.get("version") or "latest"
    adapter = _adapter(args)
    adapter.save_execution_profile(
        agent_id=profile.agent_id,
        version=version,
        profile_data=profile.model_dump(mode="json"),
    )
    print(f"Restored {profile.agent_id}:{version} from {args.backup}")
    print("KB IDs:", ", ".join(profile.retrieval_profile.knowledge_base_ids))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--table-name", default=os.getenv("DYNAMODB_TABLE_NAME", "CoactionPlatform"))

    subparsers = parser.add_subparsers(dest="command", required=True)

    backup = subparsers.add_parser("backup", help="Save the current DynamoDB profile to JSON")
    backup.add_argument("--agent-id", default="coaction-underwriting")
    backup.add_argument("--version", default="latest")
    backup.add_argument("--output-dir", default="scratch/profile-backups")
    backup.set_defaults(func=cmd_backup)

    push = subparsers.add_parser("push", help="Push a local profile JSON to DynamoDB")
    push.add_argument("--profile", default="profiles/coaction-underwriting.json")
    push.add_argument("--version", default="latest")
    push.set_defaults(func=cmd_push)

    restore = subparsers.add_parser("restore", help="Restore a DynamoDB profile from backup JSON")
    restore.add_argument("--backup", required=True)
    restore.add_argument("--version", default=None)
    restore.set_defaults(func=cmd_restore)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
