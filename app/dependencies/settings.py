"""
Unified application settings.
Loads all config from .env and environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS
    aws_region: str = "us-east-1"
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None

    # Model provider: 'openai' or 'bedrock'
    model_provider: str = "bedrock"

    # Bedrock
    bedrock_model_id: str = "amazon.nova-pro-v1:0"
    bedrock_kb_id: str | None = None

    # S3
    s3_bucket_name: str | None = None

    # OpenAI (used by Strands agent)
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o"

    # Aurora PostgreSQL
    db_host: str | None = None
    db_name: str = "postgres"
    db_user: str = "postgres"
    db_password: str | None = None
    db_port: int = 5432

    # App
    log_level: str = "INFO"
    jwt_secret_key: str | None = None
    jwt_access_token_exp_minutes: int = 60

    # AgentCore Memory
    agentcore_memory_enabled: bool = False
    agentcore_memory_id: str | None = None
    agentcore_memory_event_expiry: int = 604800  # 7 days for short-term events
    agentcore_memory_semantic_enabled: bool = (
        True  # Enable semantic extraction for long-term memory
    )

    # Guardrails
    guardrail_id: str | None = None
    guardrail_version: str | None = None


@lru_cache()
def get_settings() -> Settings:
    return Settings()
