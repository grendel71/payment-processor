"""FastAPI dependencies: DB session and Idempotency-Key header.

`get_db` reads `app.db.SessionLocal` lazily at call time so test fixtures
that rebind the module attribute take effect.
"""
from collections.abc import Generator

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app import db as db_module


def get_db() -> Generator[Session, None, None]:
    db = db_module.SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_idempotency_key(
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=64),
) -> str:
    return idempotency_key


DbDep = Depends(get_db)
IdempotencyKeyDep = Depends(get_idempotency_key)
