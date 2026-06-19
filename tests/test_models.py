"""Tests for ORM models: table creation, columns, constraints, FKs."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import db as db_module
from app.db import Base
from app.models.audit_event import AuditEvent
from app.models.enums import AuditEventType, LedgerEntryType, PaymentStatus
from app.models.ledger_entry import LedgerEntry
from app.models.payment import Payment
from app.models.settlement import Settlement, SettlementPayment


def setup_function() -> None:
    Base.metadata.drop_all(bind=db_module.engine)
    Base.metadata.create_all(bind=db_module.engine)


def test_tables_created() -> None:
    inspector = inspect(db_module.engine)
    tables = set(inspector.get_table_names())
    assert {
        "payments",
        "ledger_entries",
        "audit_events",
        "settlements",
        "settlement_payments",
    }.issubset(tables)


def test_payment_columns() -> None:
    cols = {c["name"] for c in inspect(db_module.engine).get_columns("payments")}
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


def test_payment_idempotency_unique_constraint() -> None:
    uniques = inspect(db_module.engine).get_unique_constraints("payments")
    names = {u["name"] for u in uniques}
    assert "uq_merchant_idempotency" in names
    matched = next(u for u in uniques if u["name"] == "uq_merchant_idempotency")
    assert set(matched["column_names"]) == {"merchant_id", "idempotency_key"}


def test_ledger_entry_fk_and_columns() -> None:
    cols = {c["name"] for c in inspect(db_module.engine).get_columns("ledger_entries")}
    assert {"id", "payment_id", "entry_type", "amount", "created_at"}.issubset(cols)
    fks = inspect(db_module.engine).get_foreign_keys("ledger_entries")
    assert any(
        fk["referred_table"] == "payments" and fk["constrained_columns"] == ["payment_id"]
        for fk in fks
    )


def test_audit_event_fk_and_columns() -> None:
    cols = {c["name"] for c in inspect(db_module.engine).get_columns("audit_events")}
    assert {"id", "payment_id", "event_type", "payload", "created_at"}.issubset(cols)
    fks = inspect(db_module.engine).get_foreign_keys("audit_events")
    assert any(
        fk["referred_table"] == "payments" and fk["constrained_columns"] == ["payment_id"]
        for fk in fks
    )


def test_settlement_tables_exist() -> None:
    inspector = inspect(db_module.engine)
    cols = {c["name"] for c in inspector.get_columns("settlements")}
    assert {"id", "status", "total_amount", "settled_at", "created_at"}.issubset(cols)
    join_cols = {c["name"] for c in inspector.get_columns("settlement_payments")}
    assert {"settlement_id", "payment_id"}.issubset(join_cols)
    uniques = inspector.get_unique_constraints("settlement_payments")
    assert any("payment_id" in u["column_names"] for u in uniques)


def test_payment_idempotency_constraint_enforced() -> None:
    mid = uuid4()
    with db_module.SessionLocal() as s:
        s.add(
            Payment(
                id=uuid4(),
                merchant_id=mid,
                idempotency_key="k1",
                amount=100,
                status=PaymentStatus.PENDING,
                version=1,
            )
        )
        s.commit()
        s.add(
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
            s.commit()
            raise AssertionError("expected IntegrityError")
        except IntegrityError:
            s.rollback()


def test_ledger_entry_relationship() -> None:
    with db_module.SessionLocal() as s:
        pid = uuid4()
        p = Payment(
            id=pid,
            merchant_id=uuid4(),
            idempotency_key="r1",
            amount=500,
            status=PaymentStatus.SETTLED,
            version=2,
        )
        s.add(p)
        s.flush()
        le = LedgerEntry(
            id=uuid4(),
            payment_id=pid,
            entry_type=LedgerEntryType.DEBIT,
            amount=500,
        )
        s.add(le)
        s.commit()
        assert s.get(Payment, pid).ledger_entries[0].amount == 500


def test_audit_event_payload_json() -> None:
    with db_module.SessionLocal() as s:
        pid = uuid4()
        s.add(
            Payment(
                id=pid,
                merchant_id=uuid4(),
                idempotency_key="a1",
                amount=50,
                status=PaymentStatus.PENDING,
                version=1,
            )
        )
        s.flush()
        payload = {"previous_status": "pending", "new_status": "settled"}
        s.add(
            AuditEvent(
                id=uuid4(),
                payment_id=pid,
                event_type=AuditEventType.SETTLEMENT_SUCCEEDED,
                payload=payload,
            )
        )
        s.commit()
        ev = s.get(Payment, pid).audit_events[0]
        assert ev.payload["new_status"] == "settled"


def test_created_at_defaults_to_utc() -> None:
    with db_module.SessionLocal() as s:
        pid = uuid4()
        s.add(
            Payment(
                id=pid,
                merchant_id=uuid4(),
                idempotency_key="t1",
                amount=10,
                status=PaymentStatus.PENDING,
                version=1,
            )
        )
        s.commit()
        p = s.get(Payment, pid)
        assert p.created_at is not None
        assert p.created_at.tzinfo is None or p.created_at.tzinfo == timezone.utc
