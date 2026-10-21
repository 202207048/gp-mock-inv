"""체결 시각만 사용하는 순수 포지션 재생. DB/시세/뉴스를 조회하지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import groupby
from typing import Iterable, TYPE_CHECKING

from app.schemas.portfolio_brief import PositionFacts, PositionReason, PositionTrade

if TYPE_CHECKING:
    from app.models.order import Order


@dataclass(frozen=True)
class PositionReplayResult:
    position: PositionFacts
    trades: tuple[PositionTrade, ...] = ()


def _unavailable(reason: PositionReason) -> PositionReplayResult:
    return PositionReplayResult(PositionFacts(available=False, reason=reason))


def replay_current_position(
    account_id: int, symbol_code: str, portfolio_quantity: int, orders: Iterable[Order],
) -> PositionReplayResult:
    if type(portfolio_quantity) is not int or portfolio_quantity <= 0:
        return _unavailable("invalid_trade_history")
    filled = [order for order in orders
              if order.account_id == account_id and order.symbol_code == symbol_code
              and order.status == "체결"]
    # A legacy row may belong to any position; never use created_at as a substitute.
    if any(order.filled_at is None for order in filled):
        return _unavailable("legacy_execution_time_unavailable")
    trades = []
    seen = set()
    for order in filled:
        if (type(order.order_id) is not int or order.order_id <= 0 or order.order_id in seen
                or type(order.quantity) is not int or order.quantity <= 0
                or order.order_type not in {"매수", "매도"}
                or not isinstance(order.filled_at, datetime)):
            return _unavailable("invalid_trade_history")
        try:
            price = Decimal(str(order.price))
        except (InvalidOperation, ValueError, TypeError):
            return _unavailable("invalid_trade_history")
        if not price.is_finite() or price <= 0:
            return _unavailable("invalid_trade_history")
        stamp = order.filled_at
        # Persisted naive UTC values (e.g. SQLite) represent the same UTC instant.
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp.astimezone(timezone.utc)
        trades.append(PositionTrade(order_id=order.order_id, order_type=order.order_type,
                                    quantity=order.quantity, price=price, filled_at=stamp))
        seen.add(order.order_id)
    trades.sort(key=lambda trade: (trade.filled_at, trade.order_id))
    quantity = 0
    started_at = opening_id = opening_price = None
    buys = sells = 0
    active = []
    for stamp, group in groupby(trades, key=lambda trade: trade.filled_at):
        batch = list(group)
        if len({trade.order_type for trade in batch}) != 1:
            before_quantity = quantity
            buy_trades = [trade for trade in batch if trade.order_type == "매수"]
            sell_trades = [trade for trade in batch if trade.order_type == "매도"]
            total_buy_quantity = sum(trade.quantity for trade in buy_trades)
            total_sell_quantity = sum(trade.quantity for trade in sell_trades)
            if before_quantity <= total_sell_quantity:
                return _unavailable("ambiguous_execution_order")
            # Even all sells first cannot close this position; apply only the net.
            quantity = before_quantity + total_buy_quantity - total_sell_quantity
            buys += len(buy_trades)
            sells += len(sell_trades)
            active.extend(batch)
            continue
        total = sum(trade.quantity for trade in batch)
        if batch[0].order_type == "매수":
            if quantity == 0:
                started_at = stamp
                # ID ordering cannot identify which simultaneous buy opened the position.
                opening_id = batch[0].order_id if len(batch) == 1 else None
                opening_price = batch[0].price if len(batch) == 1 else None
                buys = sells = 0
                active = []
            quantity += total
            buys += len(batch)
        else:
            quantity -= total
            if quantity < 0:
                return _unavailable("invalid_trade_history")
            sells += len(batch)
        active.extend(batch)
        if quantity == 0:
            started_at = opening_id = opening_price = None
            buys = sells = 0
            active = []
    if quantity != portfolio_quantity:
        return _unavailable("portfolio_quantity_mismatch")
    if started_at is None:
        return _unavailable("invalid_trade_history")
    return PositionReplayResult(PositionFacts(
        available=True, started_at=started_at, opening_order_id=opening_id,
        opening_price=opening_price, buy_count=buys, sell_count=sells,
    ), tuple(active))
