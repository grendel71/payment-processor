"""Pydantic schema for ledger entry responses."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LedgerEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    entry_type: str
    amount: int
    created_at: datetime
