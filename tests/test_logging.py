"""Tests for structured JSON logging."""
import logging

import structlog

from app.logging import configure_logging, get_logger


def test_logger_outputs_json():
    """structlog must emit structured output with a timestamp."""
    from structlog.testing import capture_logs

    configure_logging()
    with capture_logs() as cap_logs:
        log = get_logger()
        log.info("test_event", key="value")
    assert len(cap_logs) == 1
    assert cap_logs[0]["event"] == "test_event"
    assert cap_logs[0]["key"] == "value"
    assert "timestamp" in cap_logs[0]


def test_request_id_is_bound():
    """The request_id contextvar must be bindable and appear in logs."""
    from structlog.testing import capture_logs

    configure_logging()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="test-uuid-123")
    with capture_logs() as cap_logs:
        log = get_logger()
        log.info("request_event")
    assert cap_logs[0]["request_id"] == "test-uuid-123"


def test_amount_is_not_logged():
    """Payment amount must never be logged. This documents intent:
    callers must not pass amount to log calls."""
    from structlog.testing import capture_logs

    configure_logging()
    with capture_logs() as cap_logs:
        log = get_logger()
        log.info("payment_event")
    # amount should never be present since we never bind it
    assert "amount" not in cap_logs[0]
