"""Tests for DB-level append-only enforcement on ledger_entries and audit_events."""
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal


PAYMENT_ID = "00000000-0000-0000-0000-000000000003"


def _insert_payment(session):
    session.execute(
        text(
            "INSERT INTO payments (id, merchant_id, idempotency_key, amount, status, version, created_at, updated_at) "
            "VALUES (:id, :mid, 'test-key', 100, 'pending', 1, now(), now())"
        ),
        {"id": PAYMENT_ID, "mid": "00000000-0000-0000-0000-000000000001"},
    )
    session.flush()


def _insert_ledger_entry(session):
    session.execute(
        text(
            "INSERT INTO ledger_entries (id, payment_id, entry_type, amount, created_at) "
            "VALUES ('00000000-0000-0000-0000-000000000001', :pid, 'debit', 100, now())"
        ),
        {"pid": PAYMENT_ID},
    )
    session.flush()


def _insert_audit_event(session):
    session.execute(
        text(
            "INSERT INTO audit_events (id, payment_id, event_type, payload, created_at) "
            "VALUES ('00000000-0000-0000-0000-000000000002', :pid, 'payment_created', '{}', now())"
        ),
        {"pid": PAYMENT_ID},
    )
    session.flush()


@pytest.fixture
def session():
    s = SessionLocal()
    try:
        _insert_payment(s)
        yield s
    finally:
        s.rollback()
        s.close()


def test_update_ledger_entry_raises(session):
    _insert_ledger_entry(session)
    with pytest.raises(DBAPIError) as exc_info:
        session.execute(text("UPDATE ledger_entries SET amount = 999"))
    assert "append-only" in str(exc_info.value).lower()


def test_delete_ledger_entry_raises(session):
    _insert_ledger_entry(session)
    with pytest.raises(DBAPIError) as exc_info:
        session.execute(text("DELETE FROM ledger_entries"))
    assert "append-only" in str(exc_info.value).lower()


def test_update_audit_event_raises(session):
    _insert_audit_event(session)
    with pytest.raises(DBAPIError) as exc_info:
        session.execute(text("UPDATE audit_events SET payload = '{}'"))
    assert "append-only" in str(exc_info.value).lower()


def test_delete_audit_event_raises(session):
    _insert_audit_event(session)
    with pytest.raises(DBAPIError) as exc_info:
        session.execute(text("DELETE FROM audit_events"))
    assert "append-only" in str(exc_info.value).lower()
