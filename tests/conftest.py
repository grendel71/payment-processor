"""Shared pytest fixtures: dockerized Postgres + Alembic + per-test TRUNCATE.

Run with `docker compose up -d db` already up. The `docker_pg` fixture
waits for connectivity and creates the `paymentprocessor_test` database
if missing (so tests don't pollute dev data on the same PG instance).
"""
import os
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import db as db_module
from app.main import create_app


def _pg_env() -> dict[str, str]:
    """Read POSTGRES_* env vars (with dev-safe defaults matching .env.example)."""
    return {
        "user": os.getenv("POSTGRES_USER", "pp"),
        "password": os.getenv("POSTGRES_PASSWORD", "pp"),
        "host": os.getenv("POSTGRES_HOST", "localhost"),
        "port": os.getenv("POSTGRES_PORT", "5432"),
        "test_db": os.getenv("POSTGRES_TEST_DB", "paymentprocessor_test"),
    }


@pytest.fixture(scope="session")
def docker_pg() -> Iterator[Engine]:
    """Wait for the dockerized Postgres; create the test DB if missing.

    Assumes `docker compose up -d db` is running. This fixture only
    waits for connectivity and creates the test DB; it does NOT start
    the container.
    """
    env = _pg_env()
    admin_url = (
        f"postgresql+psycopg2://{env['user']}:{env['password']}"
        f"@{env['host']}:{env['port']}/postgres"
    )
    admin_engine = create_engine(admin_url, pool_pre_ping=True, future=True)

    with admin_engine.connect() as conn:
        conn.execute(text("COMMIT"))  # close any implicit tx
        existing = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": env["test_db"]},
        ).scalar()
        if not existing:
            conn.execute(text(f'CREATE DATABASE "{env["test_db"]}"'))
    admin_engine.dispose()

    test_url = (
        f"postgresql+psycopg2://{env['user']}:{env['password']}"
        f"@{env['host']}:{env['port']}/{env['test_db']}"
    )
    test_engine = create_engine(test_url, pool_pre_ping=True, future=True)

    deadline = time.time() + 30
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with test_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1)
    if last_err is not None:
        raise RuntimeError(
            f"Postgres not available at {env['host']}:{env['port']}/{env['test_db']}; "
            "run `docker compose up -d db` first"
        ) from last_err

    yield test_engine
    test_engine.dispose()


@pytest.fixture(scope="session")
def engine(docker_pg: Engine) -> Iterator[Engine]:
    """Run `alembic upgrade head` once against the test DB; yield engine."""
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    test_dsn = docker_pg.url.render_as_string(hide_password=False)
    old_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = test_dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        if old_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old_database_url

    yield docker_pg


@pytest.fixture(autouse=True, scope="function")
def truncate_tables(engine: Engine) -> Iterator[None]:
    """TRUNCATE all application tables between tests.

    Alembic's `alembic_version` table is deliberately preserved so the
    migration state remains valid throughout the test session.
    """
    with engine.connect() as conn:
        table_names = [
            row[0]
            for row in conn.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'public' "
                    "AND tablename <> 'alembic_version' "
                    "ORDER BY tablename;"
                )
            )
        ]
        if table_names:
            quoted = ", ".join(f'"{name}"' for name in table_names)
            conn.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE;"))
        conn.commit()
    yield


@pytest.fixture()
def db(engine: Engine) -> Iterator[Session]:
    """Yield a Session bound to the test engine."""
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@pytest.fixture()
def client(engine: Engine, truncate_tables: None) -> Iterator[TestClient]:
    """FastAPI TestClient with app bound to the test engine."""
    test_session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        future=True,
        expire_on_commit=False,
    )
    original_engine = db_module.engine
    original_session = db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = test_session_factory
    app = create_app()
    try:
        with TestClient(app) as c:
            yield c
    finally:
        db_module.engine = original_engine
        db_module.SessionLocal = original_session
