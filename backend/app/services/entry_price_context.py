"""현재 포지션 시작일 이전의 완료 일봉만 사용하는 객관적 가격 문맥."""
from datetime import date, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx

from app.schemas.portfolio_brief import EntryPriceContext, PositionFacts
from app.services.chart_history import ChartUnavailable, get_chart_history

KST = timezone(timedelta(hours=9))


def unavailable(position: PositionFacts) -> EntryPriceContext:
    if not position.available:
        return EntryPriceContext(available=False, reason="position_unavailable")
    return EntryPriceContext(available=False, reason="historical_price_unavailable",
                             anchor_at=position.started_at, opening_price=position.opening_price)


def _price(value) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("Invalid daily price")
    result = Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError("Invalid daily price")
    return result


def calculate_entry_price_context(position: PositionFacts, rows: list[dict]) -> EntryPriceContext:
    empty = unavailable(position)
    if not position.available or position.started_at.utcoffset() is None:
        return empty
    cutoff = position.started_at.astimezone(KST).date()
    if not isinstance(rows, list):
        return empty
    completed = {}
    try:
        for row in rows:
            day = date.fromisoformat(row["date"])
            # Filter before inspecting prices: even extreme future/start-day bars have no influence.
            if day >= cutoff:
                continue
            prices = tuple(_price(row[key]) for key in ("open", "high", "low", "close"))
            op, high, low, close = prices
            if not low <= min(op, close) <= max(op, close) <= high:
                raise ValueError("Inconsistent daily prices")
            if day in completed and completed[day] != prices:
                raise ValueError("Conflicting daily sessions")
            completed[day] = prices
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return empty
    sessions = sorted(completed)
    if not sessions:
        return empty
    values = [completed[day] for day in sessions]
    prior = values[-1][3]
    recent = values[-20:]
    def change(base):
        return (prior / base - 1) * 100 if base > 0 else None
    opening = position.opening_price
    return EntryPriceContext(
        available=True, anchor_at=position.started_at, opening_price=opening,
        prior_close_date=sessions[-1], prior_close=prior,
        return_5_sessions=change(values[-6][3]) if len(values) >= 6 else None,
        return_20_sessions=change(values[-21][3]) if len(values) >= 21 else None,
        high_20_sessions=max(v[1] for v in recent), low_20_sessions=min(v[2] for v in recent),
        range_sessions=len(recent),
        opening_vs_prior_close_pct=(opening / prior - 1) * 100 if opening is not None else None,
    )


async def load_entry_price_context(symbol: str, position: PositionFacts) -> EntryPriceContext:
    if not position.available or position.started_at.utcoffset() is None:
        return unavailable(position)
    try:
        # Existing KIS chart path: recent calendar year, 1d OHLC, date-range paging and 60s cache.
        history = await get_chart_history(symbol, "Y")
    except (ChartUnavailable, httpx.HTTPError, ValueError, KeyError):
        return unavailable(position)
    if not isinstance(history, dict) or history.get("resolution") != "1d":
        return unavailable(position)
    return calculate_entry_price_context(position, history.get("rows"))
