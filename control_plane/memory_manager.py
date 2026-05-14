import boto3
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class MemoryManager:
    """Automates creation and management of Bedrock AgentCore Memory resources."""

    def __init__(self, region_name="us-east-1"):
        self.client = boto3.client("bedrock-agentcore-control", region_name=region_name)

    def create_memory(self, name: str, retention_days: int = 90) -> str:
        """Creates an AgentCore Memory resource."""
        try:
            logger.info(f"Creating AgentCore Memory: {name}")
            response = self.client.create_memory(name=name, eventExpiryDuration=retention_days)
            mem_obj = response.get("memory", {})
            memory_id = mem_obj.get("id") or response.get("memoryId") or response.get("id") or name
            logger.info(f"Memory created with ID: {memory_id}")
            return memory_id
        except Exception as e:
            if "already exists" in str(e).lower():
                logger.info(f"Memory {name} already exists. Attempting recovery via get_memory...")
                return self.get_memory_id_by_name(name) or name
            logger.error(f"Failed to create Memory: {e}")
            raise

    def get_memory_id_by_name(self, name: str) -> Optional[str]:
        """
        Check if a memory resource with this name already exists.
        Attempts direct lookup via get_memory using the resource name as identifier.
        """
        try:
            response = self.client.get_memory(memoryId=name)
            mem_obj = response.get("memory", {})
            return mem_obj.get("id") or response.get("memoryId") or response.get("id") or name
        except Exception:
            # Fallback to scanning list summaries if API supports mapping
            try:
                response = self.client.list_memories()
                for mem in response.get("memories", []):
                    # Some versions return id mapping to name
                    if mem.get("id") == name or mem.get("arn", "").endswith(f"/{name}"):
                        return mem.get("id")
            except Exception:
                pass
            return None
