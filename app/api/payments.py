"""Payment API routes.

Routes stay thin: parse request, call PaymentService, commit, serialize.
All domain rules live in the service layer.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import DbDep, IdempotencyKeyDep
from app.schemas.payment import PaymentCreate, PaymentDetailResponse
from app.services.payment import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentDetailResponse)
def create_payment(
    payload: PaymentCreate,
    response: Response,
    db: Session = DbDep,
    idempotency_key: str = IdempotencyKeyDep,
) -> PaymentDetailResponse:
    svc = PaymentService(db)
    payment, newly_created = svc.create_payment(
        merchant_id=payload.merchant_id,
        idempotency_key=idempotency_key,
        amount=payload.amount,
    )
    db.commit()
    response.status_code = (
        status.HTTP_201_CREATED if newly_created else status.HTTP_200_OK
    )
    # Re-read so the response reflects committed relationship state.
    fresh = svc.get_payment_detail(payment.id)
    return PaymentDetailResponse.model_validate(fresh)


@router.get("/{payment_id}", response_model=PaymentDetailResponse)
def get_payment(payment_id: UUID, db: Session = DbDep) -> PaymentDetailResponse:
    payment = PaymentService(db).get_payment_detail(payment_id)
    return PaymentDetailResponse.model_validate(payment)


@router.post("/{payment_id}/settle", response_model=PaymentDetailResponse)
def settle_payment(
    payment_id: UUID,
    db: Session = DbDep,
    idempotency_key: str = IdempotencyKeyDep,
) -> PaymentDetailResponse:
    svc = PaymentService(db)
    svc.settle_payment(payment_id)
    db.commit()
    # Re-read so ledger_entries / audit_events reflect committed state.
    fresh = svc.get_payment_detail(payment_id)
    return PaymentDetailResponse.model_validate(fresh)
