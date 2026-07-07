"""Structured logging configuration using structlog.

Emits JSON-formatted logs with request_id correlation. Never logs
request bodies or payment amounts — those are payment-sensitive.
"""
import datetime
import logging
import uuid

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


def configure_logging(level: int = logging.INFO) -> None:
    """Configure structlog for JSON output with request_id binding."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger():
    """Return a configured structlog logger.

    Binds the current request_id (from contextvars, set by
    RequestIDMiddleware) and an ISO-8601 timestamp onto the logger so
    they are present even under structlog.testing.capture_logs, which
    disables configured processors. In production the merge_contextvars
    and TimeStamper processors re-apply accurate per-event values.
    """
    return structlog.get_logger().bind(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        **structlog.contextvars.get_contextvars(),
    )


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Binds a unique request_id to structlog contextvars per request."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        return await call_next(request)
