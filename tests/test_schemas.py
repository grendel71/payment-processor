"""Tests for Pydantic request/response schemas."""
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.audit_event import AuditEventResponse
from app.schemas.ledger_entry import LedgerEntryResponse
from app.schemas.payment import (
    PaymentCreate,
    PaymentDetailResponse,
    PaymentResponse,
)


def test_payment_create_valid() -> None:
    p = PaymentCreate(merchant_id=uuid4(), amount=1000)
    assert p.amount == 1000


def test_payment_create_rejects_zero_amount() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(merchant_id=uuid4(), amount=0)


def test_payment_create_rejects_negative_amount() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(merchant_id=uuid4(), amount=-1)


def test_payment_create_requires_merchant_id() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(amount=100)  # type: ignore[call-arg]


def test_payment_create_requires_amount() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate(merchant_id=uuid4())  # type: ignore[call-arg]


def test_payment_response_serializes() -> None:
    r = PaymentResponse(
        id=uuid4(),
        merchant_id=uuid4(),
        idempotency_key="k",
        amount=100,
        status="pending",
        version=1,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    assert r.status == "pending"


def test_ledger_entry_response() -> None:
    r = LedgerEntryResponse(
        id=uuid4(),
        payment_id=uuid4(),
        entry_type="debit",
        amount=100,
        created_at="2026-01-01T00:00:00",
    )
    assert r.entry_type == "debit"


def test_audit_event_response_payload_dict() -> None:
    r = AuditEventResponse(
        id=uuid4(),
        payment_id=uuid4(),
        event_type="payment_created",
        payload={"previous_status": None, "new_status": "pending"},
        created_at="2026-01-01T00:00:00",
    )
    assert r.payload["new_status"] == "pending"


def test_payment_detail_response_nests_children() -> None:
    pid = uuid4()
    detail = PaymentDetailResponse(
        id=pid,
        merchant_id=uuid4(),
        idempotency_key="k",
        amount=100,
        status="settled",
        version=2,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        ledger_entries=[
            LedgerEntryResponse(
                id=uuid4(),
                payment_id=pid,
                entry_type="debit",
                amount=100,
                created_at="2026-01-01T00:00:00",
            )
        ],
        audit_events=[
            AuditEventResponse(
                id=uuid4(),
                payment_id=pid,
                event_type="payment_created",
                payload={},
                created_at="2026-01-01T00:00:00",
            )
        ],
    )
    assert detail.ledger_entries[0].amount == 100
    assert detail.audit_events[0].event_type == "payment_created"


def test_payment_detail_response_defaults_empty_children() -> None:
    d = PaymentDetailResponse(
        id=uuid4(),
        merchant_id=uuid4(),
        idempotency_key="k",
        amount=100,
        status="pending",
        version=1,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    assert d.ledger_entries == []
    assert d.audit_events == []
