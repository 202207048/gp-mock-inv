"""인증된 보유 사실과 최신 공개 데이터를 결합한다. AI 생성은 수행하지 않는다."""
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.config import settings
from app.models.account import Account
from app.models.order import Order
from app.models.portfolio import Portfolio
from app.models.security import ItemMaster
from app.schemas.portfolio_brief import (
    HoldingFacts, PortfolioBriefFacts, PriceContext, RecentNews,
)
from app.services.entry_price_context import (
    calculate_entry_price_context, calculate_recent_trend, load_daily_rows, unavailable,
)
from app.services.kis_service import get_current_price
from app.services.news_service import get_news_by_symbol, get_news_by_symbol_around
from app.services.position_replay import PositionReplayResult, replay_current_position


@dataclass(frozen=True)
class PortfolioFactsSnapshot:
    account_id: int
    symbol_code: str
    security_name: str | None
    quantity: int
    avg_price: Decimal
    replay: PositionReplayResult


def load_portfolio_facts(
    db: Session, account_id: int, symbol_code: str, user_id: int,
) -> PortfolioFactsSnapshot:
    code = symbol_code.strip()
    if not code:
        raise HTTPException(400, "종목 코드를 입력해 주세요.")
    # One read statement keeps holding and order history in the same DB snapshot,
    # without holding an account write lock while waiting for external data.
    rows = (
        db.query(Account.account_id, Portfolio, Order, ItemMaster.name)
        .select_from(Account)
        .outerjoin(Portfolio, and_(Portfolio.account_id == Account.account_id,
                                  Portfolio.symbol_code == code))
        .outerjoin(Order, and_(Order.account_id == Account.account_id,
                              Order.symbol_code == code, Order.status == "체결"))
        .outerjoin(ItemMaster, ItemMaster.symbol_code == Portfolio.symbol_code)
        .filter(Account.account_id == account_id, Account.user_id == user_id)
        .populate_existing()
        .all()
    )
    if not rows:
        raise HTTPException(404, "계좌를 찾을 수 없습니다.")
    holding = rows[0][1]
    if holding is None or holding.hold_quantity <= 0:
        raise HTTPException(404, "현재 보유 중인 종목이 아닙니다.")
    if len({row[1].portfolio_id for row in rows}) != 1:
        raise HTTPException(409, "보유 정보가 일치하지 않습니다.")
    try:
        avg_price = Decimal(str(holding.avg_price))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(409, "평균단가 정보가 올바르지 않습니다.") from None
    if not avg_price.is_finite():
        raise HTTPException(409, "평균단가 정보가 올바르지 않습니다.")
    history = [row[2] for row in rows if row[2] is not None]
    replay = replay_current_position(account_id, code, holding.hold_quantity, history)
    name = (rows[0][3] or "").strip() or None
    return PortfolioFactsSnapshot(account_id, code, name, holding.hold_quantity, avg_price, replay)


def _finite(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return number if number.is_finite() else None


async def _current_quote(symbol_code: str) -> tuple[Decimal | None, Decimal | None]:
    """(현재가, 오늘 등락률). 실패하면 둘 다 None."""
    try:
        quote = await get_current_price(symbol_code)
        price = _finite(quote.get("current_price"))
        rate = None if isinstance(quote.get("change_rate"), bool) else _finite(quote.get("change_rate"))
        return (price if price is not None and price > 0 else None), rate
    except Exception:
        # Upstream exceptions may contain credentials; never log or expose them.
        return None, None


async def _recent_news(symbol_code: str) -> list[RecentNews]:
    try:
        rows = await get_news_by_symbol(symbol_code, 5)
        if not isinstance(rows, list):
            return []
        news = []
        for row in rows[:5]:
            if not isinstance(row, dict):
                continue
            try:
                title = row.get("title")
                if not isinstance(title, str) or not title.strip():
                    continue
                news.append(RecentNews(
                    title=title.strip(), summary=row.get("summary") or "",
                    source=row.get("source"), date=row.get("date"), url=row.get("url"),
                ))
            except ValidationError:
                continue
        return news
    except Exception:
        return []


KST = timezone(timedelta(hours=9))
ENTRY_NEWS_DAYS = 4


def _article_time(value) -> datetime | None:
    for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt).replace(tzinfo=KST)
        except ValueError:
            continue
    return None


