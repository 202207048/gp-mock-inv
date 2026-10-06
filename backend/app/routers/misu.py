from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.account import Account
from app.models.misu import MisuPayment
from app.models.user import User
from app.utils.deps import get_current_user
from app.services import misu_service as service
from app.services.misu_monitor import state, healthy

router = APIRouter(tags=['모의 미수'])


def owned(db, account_id, user_id, lock=False):
    query = db.query(Account).filter_by(account_id=account_id, user_id=user_id)
    if lock:
        query = query.with_for_update().populate_existing()
    account = query.first()
    if not account:
        raise HTTPException(404, '계좌를 찾을 수 없습니다.')
    return account


@router.get('/{account_id}')
def status(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    owned(db, account_id, user.user_id)
    result = service.snapshot(db, account_id)
    result.update(margin_rate=service.MARGIN_RATE, monitor_running=healthy(), last_cycle_at=state['last_cycle_at'])
    return result


class PaymentRequest(BaseModel):
    amount: Decimal = Field(gt=0, le=1000000000000, decimal_places=0, allow_inf_nan=False)
    client_request_id: UUID


@router.post('/{account_id}/repay')
def repay(account_id: int, req: PaymentRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    account = owned(db, account_id, user.user_id, True)
    previous = db.query(MisuPayment).filter_by(account_id=account_id, request_id=str(req.client_request_id)).first()
    if previous:
        if previous.amount != req.amount:
            raise HTTPException(409, '같은 요청 번호의 입금액이 다릅니다.')
        return service.snapshot(db, account_id)
    now = datetime.now(timezone.utc)
    service.settle_account(db, account, now)
    amount = service.snapshot(db, account_id, now)['debt']
    if req.amount > amount:
        raise HTTPException(400, '남은 부족금보다 많이 입금할 수 없습니다. 새로고침 후 확인해 주세요.')
    service.apply_payment(db, account_id, req.amount)
    db.add(MisuPayment(account_id=account_id, request_id=str(req.client_request_id), amount=req.amount, created_at=now))
    db.commit()
    return service.snapshot(db, account_id, now)


@router.post('/{account_id}/settle')
def settle(account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    account = owned(db, account_id, user.user_id, True)
    service.settle_account(db, account, datetime.now(timezone.utc))
    db.commit()
    return service.snapshot(db, account_id)
