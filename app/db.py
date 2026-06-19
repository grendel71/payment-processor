"""Database engine, session factory, and declarative base.

SQLite is used for local/test execution per the implementation plan.
Postgres-specific behaviors (UUID, JSON) are accessed via SQLAlchemy's
generic types so the same models work on both backends.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


# Module-level engine + session factory. Tests override `engine` via
# `create_engine("sqlite:///:memory:")` and rebind `SessionLocal`.
DATABASE_URL = "sqlite:///./paymentprocessor.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
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
