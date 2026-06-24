"""Smoke tests for Alembic migrations.

Requires `docker compose up -d db` to be running first. The fixtures
(engine, docker_pg) live in tests/conftest.py — they are stubs at this
point in the refactor; the conftest is rewritten in Task 5 to wire them
to the dockerized PG. This file deliberately uses fixtures that do not
exist yet, so it imports-fails until Task 5 lands.
"""
import pytest

from app.db import Base


def test_alembic_upgrade_head_creates_all_tables(engine) -> None:
    """`alembic upgrade head` must materialize the 5 expected tables.

    The `engine` fixture (session-scoped) runs `alembic upgrade head`
    against the test DB before yielding. If migrations are missing or
    produce the wrong tables, this test fails on import or assertion.
    """
    from sqlalchemy import inspect

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "payments",
        "ledger_entries",
        "audit_events",
        "settlements",
        "settlement_payments",
    }.issubset(tables)


def test_alembic_creates_idempotency_unique_constraint(engine) -> None:
    """The uq_merchant_idempotency constraint must exist after upgrade."""
    from sqlalchemy import inspect

    uniques = inspect(engine).get_unique_constraints("payments")
    names = {u["name"] for u in uniques}
    assert "uq_merchant_idempotency" in names
    matched = next(u for u in uniques if u["name"] == "uq_merchant_idempotency")
    assert set(matched["column_names"]) == {"merchant_id", "idempotency_key"}


def test_alembic_creates_settlement_payment_unique_constraint(engine) -> None:
    """The uq_settlement_payment_payment constraint must exist after upgrade."""
    from sqlalchemy import inspect

    uniques = inspect(engine).get_unique_constraints("settlement_payments")
    assert any(
        "payment_id" in u["column_names"] for u in uniques
    ), "expected unique constraint on settlement_payments(payment_id)"


def test_alembic_creates_indexes(engine) -> None:
    """Indexes on payments.merchant_id, ledger_entries.payment_id, audit_events.payment_id."""
    from sqlalchemy import inspect

    inspector = inspect(engine)
    assert any(
        "merchant_id" in (i["column_names"] if i.get("column_names") else [])
        for i in inspector.get_indexes("payments")
    )
    assert any(
        "payment_id" in (i["column_names"] if i.get("column_names") else [])
        for i in inspector.get_indexes("ledger_entries")
    )
    assert any(
        "payment_id" in (i["column_names"] if i.get("column_names") else [])
        for i in inspector.get_indexes("audit_events")
    )
