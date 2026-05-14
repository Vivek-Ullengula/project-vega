# adapters/aws/dynamodb.py
"""DynamoDB single-table adapter for user profiles, sessions, KB metadata, and execution profiles."""

import time
from datetime import datetime, timezone
from typing import Any

import boto3
import structlog
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError  # noqa: F401

logger = structlog.get_logger(__name__)


class DynamoDBAdapter:
    """Single-table DynamoDB adapter using composite PK/SK keys."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        dynamodb = boto3.resource("dynamodb", region_name=region)
        self.table = dynamodb.Table(table_name)
        self.table_name = table_name
        logger.info("dynamodb_adapter_initialized", table=table_name, region=region)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ──────────────────────────────────────────────────────────────────────
    # User Profiles
    # ──────────────────────────────────────────────────────────────────────

    def save_user_profile(self, user_id: str, email: str, name: str, role: str) -> dict:
        """Create or update a user profile."""
        item = {
            "PK": f"USER#{user_id}",
            "SK": "PROFILE",
            "EntityType": "User",
            "user_id": user_id,
            "email": email,
            "name": name,
            "role": role,
            "created_at": self._now_iso(),
            "updated_at": self._now_iso(),
        }
        self.table.put_item(Item=item)
        logger.info("user_profile_saved", user_id=user_id, email=email)
        return item

    def get_user_profile(self, user_id: str) -> dict | None:
        """Retrieve a user profile by user_id (Cognito sub)."""
        response = self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": "PROFILE"})
        return response.get("Item")

    # ──────────────────────────────────────────────────────────────────────
    # Chat Sessions
    # ──────────────────────────────────────────────────────────────────────

    def save_session(
        self,
        user_id: str,
        session_id: str,
        title: str,
        messages: list[dict],
        ttl_days: int = 90,
    ) -> dict:
        """Create or update a chat session."""
        now = self._now_iso()
        ttl = int(time.time()) + (ttl_days * 86400)
        item = {
            "PK": f"USER#{user_id}",
            "SK": f"SESSION#{session_id}",
            "EntityType": "ChatSession",
            "session_id": session_id,
            "user_id": user_id,
            "title": title,
            "messages": messages,
            "last_accessed": now,
            "created_at": now,
            "TTL": ttl,
        }
        self.table.put_item(Item=item)
        logger.info("session_saved", user_id=user_id, session_id=session_id)
        return item

    def get_session(self, user_id: str, session_id: str) -> dict | None:
        """Retrieve a specific chat session."""
        response = self.table.get_item(Key={"PK": f"USER#{user_id}", "SK": f"SESSION#{session_id}"})
        return response.get("Item")

    def list_user_sessions(self, user_id: str, limit: int = 50) -> list[dict]:
        """List all chat sessions for a user, newest first."""
        response = self.table.query(
            KeyConditionExpression=(
                Key("PK").eq(f"USER#{user_id}") & Key("SK").begins_with("SESSION#")
            ),
            ScanIndexForward=False,  # newest first
            Limit=limit,
        )
        return response.get("Items", [])

    def delete_session(self, user_id: str, session_id: str) -> None:
        """Delete a chat session."""
        self.table.delete_item(Key={"PK": f"USER#{user_id}", "SK": f"SESSION#{session_id}"})
        logger.info("session_deleted", user_id=user_id, session_id=session_id)

    def update_session_messages(
        self, user_id: str, session_id: str, title: str, messages: list[dict]
    ) -> None:
        """Update messages and title for an existing session."""
        self.table.update_item(
            Key={"PK": f"USER#{user_id}", "SK": f"SESSION#{session_id}"},
            UpdateExpression="SET messages = :m, title = :t, last_accessed = :la",
            ExpressionAttributeValues={
                ":m": messages,
                ":t": title,
                ":la": self._now_iso(),
            },
        )

    # ──────────────────────────────────────────────────────────────────────
    # Knowledge Base Metadata
    # ──────────────────────────────────────────────────────────────────────

    def save_kb_metadata(
        self,
        kb_id: str,
        name: str,
        description: str,
        s3_bucket: str,
        s3_prefix: str,
        created_by: str,
        data_source_id: str | None = None,
    ) -> dict:
        """Store metadata for a dynamically created Knowledge Base."""
        item = {
            "PK": f"KB#{kb_id}",
            "SK": "META",
            "EntityType": "KnowledgeBase",
            "kb_id": kb_id,
            "name": name,
            "description": description,
            "s3_bucket": s3_bucket,
            "s3_prefix": s3_prefix,
            "data_source_id": data_source_id,
            "created_by": created_by,
            "created_at": self._now_iso(),
            "status": "creating",
        }
        self.table.put_item(Item=item)
        logger.info("kb_metadata_saved", kb_id=kb_id, name=name)
        return item

    def get_kb_metadata(self, kb_id: str) -> dict | None:
        """Retrieve metadata for a Knowledge Base."""
        response = self.table.get_item(Key={"PK": f"KB#{kb_id}", "SK": "META"})
        return response.get("Item")

    def update_kb_status(self, kb_id: str, status: str, **extra) -> None:
        """Update the status of a Knowledge Base."""
        update_expr = "SET #s = :s, updated_at = :u"
        expr_values: dict[str, Any] = {":s": status, ":u": self._now_iso()}
        expr_names = {"#s": "status"}

        for key, value in extra.items():
            update_expr += f", {key} = :{key}"
            expr_values[f":{key}"] = value

        self.table.update_item(
            Key={"PK": f"KB#{kb_id}", "SK": "META"},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
            ExpressionAttributeNames=expr_names,
        )

    def list_knowledge_bases(self) -> list[dict]:
        """List all Knowledge Bases (scan for EntityType=KnowledgeBase)."""
        response = self.table.scan(
            FilterExpression="EntityType = :et",
            ExpressionAttributeValues={":et": "KnowledgeBase"},
        )
        return response.get("Items", [])

    def delete_kb_metadata(self, kb_id: str) -> None:
        """Delete KB metadata."""
        self.table.delete_item(Key={"PK": f"KB#{kb_id}", "SK": "META"})

    # ──────────────────────────────────────────────────────────────────────
    # Execution Profiles
    # ──────────────────────────────────────────────────────────────────────

    def save_execution_profile(self, agent_id: str, version: str, profile_data: dict) -> dict:
        """Store an execution profile for an agent."""
        item = {
            "PK": f"PROFILE#{agent_id}",
            "SK": f"VERSION#{version}",
            "EntityType": "ExecutionProfile",
            "agent_id": agent_id,
            "version": version,
            "profile": profile_data,
            "created_at": self._now_iso(),
        }
        self.table.put_item(Item=item)
        logger.info("execution_profile_saved", agent_id=agent_id, version=version)
        return item

    def get_execution_profile(self, agent_id: str, version: str = "latest") -> dict | None:
        """Retrieve an execution profile."""
        response = self.table.get_item(
            Key={"PK": f"PROFILE#{agent_id}", "SK": f"VERSION#{version}"}
        )
        return response.get("Item")