async def _entry_news(symbol_code: str, position) -> list[RecentNews]:
    """포지션 시작 전 ENTRY_NEWS_DAYS일 안의 기사만, 시작 시각에 가까운 순으로 최대 5개."""
    if not settings.ENABLE_PORTFOLIO_ENTRY_NEWS or not position.available or position.started_at is None:
        return []
    start = position.started_at.astimezone(KST)
    half = ENTRY_NEWS_DAYS // 2
    try:
        # A window centred half-way back covers [start - 4 days, start] and never reaches later days.
        rows = await get_news_by_symbol_around(symbol_code, start.date() - timedelta(days=half), days=half, limit=20)
    except Exception:
        return []
    news = []
    for row in rows if isinstance(rows, list) else []:
        stamp = _article_time(row.get("date")) if isinstance(row, dict) else None
        # A date without a time could be later that day, so only earlier days count then.
        if stamp is None or stamp > start or (len(str(row.get("date")).strip()) == 10 and stamp.date() >= start.date()):
            continue
        try:
            news.append(RecentNews(title=str(row.get("title") or "").strip(), summary=row.get("summary") or "",
                                   source=row.get("source"), date=row.get("date"), url=row.get("url")))
        except ValidationError:
            continue
    return news[:5]


async def build_portfolio_facts(snapshot: PortfolioFactsSnapshot) -> PortfolioBriefFacts:
    # One daily-chart read serves both the entry context and the current trend.
    position = snapshot.replay.position
    (price, change_rate), news, rows, entry_news = await asyncio.gather(
        _current_quote(snapshot.symbol_code), _recent_news(snapshot.symbol_code),
        load_daily_rows(snapshot.symbol_code), _entry_news(snapshot.symbol_code, position),
    )
    entry = unavailable(position) if rows is None else calculate_entry_price_context(position, rows)
    limitations = [] if entry_news else ["historical_news_unavailable"]
    if not entry.available:
        limitations.append("historical_price_unavailable")
    elif entry.return_20_sessions is None:
        limitations.append("entry_price_history_partial")
    if not snapshot.replay.position.available:
        limitations.append(snapshot.replay.position.reason)
    elif snapshot.replay.position.opening_order_id is None:
        limitations.append("opening_trade_ambiguous")
    if price is None:
        limitations.append("current_price_unavailable")
    if not news:
        # Existing news service uses [] for both an empty feed and fetch failures.
        limitations.append("news_unavailable")
    if snapshot.security_name is None:
        limitations.append("security_name_unavailable")
    pnl = rate = None
    if snapshot.avg_price > 0 and price is not None:
        pnl = (price - snapshot.avg_price) * snapshot.quantity
        rate = (price - snapshot.avg_price) / snapshot.avg_price * 100
    if snapshot.avg_price <= 0:
        limitations.append("invalid_average_price")
    return PortfolioBriefFacts(
        account_id=snapshot.account_id, symbol_code=snapshot.symbol_code,
        security_name=snapshot.security_name,
        holding=HoldingFacts(quantity=snapshot.quantity, avg_price=snapshot.avg_price,
                             current_price=price, unrealized_pnl=pnl, return_rate=rate),
        position=snapshot.replay.position, trades=list(snapshot.replay.trades),
        recent_news=news, price_context=PriceContext(current_price=price), entry_price_context=entry,
        recent_trend=calculate_recent_trend(rows, change_rate), entry_news=entry_news, limitations=limitations,
    )
