import pytest
from domain.models import ExecutionProfile
from pydantic import ValidationError


def test_valid_profile():
    data = {
        "agent_id": "test_agent",
        "version": "1.0",
        "prompt_template_id": "template_1",
        "model_profile": {
            "model_id": "model_1",
            "provider": "bedrock",
        },
        "retrieval_profile": {
            "knowledge_base_ids": ["kb_1"],
        },
        "memory_profile": {
            "enabled": True,
        },
        "guardrail_profile": {
            "guardrail_id": "gr_1",
        },
    }
    profile = ExecutionProfile(**data)
    assert profile.agent_id == "test_agent"
    assert profile.model_profile.model_id == "model_1"


def test_invalid_profile_missing_field():
    data = {
        "agent_id": "test_agent",
        # Missing other required fields
    }
    with pytest.raises(ValidationError):
        ExecutionProfile(**data)
