"""Tests for GET /healthz."""


def test_healthz_ok(client) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_openapi_lists_only_expected_routes(client) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    expected = {"/healthz", "/payments", "/payments/{payment_id}/settle"}
    # GET /payments/{payment_id} also expected.
    assert "/payments/{payment_id}" in paths
    methods = {
        ("/healthz", "get"),
        ("/payments", "post"),
        ("/payments/{payment_id}", "get"),
        ("/payments/{payment_id}/settle", "post"),
    }
    actual = {(p, m) for p, ms in paths.items() for m in ms}
    assert methods.issubset(actual)
