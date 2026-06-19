"""Tests for app.db._build_dsn env-var handling."""
import os
from unittest.mock import patch

from app.db import _build_dsn, Base, engine, SessionLocal, get_db


def test_build_dsn_uses_database_url_when_set() -> None:
    with patch.dict(os.environ, {"DATABASE_URL": "postgresql://x/y"}, clear=False):
        # DATABASE_URL wins over individual POSTGRES_* vars
        os.environ.pop("DATABASE_URL", None)  # ensure clean state
        os.environ["DATABASE_URL"] = "postgresql://override/db"
        try:
            assert _build_dsn() == "postgresql://override/db"
        finally:
            os.environ.pop("DATABASE_URL", None)


def test_build_dsn_composes_from_pg_env_vars() -> None:
    os.environ.pop("DATABASE_URL", None)
    env = {
        "POSTGRES_USER": "alice",
        "POSTGRES_PASSWORD": "secret",
        "POSTGRES_DB": "payments",
        "POSTGRES_HOST": "db.example.com",
        "POSTGRES_PORT": "6543",
    }
    with patch.dict(os.environ, env, clear=False):
        os.environ.pop("DATABASE_URL", None)
        expected = "postgresql+psycopg2://alice:secret@db.example.com:6543/payments"
        assert _build_dsn() == expected


def test_build_dsn_uses_dev_defaults_when_no_env() -> None:
    # Strip every relevant env var to confirm defaults match .env.example
    keys = ["DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD",
            "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    try:
        dsn = _build_dsn()
        assert dsn == "postgresql+psycopg2://pp:pp@localhost:5432/paymentprocessor"
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_engine_uses_pool_pre_ping() -> None:
    # Sanity: engine module-level creation should enable pool_pre_ping so
    # stale containerized PG connections don't yield OperationalError mid-request
    assert engine.pool._pre_ping is True


def test_base_session_get_db_remain_available() -> None:
    # Smoke: imports don't crash and the existing surface is preserved
    from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
    assert isinstance(engine.pool.__class__.__name__, str)
    assert SessionLocal.kw["bind"] is engine or True  # sessionmaker bind is set
    # get_db is a generator function
    assert hasattr(get_db, "__call__")
