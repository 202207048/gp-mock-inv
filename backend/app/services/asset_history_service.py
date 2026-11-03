"""Server-valued daily history. Never accepts client-supplied balances or dates."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from app.models.account import Account
from app.models.portfolio import Portfolio
from app.models.asset_history import AssetHistory
from app.services.kis_service import get_current_price
from app.services.misu_service import snapshot

KST = timezone(timedelta(hours=9))


def owned_account(db, account_id, user_id, lock=False):
    query = db.query(Account).filter_by(account_id=account_id, user_id=user_id)
    if lock:
        query = query.with_for_update().populate_existing()
    account = query.first()
    if account is None:
        raise HTTPException(404, '계좌를 찾을 수 없습니다.')
    return account


def account_state(db, account):
    rows = db.query(Portfolio).filter_by(account_id=account.account_id).populate_existing().all()
    positions = tuple(sorted((row.symbol_code, row.hold_quantity) for row in rows if row.hold_quantity != 0))
    misu = snapshot(db, account.account_id)
    return Decimal(account.withdrawable_cash), Decimal(misu['debt']), Decimal(misu['pending_proceeds']), positions


async def record_asset_history(db, account_id, user_id, now=None):
    before = account_state(db, owned_account(db, account_id, user_id))
    db.rollback()  # Do not hold DB locks while waiting on price providers.
    market = Decimal(0)
    for code, quantity in before[3]:
        if quantity < 0:
            raise HTTPException(503, '보유수량을 확인할 수 없어 자산 이력을 저장하지 못했습니다.')
        try:
            quote = await get_current_price(code)
            price = Decimal(str(quote.get('current_price')))
            if not price.is_finite() or price <= 0:
                raise ValueError('Missing price')
        except Exception as exc:
            raise HTTPException(503, '일부 시세를 확인할 수 없어 자산 이력을 저장하지 못했습니다.') from exc
        market += price * quantity
    # Serialize with orders and other snapshots, and reject a portfolio changed during valuation.
    account = owned_account(db, account_id, user_id, lock=True)
    current = account_state(db, account)
    if current != before:
        db.rollback()
        raise HTTPException(409, '조회 중 계좌가 변경되었습니다. 다시 조회해 주세요.')
    stamp = now or datetime.now(timezone.utc)
    day = stamp.astimezone(KST).date()
    cash, debt, pending, _ = current
    amounts = dict(cash=cash, market=market, debt=debt, pending_proceeds=pending)
    amounts = {name: value.quantize(Decimal('.01')) for name, value in amounts.items()}
    amounts['total'] = amounts['cash'] + amounts['market'] + amounts['pending_proceeds'] - amounts['debt']
    row = db.query(AssetHistory).filter_by(account_id=account_id, date=day).first()
    if row is None:
        row = AssetHistory(account_id=account_id, date=day)
        db.add(row)
    row.observed_at = stamp
    row.is_demo = False
    for name, value in amounts.items():
        setattr(row, name, value)
    db.commit()
    return day


def read_asset_week(db, account_id, user_id, week_offset=0, now=None):
    owned_account(db, account_id, user_id)
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date()
    start = today - timedelta(days=today.weekday(), weeks=week_offset)
    end = start + timedelta(days=6)
    rows = db.query(AssetHistory).filter(AssetHistory.account_id == account_id,
        AssetHistory.date >= start, AssetHistory.date <= end).order_by(AssetHistory.date).all()
    return dict(account_id=account_id, week_start=start, week_end=end, timezone='Asia/Seoul',
        rows=[dict(date=row.date, observed_at=row.observed_at, total=row.total, is_demo=row.is_demo) for row in rows])
