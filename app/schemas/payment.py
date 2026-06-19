"""Pydantic schemas for payment request/response.

`PaymentCreate` validates inbound payload; `PaymentResponse` and
`PaymentDetailResponse` serialize outbound state. `from_attributes=True`
lets each be built directly from an ORM instance.
"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.audit_event import AuditEventResponse
from app.schemas.ledger_entry import LedgerEntryResponse


class PaymentCreate(BaseModel):
    merchant_id: UUID
    amount: int = Field(gt=0, description="Amount in minor units (cents)")


class SettleRequest(BaseModel):
    """Empty body for settlement; idempotency key arrives via header."""


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    merchant_id: UUID
    idempotency_key: str
    amount: int
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class PaymentDetailResponse(PaymentResponse):
    ledger_entries: list[LedgerEntryResponse] = Field(default_factory=list)
    audit_events: list[AuditEventResponse] = Field(default_factory=list)
