"""외부 데이터 없이 체결 이력의 구간과 모호성을 검증한다."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from app.services.position_replay import replay_current_position

UTC = timezone.utc
START = datetime(2026, 10, 21, 1, tzinfo=UTC)


def trade(index, side="매수", quantity=10, **changes):
    values = dict(order_id=index, account_id=1, symbol_code="SYN001",
                  status="체결", order_type=side, quantity=quantity, price=Decimal("100.25"),
                  filled_at=START + timedelta(minutes=index))
    return SimpleNamespace(**(values | changes))


def replay(orders, quantity=10):
    return replay_current_position(1, "SYN001", quantity, orders)


class PositionReplayTests(unittest.TestCase):
    def assert_unavailable(self, result, reason):
        self.assertFalse(result.position.available)
        self.assertEqual(result.position.reason, reason)
        self.assertEqual(result.trades, ())
        for field in ["started_at", "opening_order_id", "opening_price", "buy_count", "sell_count"]:
            self.assertIsNone(getattr(result.position, field))

    def test_single_buy(self):
        order = trade(1)
        result = replay([order])
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.started_at, order.filled_at)
        self.assertEqual(result.position.opening_order_id, 1)
        self.assertEqual(result.position.opening_price, Decimal("100.25"))
        self.assertEqual((result.position.buy_count, result.position.sell_count), (1, 0))
        self.assertEqual([t.order_id for t in result.trades], [1])

    def test_additional_buy_keeps_first_start(self):
        result = replay([trade(2, quantity=5), trade(1)], 15)
        self.assertEqual(result.position.opening_order_id, 1)
        self.assertEqual(result.position.buy_count, 2)
        self.assertEqual([t.order_id for t in result.trades], [1, 2])

    def test_partial_sell_keeps_position(self):
        result = replay([trade(1), trade(2, "매도", 4)], 6)
        self.assertEqual(result.position.opening_order_id, 1)
        self.assertEqual((result.position.buy_count, result.position.sell_count), (1, 1))

    def test_full_sale_and_rebuy_starts_new_position(self):
        result = replay([trade(1), trade(2, "매도"), trade(3, quantity=5)], 5)
        self.assertEqual(result.position.opening_order_id, 3)
        self.assertEqual((result.position.buy_count, result.position.sell_count), (1, 0))
        self.assertEqual([t.order_id for t in result.trades], [3])

    def test_filled_time_wins_over_submission_time_and_order_id(self):
        delayed = trade(1, quantity=5, filled_at=START + timedelta(minutes=3),
                        created_at=START - timedelta(days=1))
        result = replay([delayed, trade(2, filled_at=START + timedelta(minutes=1)),
                         trade(3, "매도", filled_at=START + timedelta(minutes=2))], 5)
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.started_at, delayed.filled_at)
        self.assertEqual(result.position.opening_order_id, 1)
        self.assertEqual([t.order_id for t in result.trades], [1])

    def test_multiple_round_trips_keep_only_final_open_position(self):
        result = replay([trade(1), trade(2, "매도"), trade(3, quantity=3),
                         trade(4, "매도", 3), trade(5, quantity=8)], 8)
        self.assertEqual(result.position.opening_order_id, 5)
        self.assertEqual([t.order_id for t in result.trades], [5])

    def test_legacy_null_never_reads_created_at_even_in_closed_history(self):
        class Legacy(SimpleNamespace):
            @property
            def created_at(self):
                raise AssertionError("Submission time must never be read")
        legacy = Legacy(**vars(trade(1, filled_at=None)))
        self.assert_unavailable(replay([legacy, trade(2, "매도"), trade(3)]),
                                "legacy_execution_time_unavailable")

    def test_negative_running_quantity_is_invalid(self):
        self.assert_unavailable(replay([trade(1, "매도"), trade(2, quantity=20)]),
                                "invalid_trade_history")

    def test_portfolio_quantity_mismatch_and_missing_history(self):
        for orders in [[trade(1, quantity=9)], []]:
            with self.subTest(orders=orders):
                self.assert_unavailable(replay(orders), "portfolio_quantity_mismatch")

    def test_simultaneous_opening_buys_preserve_time_without_inventing_first_trade(self):
        rows = [trade(2, quantity=4, filled_at=START), trade(1, quantity=6, filled_at=START)]
        result = replay(rows)
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.started_at, START)
        self.assertIsNone(result.position.opening_order_id)
        self.assertIsNone(result.position.opening_price)
        self.assertEqual(result.position.buy_count, 2)
        self.assertEqual([t.order_id for t in result.trades], [1, 2])

    def test_simultaneous_additional_buys_preserve_known_opening(self):
        result = replay([trade(1), trade(2, quantity=2, filled_at=START + timedelta(minutes=2)),
                         trade(3, quantity=3, filled_at=START + timedelta(minutes=2))], 15)
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.opening_order_id, 1)
        self.assertEqual(result.position.buy_count, 3)

    def test_simultaneous_sells_can_close_without_ambiguity(self):
        result = replay([trade(1), trade(2, "매도", 4, filled_at=START + timedelta(minutes=2)),
                         trade(3, "매도", 6, filled_at=START + timedelta(minutes=2)),
                         trade(4, quantity=5)], 5)
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.opening_order_id, 4)
        self.assertEqual([t.order_id for t in result.trades], [4])

    def test_simultaneous_mixed_sides_keep_position_when_no_order_can_close_it(self):
        opening = trade(1)
        stamp = START + timedelta(minutes=2)
        for buy_id, sell_id in [(2, 3), (3, 2)]:
            with self.subTest(buy_id=buy_id, sell_id=sell_id):
                batch = [trade(buy_id, quantity=2, filled_at=stamp),
                         trade(sell_id, "매도", 1, filled_at=stamp)]
                expected = None
                for items in [batch, list(reversed(batch))]:
                    result = replay([opening, *items], 11)
                    self.assertTrue(result.position.available)
                    self.assertIsNone(result.position.reason)
                    self.assertEqual(result.position.started_at, opening.filled_at)
                    self.assertEqual(result.position.opening_order_id, opening.order_id)
                    self.assertEqual(result.position.opening_price, opening.price)
                    self.assertEqual((result.position.buy_count, result.position.sell_count), (2, 1))
                    self.assertEqual([t.order_id for t in result.trades], [1, 2, 3])
                    actual = {t.order_id: (t.order_type, t.quantity, t.price, t.filled_at)
                              for t in result.trades}
                    self.assertEqual(actual, {t.order_id: (t.order_type, t.quantity, t.price, t.filled_at)
                                              for t in [opening, *batch]})
                    if expected is not None:
                        self.assertEqual(result, expected)
                    expected = result

    def test_simultaneous_mixed_sides_use_total_sells_for_ambiguity(self):
        for before_quantity, sell_quantities in [(1, [1]), (2, [1, 1]), (1, [2])]:
            with self.subTest(before=before_quantity, sells=sell_quantities):
                total_sell = sum(sell_quantities)
                stamp = START + timedelta(minutes=2)
                batch = [trade(2, quantity=total_sell + 1, filled_at=stamp)]
                batch += [trade(i, "매도", qty, filled_at=stamp)
                          for i, qty in enumerate(sell_quantities, 3)]
                for items in [batch, list(reversed(batch))]:
                    result = replay([trade(1, quantity=before_quantity), *items], before_quantity + 1)
                    self.assert_unavailable(result, "ambiguous_execution_order")

    def test_safe_mixed_group_does_not_bypass_portfolio_mismatch(self):
        stamp = START + timedelta(minutes=2)
        result = replay([trade(1), trade(2, quantity=2, filled_at=stamp),
                         trade(3, "매도", 1, filled_at=stamp)], 10)
        self.assert_unavailable(result, "portfolio_quantity_mismatch")

    def test_simultaneous_mixed_sides_are_not_ordered_by_id(self):
        rows = [trade(1), trade(2, "매도", filled_at=START + timedelta(minutes=2)),
                trade(3, filled_at=START + timedelta(minutes=2))]
        for items in [rows, list(reversed(rows))]:
            self.assert_unavailable(replay(items), "ambiguous_execution_order")

    def test_unfilled_other_accounts_and_other_symbols_are_excluded(self):
        rows = [trade(1)] + [trade(i, status=status, filled_at=None, quantity=-10)
                              for i, status in enumerate(["대기", "취소", "거부"], 2)]
        rows += [trade(5, account_id=2, filled_at=None), trade(6, symbol_code="OTHER", filled_at=None)]
        result = replay(rows)
        self.assertTrue(result.position.available)
        self.assertEqual([t.order_id for t in result.trades], [1])

    def test_invalid_completed_rows_are_not_silently_accepted(self):
        for change in [{"quantity": 0}, {"quantity": -1}, {"quantity": True}, {"quantity": 1.5},
                       {"order_type": "unknown"}, {"order_id": 0}, {"price": 0},
                       {"price": "NaN"}, {"price": "Infinity"}, {"filled_at": "2026-10-21"}]:
            with self.subTest(change=change):
                self.assert_unavailable(replay([trade(1, **change)]), "invalid_trade_history")

    def test_duplicate_order_ids_are_invalid(self):
        self.assert_unavailable(replay([trade(1, quantity=5), trade(1, quantity=5)]),
                                "invalid_trade_history")

    def test_utc_offsets_group_the_same_instant(self):
        kst = timezone(timedelta(hours=9))
        result = replay([trade(1, quantity=4, filled_at=START),
                         trade(2, quantity=6, filled_at=START.astimezone(kst))])
        self.assertTrue(result.position.available)
        self.assertEqual(result.position.started_at, START)
        self.assertIsNone(result.position.opening_order_id)

    def test_naive_stored_utc_is_normalized_without_using_submission_time(self):
        result = replay([trade(1, filled_at=START.replace(tzinfo=None))])
        self.assertEqual(result.position.started_at, START)

    def test_invalid_portfolio_quantity_is_unavailable(self):
        for quantity in [0, -1, True, 1.5]:
            self.assert_unavailable(replay([trade(1)], quantity), "invalid_trade_history")
