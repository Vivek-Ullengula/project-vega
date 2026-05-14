# coaction_agent_platform/app/middleware/errors.py
"""Global error handling middleware.

Catches unhandled exceptions and returns a standard JSON error response.
"""

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = structlog.get_logger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """Global exception handler — returns structured JSON errors."""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            logger.error(
                "unhandled_exception",
                path=request.url.path,
                method=request.method,
                error=str(e),
                error_type=type(e).__name__,
            )
            return JSONResponse(
                status_code=500,
                content={
                    "status": "error",
                    "detail": "An internal error occurred.",
                    "error_type": type(e).__name__,
                },
            )
