"""Derive shareable sale price returns from verified execution history."""
from collections import defaultdict
from datetime import timezone
from decimal import Decimal, ROUND_HALF_UP


def sale_returns(orders):
    groups = defaultdict(list)
    for order in orders:
        if order.status == '체결':
            groups[(order.account_id, order.symbol_code)].append(order)
    result = {}
    for history in groups.values():
        # Unknown execution times cannot safely establish historical cost basis.
        if any(order.filled_at is None for order in history):
            continue
        def stamp(order):
            value = order.filled_at
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        history.sort(key=lambda order: (stamp(order), order.order_id))
        if len({stamp(order) for order in history}) != len(history):
            continue
        quantity = 0
        average = Decimal(0)
        for order in history:
            price = Decimal(str(order.price))
            if order.quantity <= 0 or not price.is_finite() or price <= 0:
                break
            if order.order_type == '매수':
                average = ((average * quantity + price * order.quantity) / (quantity + order.quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                quantity += order.quantity
            elif order.order_type == '매도':
                if quantity < order.quantity or average <= 0:
                    break
                result[order.order_id] = {
                    'profit_loss': str((price - average) * order.quantity),
                    'profit_rate': str((price - average) / average * 100),
                }
                quantity -= order.quantity
                if quantity == 0:
                    average = Decimal(0)
            else:
                break
    return result
