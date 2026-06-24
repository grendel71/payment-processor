"""Tests for ORM models: table creation, columns, constraints, FKs."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.models.audit_event import AuditEvent
from app.models.enums import AuditEventType, LedgerEntryType, PaymentStatus
from app.models.ledger_entry import LedgerEntry
from app.models.payment import Payment


def test_tables_created(engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {
        "payments",
        "ledger_entries",
        "audit_events",
        "settlements",
        "settlement_payments",
    }.issubset(tables)


def test_payment_columns(engine) -> None:
    cols = {c["name"] for c in inspect(engine).get_columns("payments")}
    assert {
        "id",
        "merchant_id",
        "idempotency_key",
        "amount",
        "status",
        "version",
        "created_at",
        "updated_at",
    }.issubset(cols)


def test_payment_idempotency_unique_constraint(engine) -> None:
    uniques = inspect(engine).get_unique_constraints("payments")
    names = {u["name"] for u in uniques}
    assert "uq_merchant_idempotency" in names
    matched = next(u for u in uniques if u["name"] == "uq_merchant_idempotency")
    assert set(matched["column_names"]) == {"merchant_id", "idempotency_key"}


def test_ledger_entry_fk_and_columns(engine) -> None:
    cols = {c["name"] for c in inspect(engine).get_columns("ledger_entries")}
    assert {"id", "payment_id", "entry_type", "amount", "created_at"}.issubset(cols)
    fks = inspect(engine).get_foreign_keys("ledger_entries")
    assert any(
        fk["referred_table"] == "payments" and fk["constrained_columns"] == ["payment_id"]
        for fk in fks
    )


def test_audit_event_fk_and_columns(engine) -> None:
    cols = {c["name"] for c in inspect(engine).get_columns("audit_events")}
    assert {"id", "payment_id", "event_type", "payload", "created_at"}.issubset(cols)
    fks = inspect(engine).get_foreign_keys("audit_events")
    assert any(
        fk["referred_table"] == "payments" and fk["constrained_columns"] == ["payment_id"]
        for fk in fks
    )


def test_settlement_tables_exist(engine) -> None:
    inspector = inspect(engine)
    cols = {c["name"] for c in inspector.get_columns("settlements")}
    assert {"id", "status", "total_amount", "settled_at", "created_at"}.issubset(cols)
    join_cols = {c["name"] for c in inspector.get_columns("settlement_payments")}
    assert {"settlement_id", "payment_id"}.issubset(join_cols)
    uniques = inspector.get_unique_constraints("settlement_payments")
    assert any("payment_id" in u["column_names"] for u in uniques)


def test_payment_idempotency_constraint_enforced(db) -> None:
    mid = uuid4()
    db.add(
        Payment(
            id=uuid4(),
            merchant_id=mid,
            idempotency_key="k1",
            amount=100,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    db.commit()
    db.add(
        Payment(
            id=uuid4(),
            merchant_id=mid,
            idempotency_key="k1",
            amount=200,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    try:
        db.commit()
        raise AssertionError("expected IntegrityError")
    except IntegrityError:
        db.rollback()


def test_ledger_entry_relationship(db) -> None:
    pid = uuid4()
    p = Payment(
        id=pid,
        merchant_id=uuid4(),
        idempotency_key="r1",
        amount=500,
        status=PaymentStatus.SETTLED,
        version=2,
    )
    db.add(p)
    db.flush()
    le = LedgerEntry(
        id=uuid4(),
        payment_id=pid,
        entry_type=LedgerEntryType.DEBIT,
        amount=500,
    )
    db.add(le)
    db.commit()
    assert db.get(Payment, pid).ledger_entries[0].amount == 500


def test_audit_event_payload_json(db) -> None:
    pid = uuid4()
    db.add(
        Payment(
            id=pid,
            merchant_id=uuid4(),
            idempotency_key="a1",
            amount=50,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    db.flush()
    payload = {"previous_status": "pending", "new_status": "settled"}
    db.add(
        AuditEvent(
            id=uuid4(),
            payment_id=pid,
            event_type=AuditEventType.SETTLEMENT_SUCCEEDED,
            payload=payload,
        )
    )
    db.commit()
    ev = db.get(Payment, pid).audit_events[0]
    assert ev.payload["new_status"] == "settled"


def test_created_at_defaults_to_utc(db) -> None:
    pid = uuid4()
    db.add(
        Payment(
            id=pid,
            merchant_id=uuid4(),
            idempotency_key="t1",
            amount=10,
            status=PaymentStatus.PENDING,
            version=1,
        )
    )
    db.commit()
    p = db.get(Payment, pid)
    assert p.created_at is not None
    assert p.created_at.tzinfo is None or p.created_at.tzinfo == timezone.utc
