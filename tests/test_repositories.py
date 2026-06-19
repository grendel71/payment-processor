"""Tests for repositories: CRUD, idempotency lookup, versioned update."""
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.db import Base, SessionLocal, engine
from app.models.enums import AuditEventType, LedgerEntryType, PaymentStatus
from app.repositories.audit_event import AuditEventRepository
from app.repositories.ledger_entry import LedgerEntryRepository
from app.repositories.payment import PaymentRepository


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_create_and_get_payment_by_id() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        repo.create(
            id=pid,
            merchant_id=uuid4(),
            idempotency_key="k1",
            amount=100,
        )
        s.commit()
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        assert p is not None
        assert p.amount == 100
        assert p.status == PaymentStatus.PENDING
        assert p.version == 1


def test_get_by_idempotency_returns_match() -> None:
    mid = uuid4()
    pid = uuid4()
    with SessionLocal() as s:
        PaymentRepository(s).create(
            id=pid, merchant_id=mid, idempotency_key="kx", amount=10
        )
        s.commit()
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_idempotency(mid, "kx")
        assert p is not None and p.id == pid


def test_get_by_idempotency_returns_none_when_missing() -> None:
    with SessionLocal() as s:
        assert PaymentRepository(s).get_by_idempotency(uuid4(), "nope") is None


def test_get_by_id_returns_none_when_missing() -> None:
    with SessionLocal() as s:
        assert PaymentRepository(s).get_by_id(uuid4()) is None


def test_update_with_version_increments_and_persists() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        PaymentRepository(s).create(
            id=pid, merchant_id=uuid4(), idempotency_key="v1", amount=5
        )
        s.commit()
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        p = repo.get_by_id(pid)
        assert p is not None
        repo.update_with_version(p, status=PaymentStatus.SETTLED)
        s.commit()
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        assert p is not None
        assert p.status == PaymentStatus.SETTLED
        assert p.version == 2


def test_update_with_version_detects_stale_version() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        PaymentRepository(s).create(
            id=pid, merchant_id=uuid4(), idempotency_key="v2", amount=5
        )
        s.commit()
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        p = repo.get_by_id(pid)
        assert p is not None
        p.version = 99  # simulate stale
        with pytest.raises(Exception):
            repo.update_with_version(p, status=PaymentStatus.SETTLED)


def test_create_duplicate_idempotency_raises() -> None:
    mid = uuid4()
    with SessionLocal() as s:
        repo = PaymentRepository(s)
        repo.create(id=uuid4(), merchant_id=mid, idempotency_key="dup", amount=1)
        s.commit()
        # repo.create() flushes, so the IntegrityError surfaces at the
        # duplicate create call, not at commit time.
        with pytest.raises(IntegrityError):
            repo.create(id=uuid4(), merchant_id=mid, idempotency_key="dup", amount=2)


def test_ledger_entry_create_and_list_by_payment() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        PaymentRepository(s).create(
            id=pid, merchant_id=uuid4(), idempotency_key="l1", amount=50
        )
        LedgerEntryRepository(s).create(
            payment_id=pid,
            entry_type=LedgerEntryType.DEBIT,
            amount=50,
        )
        s.commit()
    with SessionLocal() as s:
        entries = LedgerEntryRepository(s).get_by_payment_id(pid)
        assert len(entries) == 1
        assert entries[0].amount == 50


def test_audit_event_create_and_list_by_payment() -> None:
    pid = uuid4()
    with SessionLocal() as s:
        PaymentRepository(s).create(
            id=pid, merchant_id=uuid4(), idempotency_key="a1", amount=50
        )
        AuditEventRepository(s).create(
            payment_id=pid,
            event_type=AuditEventType.PAYMENT_CREATED,
            payload={"new_status": "pending"},
        )
        s.commit()
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(pid)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PAYMENT_CREATED
