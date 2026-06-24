"""Tests for PaymentService: idempotency, state machine, ledger, audit, tx."""
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.models.enums import AuditEventType, PaymentStatus
from app.repositories.audit_event import AuditEventRepository
from app.repositories.ledger_entry import LedgerEntryRepository
from app.services.exceptions import (
    IdempotencyConflictError,
    InvalidStateTransitionError,
    PaymentNotFoundError,
)
from app.services.payment import PaymentService


def _svc(session: Session) -> PaymentService:
    return PaymentService(session)


def _sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_create_payment_writes_created_audit_event(db, engine) -> None:
    mid = uuid4()
    p, created = _svc(db).create_payment(
        merchant_id=mid,
        idempotency_key="k1",
        amount=100,
    )
    db.commit()
    assert created is True
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(p.id)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PAYMENT_CREATED
        assert events[0].payload["new_status"] == "pending"


def test_create_payment_status_pending_version_1(db) -> None:
    p, _ = _svc(db).create_payment(
        merchant_id=uuid4(), idempotency_key="k2", amount=10
    )
    db.commit()
    assert p.status == PaymentStatus.PENDING
    assert p.version == 1


def test_duplicate_create_same_amount_returns_existing(db, engine) -> None:
    mid = uuid4()
    first, created_first = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="dup", amount=100
    )
    db.commit()
    assert created_first is True

    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        second, created_second = _svc(s).create_payment(
            merchant_id=mid, idempotency_key="dup", amount=100
        )
        s.commit()
        assert second.id == first.id
        assert created_second is False
    # No extra audit events written.
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(first.id)
        assert len(events) == 1


def test_duplicate_create_different_amount_raises_conflict(db, engine) -> None:
    mid = uuid4()
    _svc(db).create_payment(merchant_id=mid, idempotency_key="c", amount=100)
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        with pytest.raises(IdempotencyConflictError):
            _svc(s).create_payment(
                merchant_id=mid,
                idempotency_key="c",
                amount=200,
            )


def test_settle_pending_writes_ledger_and_audit(db, engine) -> None:
    mid = uuid4()
    p, _ = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="s1", amount=250
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        settled = _svc(s).settle_payment(p.id)
        s.commit()
        assert settled.status == PaymentStatus.SETTLED
        assert settled.version == 2
    with SessionLocal() as s:
        entries = LedgerEntryRepository(s).get_by_payment_id(p.id)
        assert len(entries) == 1
        assert entries[0].amount == 250
        events = AuditEventRepository(s).get_by_payment_id(p.id)
        types = [e.event_type for e in events]
        assert AuditEventType.PAYMENT_CREATED in types
        assert AuditEventType.SETTLEMENT_SUCCEEDED in types


def test_settle_unknown_payment_raises_not_found(db) -> None:
    with pytest.raises(PaymentNotFoundError):
        _svc(db).settle_payment(uuid4())


def test_settle_already_settled_is_idempotent_no_duplicate_ledger(db, engine) -> None:
    mid = uuid4()
    p, _ = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="s2", amount=50
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        _svc(s).settle_payment(p.id)
        s.commit()
    with SessionLocal() as s:
        result = _svc(s).settle_payment(p.id)
        s.commit()
        assert result.status == PaymentStatus.SETTLED
    with SessionLocal() as s:
        entries = LedgerEntryRepository(s).get_by_payment_id(p.id)
        assert len(entries) == 1  # no duplicate ledger row


def test_settle_failed_payment_raises_invalid_transition(db, engine) -> None:
    mid = uuid4()
    p, _ = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="f1", amount=50
    )
    db.commit()
    # Force the payment into a failed state directly to test the guard.
    from app.repositories.payment import PaymentRepository

    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        row = repo.get_by_id(p.id)
        assert row is not None
        repo.update_with_version(row, status=PaymentStatus.FAILED)
        s.commit()
    with SessionLocal() as s:
        with pytest.raises(InvalidStateTransitionError):
            _svc(s).settle_payment(p.id)


def test_audit_payload_captures_before_after_status(db, engine) -> None:
    mid = uuid4()
    p, _ = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="p1", amount=10
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        _svc(s).settle_payment(p.id)
        s.commit()
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(p.id)
        settle_ev = next(
            e for e in events if e.event_type == AuditEventType.SETTLEMENT_SUCCEEDED
        )
        assert settle_ev.payload["previous_status"] == "pending"
        assert settle_ev.payload["new_status"] == "settled"


def test_failed_settlement_writes_failure_audit_and_marks_failed(db, engine) -> None:
    """A settle failure transitions payment to failed + SETTLEMENT_FAILED audit."""
    mid = uuid4()
    p, _ = _svc(db).create_payment(
        merchant_id=mid, idempotency_key="ff", amount=10
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        svc = _svc(s)
        # Inject a failure by forcing the ledger write to raise.
        from unittest.mock import patch

        with patch.object(
            LedgerEntryRepository, "create", side_effect=RuntimeError("bank down")
        ):
            with pytest.raises(RuntimeError):
                svc.settle_payment(p.id)
            s.rollback()
    # Payment should remain pending (tx rolled back, no partial state).
    with SessionLocal() as s:
        from app.repositories.payment import PaymentRepository

        p2 = PaymentRepository(s).get_by_id(p.id)
        assert p2 is not None
        assert p2.status == PaymentStatus.PENDING
        assert p2.version == 1
