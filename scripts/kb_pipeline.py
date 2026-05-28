#!/usr/bin/env python3
"""
KB Pipeline — Modular Knowledge Base management CLI.

Supports the full lifecycle: upload documents to S3, create Bedrock KBs,
add data sources, trigger ingestion, wire KBs to agent profiles, and
monitor sync status.

Usage:
  python scripts/kb_pipeline.py upload <file> --bucket <bucket> --prefix <prefix>
  python scripts/kb_pipeline.py create-kb --name <name> [--description <desc>]
  python scripts/kb_pipeline.py add-source --kb-id <id> --bucket <bucket> --prefix <prefix>
  python scripts/kb_pipeline.py sync --kb-id <id> --data-source-id <ds_id>
  python scripts/kb_pipeline.py wire --kb-id <id> --profile <agent_id>
  python scripts/kb_pipeline.py list
  python scripts/kb_pipeline.py status --kb-id <id>
  python scripts/kb_pipeline.py full-pipeline <file> --name <name> --bucket <bucket> --prefix <prefix> --profile <agent_id>

Requires: AWS credentials configured (via .env or AWS CLI).
Admin-only: This script is intended for platform administrators only.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import boto3  # noqa: E402
import structlog  # noqa: E402

from adapters.aws.bedrock_kb_manager import BedrockKBManager  # noqa: E402

logger = structlog.get_logger(__name__)


def get_kb_manager() -> BedrockKBManager:
    """Initialize a BedrockKBManager from environment variables."""
    region = os.getenv("AWS_REGION", "us-east-1")
    role_arn = os.getenv("BEDROCK_KB_ROLE_ARN", "")
    embedding_model_arn = os.getenv(
        "EMBEDDING_MODEL_ARN",
        f"arn:aws:bedrock:{region}::foundation-model/amazon.titan-embed-text-v2:0",
    )

    mgr = BedrockKBManager(
        region=region,
        role_arn=role_arn,
        embedding_model_arn=embedding_model_arn,
    )
    mgr._rds_resource_arn = os.getenv("RDS_RESOURCE_ARN", "")
    mgr._rds_credentials_secret_arn = os.getenv("RDS_CREDENTIALS_SECRET_ARN", "")
    mgr._rds_table_name = os.getenv(
        "BEDROCK_KB_RDS_TABLE_NAME",
        "bedrock_integration.bedrock_kb",
    )
    return mgr


# ── Commands ─────────────────────────────────────────────────────────────


def cmd_upload(args):
    """Upload a local file to S3."""
    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Error: File not found: {file_path}")
        sys.exit(1)

    region = os.getenv("AWS_REGION", "us-east-1")
    s3 = boto3.client("s3", region_name=region)
    prefix = args.prefix.strip("/")
    s3_key = f"{prefix}/{file_path.name}" if prefix else file_path.name

    if args.dry_run:
        print(f"[DRY RUN] Would upload: {file_path} → s3://{args.bucket}/{s3_key}")
        return s3_key

    print(f"Uploading: {file_path} → s3://{args.bucket}/{s3_key}")
    s3.upload_file(str(file_path), args.bucket, s3_key)
    print(f"Upload complete: s3://{args.bucket}/{s3_key}")
    return s3_key


def cmd_create_kb(args):
    """Create a new Bedrock Knowledge Base."""
    mgr = get_kb_manager()

    # Check if KB with same name already exists
    existing = mgr.get_kb_id_by_name(args.name)
    if existing:
        print(f"Knowledge Base '{args.name}' already exists: {existing}")
        return existing

    print(f"Creating Knowledge Base: {args.name}")
    kb = mgr.create_knowledge_base(
        name=args.name,
        description=args.description or f"Knowledge Base: {args.name}",
        rds_resource_arn=mgr._rds_resource_arn,
        rds_credentials_secret_arn=mgr._rds_credentials_secret_arn,
        rds_table_name=args.rds_table_name or mgr._rds_table_name,
    )
    kb_id = kb["knowledgeBaseId"]
    print(f"Knowledge Base created: {kb_id}")
    return kb_id


def cmd_add_source(args):
    """Add an S3 data source to an existing KB."""
    mgr = get_kb_manager()
    prefix = args.prefix.strip("/") if args.prefix else ""
    ds_name = args.source_name or f"{args.bucket}-{prefix}" if prefix else args.bucket

    # Check if data source already exists
    existing = mgr.get_data_source_id(args.kb_id, ds_name)
    if existing:
        print(f"Data source '{ds_name}' already exists on KB {args.kb_id}: {existing}")
        return existing

    chunking = getattr(args, "chunking", "SEMANTIC")
    parsing = getattr(args, "parsing", None)
    chunk_size = getattr(args, "chunk_size", 300)
    chunk_overlap = getattr(args, "chunk_overlap", 20)
    parsing_model = getattr(args, "parsing_model", None)

    print(f"Adding data source: s3://{args.bucket}/{prefix} -> KB {args.kb_id}")
    print(f"  Chunking:  {chunking}")
    print(f"  Parsing:   {parsing or 'STANDARD'}")
    ds = mgr.add_s3_data_source(
        kb_id=args.kb_id,
        s3_bucket=args.bucket,
        s3_prefix=f"{prefix}/" if prefix else "",
        data_source_name=ds_name,
        chunking_strategy=chunking,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        parsing_strategy=parsing,
        parsing_model_arn=parsing_model,
    )
    ds_id = ds["dataSourceId"]
    print(f"Data source added: {ds_id}")
    return ds_id


def cmd_sync(args):
    """Trigger ingestion/sync for a KB data source."""
    mgr = get_kb_manager()

    # If no data source ID, try to find the first one
    ds_id = args.data_source_id
    if not ds_id:
        sources = mgr.list_data_sources(args.kb_id)
        if not sources:
            print(f"Error: No data sources found for KB {args.kb_id}")
            sys.exit(1)
        ds_id = sources[0]["dataSourceId"]
        print(f"Using data source: {ds_id}")

    print(f"Starting ingestion: KB={args.kb_id}, DS={ds_id}")
    job = mgr.sync_data_source(kb_id=args.kb_id, data_source_id=ds_id)
    job_id = job["ingestionJobId"]
    print(f"Ingestion job started: {job_id}")
    print(f"Check status: python scripts/kb_pipeline.py status --kb-id {args.kb_id}")
    return job_id


def cmd_wire(args):
    """Wire a KB ID to an agent's execution profile."""
    profile_path = Path("profiles") / f"{args.profile}.json"
    if not profile_path.exists():
        print(f"Error: Profile not found: {profile_path}")
        sys.exit(1)

    with open(profile_path) as f:
        profile = json.load(f)

    kb_ids = profile.get("retrieval_profile", {}).get("knowledge_base_ids", [])

    if args.kb_id in kb_ids:
        print(f"KB {args.kb_id} is already wired to profile '{args.profile}'")
        return

    if args.dry_run:
        print(f"[DRY RUN] Would add KB {args.kb_id} to profile '{args.profile}'")
        print(f"  Current KB IDs: {kb_ids}")
        print(f"  New KB IDs:     {kb_ids + [args.kb_id]}")
        return

    kb_ids.append(args.kb_id)
    profile.setdefault("retrieval_profile", {})["knowledge_base_ids"] = kb_ids

    with open(profile_path, "w") as f:
        json.dump(profile, f, indent=2)

    print(f"KB {args.kb_id} wired to profile '{args.profile}'")
    print(f"  Updated KB IDs: {kb_ids}")
    print(f"  Profile saved: {profile_path}")


