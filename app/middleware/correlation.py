# coaction_agent_platform/app/middleware/correlation.py
"""Correlation ID middleware per HLD Section 12.

Ensures every request has a correlation ID for end-to-end tracing.
"""

import uuid
import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = structlog.get_logger(__name__)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Injects or propagates X-Correlation-Id header on every request."""

    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-Id", str(uuid.uuid4()))

        # Bind to structlog context for all downstream logging
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        response = await call_next(request)
        response.headers["X-Correlation-Id"] = correlation_id

        structlog.contextvars.unbind_contextvars("correlation_id")
        return response
