from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from app.services.trade_share import sale_returns


def trade(oid, side, price, qty, **extra):
    return SimpleNamespace(**dict(dict(order_id=oid, account_id=1, symbol_code='005930',
        status='체결', filled_at=datetime(2026, 10, 28, tzinfo=timezone.utc) + timedelta(minutes=oid),
        order_type=side, price=Decimal(price), quantity=qty), **extra))


class TradeShareTests(unittest.TestCase):
    def test_saved_basis_overrides_incomplete_or_reconstructed_history(self):
        sell = trade(2, '매도', '239500', 1, sale_cost_basis=Decimal('251714'))
        for history in ([sell], [trade(1, '매수', '100', 7, filled_at=None), sell],
                        [trade(1, '매수', '100', 7), sell]):
            result = sale_returns(history)
            self.assertEqual(Decimal(result[2]['profit_loss']), Decimal('-12214'))

    def test_weighted_cost_partial_sales_and_new_position(self):
        history = [trade(1, '매수', '10000', 10), trade(2, '매수', '20000', 10),
                   trade(3, '매도', '16500', 10), trade(4, '매도', '12000', 10),
                   trade(5, '매수', '1000', 10), trade(6, '매도', '1000', 10)]
        result = sale_returns(reversed(history))
        self.assertEqual(Decimal(result[3]['profit_loss']), Decimal('15000'))
        self.assertEqual(Decimal(result[3]['profit_rate']), Decimal('10'))
        self.assertEqual(Decimal(result[4]['profit_loss']), Decimal('-30000'))
        self.assertEqual(Decimal(result[6]['profit_rate']), Decimal('0'))

    def test_legacy_ambiguous_or_missing_cost_is_unavailable(self):
        buy = trade(1, '매수', '100', 10)
        sell = trade(2, '매도', '110', 10)
        for history in ([trade(1, '매수', '100', 10, filled_at=None), sell],
                        [buy, trade(2, '매도', '110', 10, filled_at=buy.filled_at)], [sell],
                        [buy, trade(2, '매도', '110', 20)]):
            self.assertEqual(sale_returns(history), {})

    def test_account_symbol_and_unfilled_orders_are_isolated(self):
        result = sale_returns([trade(1, '매수', '100', 10), trade(2, '매수', '1000', 10, account_id=2),
                              trade(3, '매수', '1000', 10, symbol_code='000660'),
                              trade(4, '매수', '500', 100, status='대기', filled_at=None),
                              trade(5, '매도', '110', 10)])
        self.assertEqual(Decimal(result[5]['profit_loss']), Decimal('100'))
