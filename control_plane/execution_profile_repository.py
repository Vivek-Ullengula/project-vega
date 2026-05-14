# coaction_agent_platform/control_plane/execution_profile_repository.py
"""Execution profile repository per HLD Section 5.

Stores and retrieves ExecutionProfiles from DynamoDB.
"""

import yaml
import structlog
from pathlib import Path

from domain.models import ExecutionProfile

logger = structlog.get_logger(__name__)


class ExecutionProfileRepository:
    """Loads execution profiles from DynamoDB, with YAML file fallback."""

    def __init__(self, dynamodb_adapter=None, config_dir: str | None = None):
        self.dynamodb = dynamodb_adapter
        self.config_dir = config_dir
        self._cache: dict[str, ExecutionProfile] = {}

    async def get_profile(self, agent_id: str, version: str = "latest") -> ExecutionProfile:
        """Load an ExecutionProfile for the given agent_id.

        Resolution order:
        1. In-memory cache
        2. DynamoDB
        3. Disk config file (.json or .yaml)
        """
        cache_key = f"{agent_id}:{version}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Try DynamoDB
        if self.dynamodb:
            stored = self.dynamodb.get_execution_profile(agent_id, version)
            if stored and stored.get("profile"):
                profile = ExecutionProfile(**stored["profile"])
                self._cache[cache_key] = profile
                logger.info("profile_loaded_from_dynamodb", agent_id=agent_id)
                return profile

        # Fallback: Disk config file (.json)
        if self.config_dir:
            profile = self._load_from_disk(agent_id)
            if profile:
                self._cache[cache_key] = profile
                logger.info("profile_loaded_from_disk", agent_id=agent_id)
                return profile

        raise ValueError(f"No execution profile found for agent '{agent_id}'")

    def _load_from_disk(self, agent_id: str) -> ExecutionProfile | None:
        """Load a profile from a JSON config file."""
        if not self.config_dir:
            return None

        import json
        config_path = Path(self.config_dir)
        for json_file in config_path.glob("*.json"):
            try:
                with open(json_file) as f:
                    data = json.load(f)
                if data and data.get("agent_id") == agent_id:
                    return ExecutionProfile(**data)
            except Exception as e:
                logger.error("json_load_failed", file=str(json_file), error=str(e))

        return None

    async def save_profile(self, profile: ExecutionProfile, version: str = "latest") -> None:
        """Persist an execution profile to DynamoDB."""
        if self.dynamodb:
            self.dynamodb.save_execution_profile(
                agent_id=profile.agent_id,
                version=version,
                profile_data=profile.model_dump(),
            )
            cache_key = f"{profile.agent_id}:{version}"
            self._cache[cache_key] = profile
            logger.info("profile_saved", agent_id=profile.agent_id, version=version)

    def invalidate_cache(self, agent_id: str) -> None:
        """Clear cached profiles for an agent."""
        keys_to_remove = [k for k in self._cache if k.startswith(f"{agent_id}:")]
        for k in keys_to_remove:
            del self._cache[k]
