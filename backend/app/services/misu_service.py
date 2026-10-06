"""Persisted mock cash shortfalls. Never sends a brokerage order or moves real money."""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
from functools import lru_cache
import holidays
from fastapi import HTTPException
from app.models.misu import MisuDebt, MisuSettlement

KST = timezone(timedelta(hours=9))
MARGIN_RATE = Decimal('0.5')


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


@lru_cache(maxsize=12)
def closures(year):
    return holidays.financial_holidays('XKRX', years=year)


def business_day(day):
    return day.weekday() < 5 and day not in closures(day.year)


def after_sessions(day, count):
    while count:
        day += timedelta(days=1)
        if business_day(day):
            count -= 1
    return day


def settlement_at(now):
    local = now.astimezone(KST)
    return datetime.combine(after_sessions(local.date(), 2), time(17), KST).astimezone(timezone.utc)


def debts(db, account_id):
    return db.query(MisuDebt).filter(MisuDebt.account_id == account_id).order_by(MisuDebt.due_at, MisuDebt.id).all()


def snapshot(db, account_id, now=None):
    now = now or datetime.now(timezone.utc)
    records = debts(db, account_id)
    active = [d for d in records if d.remaining > 0]
    pending = db.query(MisuSettlement).filter_by(account_id=account_id, applied=False).all()
    uncovered = [d for d in active if d.remaining > sum(
        (s.amount for s in pending if utc(s.available_at) <= utc(d.due_at)), Decimal(0))]
    frozen = [utc(d.frozen_until) for d in records if d.frozen_until]
    # Read-only computation enforces expiry even between monitor cycles.
    frozen += [utc(d.liquidate_at) + timedelta(days=30) for d in uncovered if now > utc(d.due_at)]
    until = max(frozen, default=None)
    return dict(debt=sum((d.remaining for d in active), Decimal(0)),
                pending_proceeds=sum((s.amount for s in pending), Decimal(0)),
                due_at=min((utc(d.due_at) for d in active), default=None),
                frozen_until=until if until and until > now else None,
                overdue=any(now > utc(d.due_at) for d in uncovered),
                settlements=[dict(amount=s.amount, available_at=utc(s.available_at)) for s in pending])


def assert_buy_allowed(db, account, misu=False, now=None):
    state = snapshot(db, account.account_id, now)
    if state['debt'] or state['pending_proceeds']:
        raise HTTPException(400, '미수 부족금과 매도 결제가 남아 추가 매수를 제한합니다. 미수 내역을 확인해 주세요.')
    if misu and state['frozen_until']:
        raise HTTPException(400, '미수 이용 제한 기간입니다. 현금 주문만 가능합니다.')


def validate_misu(db, account, req, gross, commission, now):
    if req.order_type != '매수' or req.price_type != '시장가':
        raise HTTPException(400, '모의 미수거래는 즉시 시장가 매수만 지원합니다.')
    if not req.client_request_id or not req.misu_risk_ack:
        raise HTTPException(400, '미수 위험 확인과 주문 요청 번호가 필요합니다.')
    local = now.astimezone(KST)
    if not business_day(local.date()) or not time(9) <= local.time() < time(15, 20):
        raise HTTPException(400, '모의 미수 주문은 거래일 09:00~15:20에 가능합니다.')
    assert_buy_allowed(db, account, True, now)
    required = (gross * MARGIN_RATE).quantize(Decimal('1'), rounding=ROUND_CEILING) + commission
    if account.withdrawable_cash < required:
        raise HTTPException(400, '증거금 50%와 수수료를 낼 현금이 부족합니다.')
    shortfall = gross + commission - account.withdrawable_cash
    if shortfall <= 0:
        raise HTTPException(400, '현금으로 전액 결제할 수 있습니다. 현금 주문을 이용해 주세요.')
    return shortfall


def record_debt(db, account, order, req, shortfall, now):
    due = settlement_at(now)
    liquidation = datetime.combine(after_sessions(due.astimezone(KST).date(), 1), time(9), KST).astimezone(timezone.utc)
    db.add(MisuDebt(account_id=account.account_id, order_id=order.order_id,
                    request_id=str(req.client_request_id), original=shortfall, remaining=shortfall,
                    due_at=due, liquidate_at=liquidation))


def freeze_overdue(db, account_id, now):
    for debt in debts(db, account_id):
        if debt.remaining > 0 and now > utc(debt.due_at) and not debt.frozen_until:
            # Deliberately stricter mock rule: any overdue amount restricts this account.
            debt.frozen_until = utc(debt.liquidate_at) + timedelta(days=30)


def apply_payment(db, account_id, amount):
    for debt in debts(db, account_id):
        paid = min(debt.remaining, amount)
        debt.remaining -= paid
        amount -= paid
        if not amount:
            break
    return amount


def settle_account(db, account, now):
    """Caller holds account row lock and commits; mark overdue before late receipts."""
    # Receipts dated no later than the deadline prevent a false freeze after downtime.
    pending = db.query(MisuSettlement).filter_by(account_id=account.account_id, applied=False).order_by(MisuSettlement.available_at).all()
    for receipt in pending:
        if utc(receipt.available_at) > now:
            continue
        freeze_overdue(db, account.account_id, utc(receipt.available_at))
        remainder = apply_payment(db, account.account_id, receipt.amount)
        account.withdrawable_cash += remainder
        account.balance += remainder
        receipt.applied = True
    freeze_overdue(db, account.account_id, now)
    db.flush()


def defer_sale(db, account, order, amount, now):
    state = snapshot(db, account.account_id, now)
    if not state['debt'] and not state['pending_proceeds']:
        return
    # Existing ordinary orders credit immediately. Undo credit only for misu accounts.
    account.withdrawable_cash -= amount
    account.balance -= amount
    db.add(MisuSettlement(account_id=account.account_id, order_id=order.order_id,
                          amount=amount, available_at=settlement_at(now)))
