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
