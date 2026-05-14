from domain.models import (
    AgentInvocationResponse,
    SourceCitation,
)


def test_invocation_response_model():
    """Test that AgentInvocationResponse serializes correctly."""
    response = AgentInvocationResponse(
        status="success",
        answer="Hello world",
        citations=[
            SourceCitation(source_id="src-1", title="Test", uri="https://example.com"),
        ],
        session_id="session-123",
        correlation_id="corr-456",
        model_id="anthropic.claude-v3",
        metadata={"follow_up_questions": ["What about X?"]},
    )

    assert response.status == "success"
    assert response.answer == "Hello world"
    assert response.session_id == "session-123"
    assert response.model_id == "anthropic.claude-v3"
    assert len(response.citations) == 1
    assert response.citations[0].source_id == "src-1"
    assert "follow_up_questions" in response.metadata