def cmd_list(args):
    """List all Knowledge Bases."""
    mgr = get_kb_manager()
    kbs = mgr.list_knowledge_bases()

    if not kbs:
        print("No Knowledge Bases found.")
        return

    print(f"\n{'ID':<15} {'Name':<35} {'Status':<12} {'Updated'}")
    print("-" * 80)
    for kb in kbs:
        print(
            f"{kb.get('knowledgeBaseId', ''):<15} "
            f"{kb.get('name', ''):<35} "
            f"{kb.get('status', ''):<12} "
            f"{str(kb.get('updatedAt', ''))[:19]}"
        )
    print(f"\nTotal: {len(kbs)} Knowledge Base(s)")


def cmd_status(args):
    """Get detailed status of a KB and its data sources."""
    mgr = get_kb_manager()

    # KB status
    kb = mgr.get_kb_status(args.kb_id)
    print(f"\nKnowledge Base: {args.kb_id}")
    print(f"  Name:    {kb.get('name', '')}")
    print(f"  Status:  {kb.get('status', '')}")
    print(f"  Created: {kb.get('createdAt', '')}")
    print(f"  Updated: {kb.get('updatedAt', '')}")

    # Data sources
    sources = mgr.list_data_sources(args.kb_id)
    if sources:
        print(f"\n  Data Sources ({len(sources)}):")
        for ds in sources:
            print(
                f"    - {ds.get('dataSourceId', '')} | {ds.get('name', '')} | {ds.get('status', '')}"
            )
    else:
        print("\n  No data sources.")


