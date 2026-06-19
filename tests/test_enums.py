"""Tests for domain enums."""
import pytest

from app.models.enums import PaymentStatus, LedgerEntryType, AuditEventType


def test_payment_status_values() -> None:
    assert PaymentStatus.PENDING == "pending"
    assert PaymentStatus.SETTLED == "settled"
    assert PaymentStatus.FAILED == "failed"


def test_ledger_entry_type_values() -> None:
    assert LedgerEntryType.DEBIT == "debit"


def test_audit_event_type_values() -> None:
    assert AuditEventType.PAYMENT_CREATED == "payment_created"
    assert AuditEventType.SETTLEMENT_INITIATED == "settlement_initiated"
    assert AuditEventType.SETTLEMENT_SUCCEEDED == "settlement_succeeded"
    assert AuditEventType.SETTLEMENT_FAILED == "settlement_failed"


def test_enums_are_string_enum() -> None:
    from enum import Enum

    for enum_cls in (PaymentStatus, LedgerEntryType, AuditEventType):
        assert issubclass(enum_cls, str)
        assert issubclass(enum_cls, Enum)


def test_db_module_exposes_base_engine_sessionlocal_get_db() -> None:
    from app.db import Base, engine, SessionLocal, get_db

    assert Base is not None
    assert engine is not None
    assert SessionLocal is not None
    assert callable(get_db)
