"""현재 포지션 시작일 이전의 완료 일봉만 사용하는 객관적 가격 문맥."""
from datetime import date, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx

from app.schemas.portfolio_brief import EntryPriceContext, PositionFacts, RecentTrend
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
    average = (sum(v[3] for v in recent) / len(recent)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return EntryPriceContext(
        available=True, anchor_at=position.started_at, opening_price=opening,
        prior_close_date=sessions[-1], prior_close=prior,
        return_5_sessions=change(values[-6][3]) if len(values) >= 6 else None,
        return_20_sessions=change(values[-21][3]) if len(values) >= 21 else None,
        high_20_sessions=max(v[1] for v in recent), low_20_sessions=min(v[2] for v in recent),
        average_close_20_sessions=average,
        range_sessions=len(recent),
        opening_vs_prior_close_pct=(opening / prior - 1) * 100 if opening is not None else None,
    )


def calculate_recent_trend(rows, change_rate_today: Decimal | None) -> RecentTrend | None:
    """최근 일봉 종가 기준 5/20거래세션 수익률. 일봉이 없거나 이상하면 오늘 등락률만 남긴다."""
    closes = {}
    try:
        for row in rows if isinstance(rows, list) else []:
            closes[date.fromisoformat(row["date"])] = _price(row["close"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        closes = {}
    values = [closes[day] for day in sorted(closes)]
    def change(sessions):
        return (values[-1] / values[-sessions - 1] - 1) * 100 if len(values) > sessions else None
    if change_rate_today is None and not values:
        return None
    return RecentTrend(change_rate_today=change_rate_today, last_session_date=max(closes) if closes else None,
                       return_5_sessions=change(5), return_20_sessions=change(20))


async def load_daily_rows(symbol: str):
    try:
        # Existing KIS chart path: recent calendar year, 1d OHLC, date-range paging and 60s cache.
        history = await get_chart_history(symbol, "Y")
    except (ChartUnavailable, httpx.HTTPError, ValueError, KeyError):
        return None
    if not isinstance(history, dict) or history.get("resolution") != "1d":
        return None
    return history.get("rows")


async def load_entry_price_context(symbol: str, position: PositionFacts) -> EntryPriceContext:
    if not position.available or position.started_at.utcoffset() is None:
        return unavailable(position)
    rows = await load_daily_rows(symbol)
    return unavailable(position) if rows is None else calculate_entry_price_context(position, rows)
