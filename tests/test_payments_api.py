"""End-to-end API tests for /payments routes."""
from uuid import uuid4

MERCHANT = "00000000-0000-0000-0000-000000000001"


def _headers(key: str) -> dict[str, str]:
    return {"Idempotency-Key": key, "Content-Type": "application/json"}


def _create(client, key: str, amount: int = 1000, merchant: str = MERCHANT):
    return client.post(
        "/payments",
        headers=_headers(key),
        json={"merchant_id": merchant, "amount": amount},
    )


# ---------------------------------------------------------------------
# POST /payments
# ---------------------------------------------------------------------
def test_create_payment_201(client) -> None:
    r = _create(client, "k1")
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "pending"
    assert body["version"] == 1
    assert body["amount"] == 1000
    assert body["merchant_id"] == MERCHANT
    assert body["idempotency_key"] == "k1"
    assert body["ledger_entries"] == []
    assert [e["event_type"] for e in body["audit_events"]] == ["payment_created"]


def test_create_payment_missing_idempotency_header_422(client) -> None:
    r = client.post(
        "/payments",
        json={"merchant_id": MERCHANT, "amount": 100},
    )
    assert r.status_code == 422


def test_create_payment_invalid_amount_422(client) -> None:
    r = client.post(
        "/payments",
        headers=_headers("bad"),
        json={"merchant_id": MERCHANT, "amount": 0},
    )
    assert r.status_code == 422


def test_create_payment_missing_body_422(client) -> None:
    r = client.post("/payments", headers=_headers("k"))
    assert r.status_code == 422


def test_duplicate_create_same_amount_returns_existing_200(client) -> None:
    first = _create(client, "dup", amount=500)
    assert first.status_code == 201
    second = _create(client, "dup", amount=500)
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


def test_duplicate_create_different_amount_409(client) -> None:
    _create(client, "conflict", amount=500)
    r = _create(client, "conflict", amount=999)
    assert r.status_code == 409


# ---------------------------------------------------------------------
# GET /payments/{id}
# ---------------------------------------------------------------------
def test_get_payment_200_with_detail(client) -> None:
    created = _create(client, "g1").json()
    r = client.get(f"/payments/{created['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == created["id"]
    assert len(body["audit_events"]) == 1


def test_get_payment_unknown_404(client) -> None:
    r = client.get(f"/payments/{uuid4()}")
    assert r.status_code == 404


# ---------------------------------------------------------------------
# POST /payments/{id}/settle
# ---------------------------------------------------------------------
def test_settle_pending_200(client) -> None:
    created = _create(client, "s1").json()
    r = client.post(f"/payments/{created['id']}/settle", headers=_headers("s1"))
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "settled"
    assert body["version"] == 2
    assert len(body["ledger_entries"]) == 1
    assert body["ledger_entries"][0]["entry_type"] == "debit"
    assert body["ledger_entries"][0]["amount"] == 1000
    event_types = [e["event_type"] for e in body["audit_events"]]
    assert "settlement_succeeded" in event_types


def test_settle_idempotent_already_settled_200(client) -> None:
    created = _create(client, "s2").json()
    pid = created["id"]
    first = client.post(f"/payments/{pid}/settle", headers=_headers("s2"))
    assert first.status_code == 200
    second = client.post(f"/payments/{pid}/settle", headers=_headers("s2"))
    assert second.status_code == 200
    # No duplicate ledger row.
    assert len(second.json()["ledger_entries"]) == 1


def test_settle_unknown_payment_404(client) -> None:
    r = client.post(f"/payments/{uuid4()}/settle", headers=_headers("nope"))
    assert r.status_code == 404


def test_settle_missing_idempotency_header_422(client) -> None:
    created = _create(client, "s3").json()
    r = client.post(f"/payments/{created['id']}/settle")
    assert r.status_code == 422
