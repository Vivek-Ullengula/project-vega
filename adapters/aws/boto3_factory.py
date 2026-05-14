"""
Boto3 client factory.

All AWS adapters use one Boto3 client factory. This prevents each
agent or adapter from independently creating clients and makes retry,
timeout, and region behavior consistent.
"""

import boto3
from botocore.config import Config


class Boto3SessionFactory:
    """
    Centralized Boto3 client factory.

    Provides consistent retry, timeout, and region configuration
    across all AWS service adapters. Uses explicit credentials from
    settings to avoid botocore credential chain issues.
    """

    def __init__(self, region_name: str) -> None:
        self.region_name = region_name
        self.config = Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=3,
            read_timeout=30,
        )
        # Use explicit credentials when available (local dev),
        # otherwise fall back to the default credential chain (IAM role in cloud)
        from app.dependencies.settings import get_settings

        settings = get_settings()
        session_kwargs = {"region_name": region_name}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            session_kwargs["aws_access_key_id"] = settings.aws_access_key_id
            session_kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
        self._session = boto3.Session(**session_kwargs)

    def client(self, service_name: str):
        """Create a Boto3 client for the given AWS service."""
        return self._session.client(
            service_name,
            region_name=self.region_name,
            config=self.config,
        )

    @property
    def session(self) -> boto3.Session:
        """Get the underlying boto3 Session."""
        return self._session

    def resource(self, service_name: str):
        """Create a Boto3 resource for the given AWS service."""
        return self._session.resource(
            service_name,
            region_name=self.region_name,
            config=self.config,
        )