def cmd_full_pipeline(args):
    """Run the complete pipeline: upload → create KB → add source → sync → wire."""
    print("=" * 60)
    print("  KB Full Pipeline")
    print("=" * 60)

    # Step 1: Upload
    print("\n[1/5] Uploading document to S3...")
    s3_key = cmd_upload(args)

    # Step 2: Create KB
    print("\n[2/5] Creating Knowledge Base...")
    kb_id = cmd_create_kb(args)

    # Step 3: Add data source
    print("\n[3/5] Adding S3 data source...")
    args.kb_id = kb_id
    ds_id = cmd_add_source(args)

    # Step 4: Sync
    print("\n[4/5] Starting ingestion...")
    args.data_source_id = ds_id
    job_id = cmd_sync(args)

    # Step 5: Wire to profile
    if args.profile:
        print("\n[5/5] Wiring KB to agent profile...")
        cmd_wire(args)
    else:
        print("\n[5/5] Skipped (no --profile specified)")

    print(f"\n{'=' * 60}")
    print("  Pipeline Complete!")
    print(f"{'=' * 60}")
    print(f"  KB ID:          {kb_id}")
    print(f"  Data Source ID:  {ds_id}")
    print(f"  Ingestion Job:   {job_id}")
    print(f"  S3 Key:          s3://{args.bucket}/{s3_key}")
    if args.profile:
        print(f"  Profile:         profiles/{args.profile}.json")
    print(f"{'=' * 60}\n")


