"""Daily post-close valuation, including accounts whose owners do not open the app."""
import asyncio
import logging
from datetime import datetime, time, timezone

from app.database import SessionLocal
from app.models.account import Account
from app.models.asset_history import AssetHistory
from app.services.asset_history_service import KST, record_asset_history

logger = logging.getLogger(__name__)


async def capture_daily(session_factory=SessionLocal, now=None):
    stamp = now or datetime.now(timezone.utc)
    local = stamp.astimezone(KST)
    if local.time() < time(15, 40):
        return
    cutoff = datetime.combine(local.date(), time(15, 40), KST).astimezone(timezone.utc)
    with session_factory() as db:
        recorded = db.query(AssetHistory.account_id).filter(AssetHistory.date == local.date(),
                                                         AssetHistory.observed_at >= cutoff)
        accounts = db.query(Account.account_id, Account.user_id).filter(~Account.account_id.in_(recorded)).all()
    for account_id, user_id in accounts:
        try:
            with session_factory() as db:
                await record_asset_history(db, account_id, user_id, now=now)
        except Exception:
            logger.warning('Daily asset valuation unavailable for account %s; retry next cycle', account_id)
        await asyncio.sleep(.1)


async def monitor():
    while True:
        try:
            await capture_daily()
        except Exception:
            logger.warning('Daily asset history cycle failed; retry next cycle')
        await asyncio.sleep(15 * 60)
