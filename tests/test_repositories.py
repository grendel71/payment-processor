"""Tests for repositories: CRUD, idempotency lookup, versioned update."""
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models.enums import AuditEventType, LedgerEntryType, PaymentStatus
from app.repositories.audit_event import AuditEventRepository
from app.repositories.ledger_entry import LedgerEntryRepository
from app.repositories.payment import PaymentRepository


def _sessionmaker(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def test_create_and_get_payment_by_id(db, engine) -> None:
    pid = uuid4()
    repo = PaymentRepository(db)
    repo.create(
        id=pid,
        merchant_id=uuid4(),
        idempotency_key="k1",
        amount=100,
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        assert p is not None
        assert p.amount == 100
        assert p.status == PaymentStatus.PENDING
        assert p.version == 1


def test_get_by_idempotency_returns_match(db, engine) -> None:
    mid = uuid4()
    pid = uuid4()
    PaymentRepository(db).create(
        id=pid, merchant_id=mid, idempotency_key="kx", amount=10
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_idempotency(mid, "kx")
        assert p is not None and p.id == pid


def test_get_by_idempotency_returns_none_when_missing(db) -> None:
    assert PaymentRepository(db).get_by_idempotency(uuid4(), "nope") is None


def test_get_by_id_returns_none_when_missing(db) -> None:
    assert PaymentRepository(db).get_by_id(uuid4()) is None


def test_update_with_version_increments_and_persists(db, engine) -> None:
    pid = uuid4()
    PaymentRepository(db).create(
        id=pid, merchant_id=uuid4(), idempotency_key="v1", amount=5
    )
    db.commit()
    repo = PaymentRepository(db)
    p = repo.get_by_id(pid)
    assert p is not None
    repo.update_with_version(p, status=PaymentStatus.SETTLED)
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        p = PaymentRepository(s).get_by_id(pid)
        assert p is not None
        assert p.status == PaymentStatus.SETTLED
        assert p.version == 2


def test_update_with_version_detects_stale_version(db) -> None:
    pid = uuid4()
    PaymentRepository(db).create(
        id=pid, merchant_id=uuid4(), idempotency_key="v2", amount=5
    )
    db.commit()
    repo = PaymentRepository(db)
    p = repo.get_by_id(pid)
    assert p is not None
    p.version = 99  # simulate stale
    with pytest.raises(Exception):
        repo.update_with_version(p, status=PaymentStatus.SETTLED)


def test_create_duplicate_idempotency_raises(db) -> None:
    mid = uuid4()
    repo = PaymentRepository(db)
    repo.create(id=uuid4(), merchant_id=mid, idempotency_key="dup", amount=1)
    db.commit()
    # repo.create() flushes, so the IntegrityError surfaces at the
    # duplicate create call, not at commit time.
    with pytest.raises(IntegrityError):
        repo.create(id=uuid4(), merchant_id=mid, idempotency_key="dup", amount=2)


def test_ledger_entry_create_and_list_by_payment(db, engine) -> None:
    pid = uuid4()
    PaymentRepository(db).create(
        id=pid, merchant_id=uuid4(), idempotency_key="l1", amount=50
    )
    LedgerEntryRepository(db).create(
        payment_id=pid,
        entry_type=LedgerEntryType.DEBIT,
        amount=50,
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        entries = LedgerEntryRepository(s).get_by_payment_id(pid)
        assert len(entries) == 1
        assert entries[0].amount == 50


def test_audit_event_create_and_list_by_payment(db, engine) -> None:
    pid = uuid4()
    PaymentRepository(db).create(
        id=pid, merchant_id=uuid4(), idempotency_key="a1", amount=50
    )
    AuditEventRepository(db).create(
        payment_id=pid,
        event_type=AuditEventType.PAYMENT_CREATED,
        payload={"new_status": "pending"},
    )
    db.commit()
    SessionLocal = _sessionmaker(engine)
    with SessionLocal() as s:
        events = AuditEventRepository(s).get_by_payment_id(pid)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.PAYMENT_CREATED
