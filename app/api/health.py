"""Health check route."""
from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import db as db_module

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
def readyz() -> dict[str, str] | JSONResponse:
    try:
        with db_module.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready"},
        )
    return {"status": "ready"}
