"""Shared pytest fixtures: in-memory SQLite per test, FastAPI client."""
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module
from app.db import Base
from app.main import create_app


@pytest.fixture()
def isolated_db() -> Generator[None, None, None]:
    """Rebind app.db to a fresh in-memory SQLite for one test.

    StaticPool shares a single connection so the fixture's create_all,
    the app lifespan's create_all, and request sessions all see the same
    in-memory database.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=engine)

    original_engine = db_module.engine
    original_session = db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = SessionLocal
    try:
        yield
    finally:
        db_module.engine = original_engine
        db_module.SessionLocal = original_session
        engine.dispose()


@pytest.fixture()
def client(isolated_db: None) -> Generator[TestClient, None, None]:
    app = create_app()
    with TestClient(app) as c:
        yield c
