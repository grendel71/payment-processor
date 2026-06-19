"""Database engine, session factory, and declarative base.

PostgreSQL is the only supported backend. Dev-safe defaults in
`_build_dsn()` match `.env.example` so module import never crashes
without env configured — production overrides via env vars or the
`DATABASE_URL` shortcut.
"""
import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _build_dsn() -> str:
    """Construct a Postgres DSN from environment.

    `DATABASE_URL` (if set) wins; otherwise compose from individual
    `POSTGRES_*` vars. Each has a dev-safe default matching
    `.env.example` so module import never crashes. Single source of
    truth — `migrations/env.py` imports this function rather than
    duplicating it.
    """
    if url := os.getenv("DATABASE_URL"):
        # Heroku/Render emit bare `postgres://`; SQLAlchemy 2.x requires `postgresql://`.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url
    user = os.getenv("POSTGRES_USER", "pp")
    password = os.getenv("POSTGRES_PASSWORD", "pp")
    db = os.getenv("POSTGRES_DB", "paymentprocessor")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"


# Module-level engine + session factory. Tests override `engine` and
# `SessionLocal` via monkeypatch (see tests/conftest.py).
engine = create_engine(
    _build_dsn(),
    pool_pre_ping=True,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    future=True,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a session, rolling back on error."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
