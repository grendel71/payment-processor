"""FastAPI application factory and lifespan."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.payments import router as payments_router
from app.services.exceptions import (
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PaymentNotFoundError,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema is owned by Alembic. Production runs `alembic upgrade head`
    # out-of-band (init container / deploy step). Tests do the same in the
    # session-scoped `engine` fixture in tests/conftest.py.
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Payment Processor",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(payments_router)

    @app.exception_handler(PaymentNotFoundError)
    async def _not_found(_: Request, exc: PaymentNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(IdempotencyConflictError)
    async def _conflict(_: Request, exc: IdempotencyConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(InvalidStateTransitionError)
    async def _invalid_transition(
        _: Request, exc: InvalidStateTransitionError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    return app


app = create_app()
