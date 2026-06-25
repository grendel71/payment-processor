"""Tests for health and readiness endpoints."""

from sqlalchemy.exc import OperationalError

from app import db as db_module


def test_healthz_ok(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_ok_when_database_accepts_query(client) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json() == {"status": "ready"}


def test_readyz_unavailable_when_database_query_fails(client, monkeypatch) -> None:
    class BrokenConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement):
            raise OperationalError("SELECT 1", {}, Exception("database unavailable"))

    class BrokenEngine:
        def connect(self):
            return BrokenConnection()

    monkeypatch.setattr(db_module, "engine", BrokenEngine())

    r = client.get("/readyz")

    assert r.status_code == 503
    assert r.json() == {"status": "not_ready"}


def test_openapi_lists_only_expected_routes(client) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    expected = {"/healthz", "/readyz", "/payments", "/payments/{payment_id}/settle"}
    # GET /payments/{payment_id} also expected.
    assert "/payments/{payment_id}" in paths
    methods = {
        ("/healthz", "get"),
        ("/readyz", "get"),
        ("/payments", "post"),
        ("/payments/{payment_id}", "get"),
        ("/payments/{payment_id}/settle", "post"),
    }
    actual = {(p, m) for p, ms in paths.items() for m in ms}
    assert methods.issubset(actual)