# ── CLI Setup ────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="KB Pipeline — Modular Knowledge Base management CLI (Admin only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # upload
    p_upload = sub.add_parser("upload", help="Upload a file to S3")
    p_upload.add_argument("file", help="Local file path to upload")
    p_upload.add_argument("--bucket", required=True, help="S3 bucket name")
    p_upload.add_argument("--prefix", default="", help="S3 key prefix (folder)")
    p_upload.add_argument("--dry-run", action="store_true", help="Show what would happen")

    # create-kb
    p_create = sub.add_parser("create-kb", help="Create a new Bedrock Knowledge Base")
    p_create.add_argument("--name", required=True, help="KB name")
    p_create.add_argument("--description", default="", help="KB description")
    p_create.add_argument(
        "--rds-table-name",
        default="",
        help=(
            "RDS table for vector storage. Use a unique table for isolated test KBs "
            "(default: BEDROCK_KB_RDS_TABLE_NAME or bedrock_integration.bedrock_kb)."
        ),
    )

    # add-source
    p_source = sub.add_parser("add-source", help="Add S3 data source to a KB")
    p_source.add_argument("--kb-id", required=True, help="Knowledge Base ID")
    p_source.add_argument("--bucket", required=True, help="S3 bucket name")
    p_source.add_argument("--prefix", default="", help="S3 key prefix")
    p_source.add_argument("--source-name", default="", help="Custom data source name")
    p_source.add_argument(
        "--chunking",
        default="SEMANTIC",
        choices=["NONE", "FIXED_SIZE", "SEMANTIC", "HIERARCHICAL"],
        help="Chunking strategy (default: SEMANTIC)",
    )
    p_source.add_argument(
        "--chunk-size", type=int, default=300, help="Max tokens per chunk (default: 300)"
    )
    p_source.add_argument(
        "--chunk-overlap", type=int, default=20, help="Overlap (percentage or tokens, default: 20)"
    )
    p_source.add_argument(
        "--parsing",
        default=None,
        choices=[None, "BEDROCK_FOUNDATION_MODEL"],
        help="Parsing strategy. Use BEDROCK_FOUNDATION_MODEL for complex Word/PDF layouts.",
    )
    p_source.add_argument(
        "--parsing-model", default=None, help="FM ARN for parsing (default: Amazon Nova Pro)"
    )

    # sync
    p_sync = sub.add_parser("sync", help="Trigger ingestion sync")
    p_sync.add_argument("--kb-id", required=True, help="Knowledge Base ID")
    p_sync.add_argument(
        "--data-source-id", default="", help="Data source ID (auto-detects if omitted)"
    )

    # wire
    p_wire = sub.add_parser("wire", help="Wire a KB to an agent profile")
    p_wire.add_argument("--kb-id", required=True, help="Knowledge Base ID to add")
    p_wire.add_argument(
        "--profile", required=True, help="Agent profile name (filename without .json)"
    )
    p_wire.add_argument("--dry-run", action="store_true", help="Show what would happen")

    # list
    sub.add_parser("list", help="List all Knowledge Bases")

    # status
    p_status = sub.add_parser("status", help="Get KB status and data sources")
    p_status.add_argument("--kb-id", required=True, help="Knowledge Base ID")

    # full-pipeline
    p_full = sub.add_parser(
        "full-pipeline", help="Full pipeline: upload -> create KB -> add source -> sync -> wire"
    )
    p_full.add_argument("file", help="Local file path to upload")
    p_full.add_argument("--name", required=True, help="KB name")
    p_full.add_argument("--description", default="", help="KB description")
    p_full.add_argument("--bucket", required=True, help="S3 bucket name")
    p_full.add_argument("--prefix", default="", help="S3 key prefix")
    p_full.add_argument("--source-name", default="", help="Custom data source name")
    p_full.add_argument("--profile", default="", help="Agent profile to wire KB to")
    p_full.add_argument("--dry-run", action="store_true", help="Show what would happen")
    p_full.add_argument(
        "--rds-table-name",
        default="",
        help=(
            "RDS table for vector storage. Use a unique table for isolated test KBs "
            "(default: BEDROCK_KB_RDS_TABLE_NAME or bedrock_integration.bedrock_kb)."
        ),
    )
    p_full.add_argument(
        "--chunking",
        default="SEMANTIC",
        choices=["NONE", "FIXED_SIZE", "SEMANTIC", "HIERARCHICAL"],
        help="Chunking strategy (default: SEMANTIC)",
    )
    p_full.add_argument(
        "--chunk-size", type=int, default=300, help="Max tokens per chunk (default: 300)"
    )
    p_full.add_argument("--chunk-overlap", type=int, default=20, help="Overlap (default: 20)")
    p_full.add_argument(
        "--parsing",
        default=None,
        choices=[None, "BEDROCK_FOUNDATION_MODEL"],
        help="Parsing strategy for complex Word/PDF docs.",
    )
    p_full.add_argument(
        "--parsing-model", default=None, help="FM ARN for parsing (default: Amazon Nova Pro)"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "upload": cmd_upload,
        "create-kb": cmd_create_kb,
        "add-source": cmd_add_source,
        "sync": cmd_sync,
        "wire": cmd_wire,
        "list": cmd_list,
        "status": cmd_status,
        "full-pipeline": cmd_full_pipeline,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
