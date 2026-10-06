"""Catch-up settlement and forced mock sales, with fresh quotes and account locks."""
import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from app.database import SessionLocal
from app.models.account import Account
from app.models.portfolio import Portfolio
from app.models.misu import MisuDebt, MisuSettlement
from app.services import misu_service as misu
from app.services.order_service import create_order, trade_cost
from app.services.order_automation_service import fetch_execution_quote, usable_quote, in_session
from app.schemas.order import OrderRequest

state = {'running': False, 'last_cycle_at': None}
logger = logging.getLogger(__name__)


def healthy():
    checked = state['last_cycle_at']
    return bool(state['running'] and checked and 0 <= (datetime.now(timezone.utc) - misu.utc(checked)).total_seconds() <= 120)


def process_account(db, account_id, quotes, now):
    account = db.query(Account).filter_by(account_id=account_id).with_for_update().populate_existing().first()
    if not account:
        return
    misu.settle_account(db, account, now)
    due = [d for d in misu.debts(db, account_id) if d.remaining > 0 and misu.utc(d.liquidate_at) <= now]
    if not due or not in_session(now) or not misu.business_day(now.astimezone(misu.KST).date()):
        return
    info = misu.snapshot(db, account_id, now)
    needed = sum((d.remaining for d in due), Decimal(0)) - info['pending_proceeds']
    for holding in db.query(Portfolio).filter_by(account_id=account_id).order_by(Portfolio.portfolio_id).all():
        if needed <= 0:
            break
        quote = quotes.get(holding.symbol_code)
        if not usable_quote(quote, now):
            continue  # Never invent a fill for a halted/unquoted instrument.
        price = Decimal(str(quote['price']))
        def net(qty):
            gross, fee, tax = trade_cost('매도', price, qty)
            return gross - fee - tax
        low, high = 1, holding.hold_quantity
        while low < high:
            mid = (low + high) // 2
            if net(mid) >= needed:
                high = mid
            else:
                low = mid + 1
        request = OrderRequest(account_id=account_id, symbol_code=holding.symbol_code,
                               order_type='매도', price_type='시장가', price=price, quantity=low)
        order = create_order(db, request, account.user_id, price, commit=False, now=now)
        order.funding_type = '반대매매'
        needed -= net(low)


async def run_cycle(session_factory=SessionLocal, quote_loader=fetch_execution_quote, now=None):
    current = now or datetime.now(timezone.utc)
    with session_factory() as db:
        ids = {r[0] for r in db.execute(select(MisuDebt.account_id).where(MisuDebt.remaining > 0))}
        ids |= {r[0] for r in db.execute(select(MisuSettlement.account_id).where(MisuSettlement.applied.is_(False)))}
        due_ids = {d.account_id for d in db.query(MisuDebt).filter(MisuDebt.remaining > 0).all() if misu.utc(d.liquidate_at) <= current}
        symbols = {r[0] for r in db.execute(select(Portfolio.symbol_code).where(Portfolio.account_id.in_(due_ids)))} if due_ids else set()
    quotes = {}
    if in_session(current) and misu.business_day(current.astimezone(misu.KST).date()):
        for symbol in symbols:
            try:
                quotes[symbol] = await quote_loader(symbol, current)
            except Exception:
                quotes[symbol] = None
    for account_id in sorted(ids):
        with session_factory() as db:
            try:
                process_account(db, account_id, quotes, now or datetime.now(timezone.utc))
                db.commit()
            except Exception:
                db.rollback()
                logger.warning('Mock misu account %s processing rolled back', account_id)


async def monitor():
    state['running'] = True
    try:
        while True:
            try:
                await run_cycle()
                state['last_cycle_at'] = datetime.now(timezone.utc)
            except Exception:
                logger.warning('Mock misu monitor unavailable; retrying')
            await asyncio.sleep(30)
    finally:
        state['running'] = False
