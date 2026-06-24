"""Tests for app.db._build_dsn env-var handling."""
from app.db import _build_dsn, Base, engine, SessionLocal, get_db


def test_build_dsn_uses_database_url_when_set(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://override/db")
    assert _build_dsn() == "postgresql://override/db"


def test_build_dsn_composes_from_pg_env_vars(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "alice")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_DB", "payments")
    monkeypatch.setenv("POSTGRES_HOST", "db.example.com")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    assert _build_dsn() == "postgresql+psycopg2://alice:secret@db.example.com:6543/payments"


def test_build_dsn_uses_dev_defaults_when_no_env(monkeypatch) -> None:
    # Strip every relevant env var to confirm defaults match .env.example
    for k in ["DATABASE_URL", "POSTGRES_USER", "POSTGRES_PASSWORD",
              "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT"]:
        monkeypatch.delenv(k, raising=False)
    dsn = _build_dsn()
    assert dsn == "postgresql+psycopg2://pp:pp@localhost:5432/paymentprocessor"


def test_build_dsn_normalizes_heroku_postgres_scheme(monkeypatch) -> None:
    """Heroku/Render emit `postgres://` in DATABASE_URL; SQLAlchemy needs `postgresql://`."""
    monkeypatch.setenv("DATABASE_URL", "postgres://user:pass@host:5432/db")
    dsn = _build_dsn()
    assert dsn == "postgresql://user:pass@host:5432/db"


def test_engine_uses_pool_pre_ping() -> None:
    # Sanity: engine module-level creation should enable pool_pre_ping so
    # stale containerized PG connections don't yield OperationalError mid-request
    assert engine.pool._pre_ping is True


def test_base_session_get_db_remain_available() -> None:
    # Verify the public surface is intact: Base is a DeclarativeBase,
    # SessionLocal binds to the import-time engine with expire_on_commit=False,
    # and get_db is a generator function suitable for FastAPI's dependency injection.
    import inspect
    from sqlalchemy.orm import DeclarativeBase
    assert issubclass(Base, DeclarativeBase)
    assert SessionLocal.kw["bind"] is engine
    assert SessionLocal.kw["expire_on_commit"] is False
    assert inspect.isgeneratorfunction(get_db)
