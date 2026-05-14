# coaction_agent_platform/app/routers/kb_router.py
"""Knowledge Base management endpoints — create, sync, list, delete KBs from the API."""

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from domain.models import IdentityContext
from app.dependencies.identity import get_identity_context, require_role

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/knowledge-bases", tags=["Knowledge Bases"])

# Module-level references — set at app startup
_kb_manager = None
_dynamodb = None


def init_kb_router(kb_manager, dynamodb_adapter) -> None:
    """Initialize with Bedrock KB Manager and DynamoDB adapter."""
    global _kb_manager, _dynamodb
    _kb_manager = kb_manager
    _dynamodb = dynamodb_adapter


# ── Request Models ───────────────────────────────────────────────────────


class CreateKBRequest(BaseModel):
    name: str
    description: str = ""
    s3_bucket: str
    s3_prefix: str = ""


class SyncKBRequest(BaseModel):
    """Optional: specify data_source_id if KB has multiple sources."""

    data_source_id: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("")
async def create_knowledge_base(
    req: CreateKBRequest,
    identity: IdentityContext = Depends(require_role("underwriter")),
):
    """Create a new Bedrock Knowledge Base with an S3 data source.

    Only underwriters can create KBs.
    """
    if not _kb_manager or not _dynamodb:
        raise HTTPException(status_code=503, detail="KB service not initialized")

    try:
        # Step 1: Create the KB in Bedrock
        kb = _kb_manager.create_knowledge_base(
            name=req.name,
            description=req.description,
            rds_resource_arn=_kb_manager._rds_resource_arn,
            rds_credentials_secret_arn=_kb_manager._rds_credentials_secret_arn,
        )
        kb_id = kb["knowledgeBaseId"]

        # Step 2: Add S3 data source
        ds = _kb_manager.add_s3_data_source(
            kb_id=kb_id,
            s3_bucket=req.s3_bucket,
            s3_prefix=req.s3_prefix,
        )
        ds_id = ds["dataSourceId"]

        # Step 3: Trigger initial sync
        job = _kb_manager.sync_data_source(kb_id=kb_id, data_source_id=ds_id)

        # Step 4: Save metadata to DynamoDB
        _dynamodb.save_kb_metadata(
            kb_id=kb_id,
            name=req.name,
            description=req.description,
            s3_bucket=req.s3_bucket,
            s3_prefix=req.s3_prefix,
            created_by=identity.user_id,
            data_source_id=ds_id,
        )

        logger.info(
            "kb_creation_pipeline_complete",
            kb_id=kb_id,
            ds_id=ds_id,
            job_id=job["ingestionJobId"],
        )

        return {
            "kb_id": kb_id,
            "data_source_id": ds_id,
            "ingestion_job_id": job["ingestionJobId"],
            "status": "syncing",
            "message": f"Knowledge Base '{req.name}' created. Sync in progress.",
        }

    except Exception as e:
        logger.error("kb_creation_pipeline_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to create Knowledge Base: {str(e)}")


@router.get("")
async def list_knowledge_bases(
    identity: IdentityContext = Depends(get_identity_context),
):
    """List all Knowledge Bases with their current status."""
    if not _kb_manager or not _dynamodb:
        raise HTTPException(status_code=503, detail="KB service not initialized")

    # Get metadata from DynamoDB (our records)
    db_kbs = _dynamodb.list_knowledge_bases()

    # Enrich with live status from Bedrock
    result = []
    for kb_meta in db_kbs:
        kb_id = kb_meta["kb_id"]
        try:
            live_kb = _kb_manager.get_kb_status(kb_id)
            status = live_kb.get("status", "UNKNOWN")
        except Exception:
            status = "UNAVAILABLE"

        result.append(
            {
                "kb_id": kb_id,
                "name": kb_meta.get("name", ""),
                "description": kb_meta.get("description", ""),
                "s3_bucket": kb_meta.get("s3_bucket", ""),
                "s3_prefix": kb_meta.get("s3_prefix", ""),
                "status": status,
                "created_by": kb_meta.get("created_by", ""),
                "created_at": kb_meta.get("created_at", ""),
            }
        )

    return result


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: str,
    identity: IdentityContext = Depends(get_identity_context),
):
    """Get detailed status of a specific Knowledge Base."""
    if not _kb_manager:
        raise HTTPException(status_code=503, detail="KB service not initialized")

    try:
        live_kb = _kb_manager.get_kb_status(kb_id)
        db_meta = _dynamodb.get_kb_metadata(kb_id) if _dynamodb else {}

        return {
            "kb_id": kb_id,
            "name": live_kb.get("name", ""),
            "status": live_kb.get("status", "UNKNOWN"),
            "created_at": str(live_kb.get("createdAt", "")),
            "updated_at": str(live_kb.get("updatedAt", "")),
            "s3_bucket": (db_meta or {}).get("s3_bucket", ""),
            "s3_prefix": (db_meta or {}).get("s3_prefix", ""),
            "data_source_id": (db_meta or {}).get("data_source_id", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Knowledge Base not found: {str(e)}")


@router.post("/{kb_id}/sync")
async def sync_knowledge_base(
    kb_id: str,
    req: SyncKBRequest = SyncKBRequest(),
    identity: IdentityContext = Depends(require_role("underwriter")),
):
    """Trigger a re-sync (re-ingestion) of a Knowledge Base's data source."""
    if not _kb_manager or not _dynamodb:
        raise HTTPException(status_code=503, detail="KB service not initialized")

    # Get the data_source_id
    ds_id = req.data_source_id
    if not ds_id:
        db_meta = _dynamodb.get_kb_metadata(kb_id)
        if not db_meta or not db_meta.get("data_source_id"):
            raise HTTPException(status_code=400, detail="No data source found for this KB")
        ds_id = db_meta["data_source_id"]

    try:
        job = _kb_manager.sync_data_source(kb_id=kb_id, data_source_id=ds_id)
        _dynamodb.update_kb_status(kb_id, "syncing")

        return {
            "kb_id": kb_id,
            "ingestion_job_id": job["ingestionJobId"],
            "status": "syncing",
            "message": "Re-sync started.",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: str,
    identity: IdentityContext = Depends(require_role("underwriter")),
):
    """Delete a Knowledge Base from Bedrock and remove its metadata."""
    if not _kb_manager or not _dynamodb:
        raise HTTPException(status_code=503, detail="KB service not initialized")

    try:
        _kb_manager.delete_knowledge_base(kb_id)
        _dynamodb.delete_kb_metadata(kb_id)
        return {"message": f"Knowledge Base {kb_id} deleted."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete failed: {str(e)}")
