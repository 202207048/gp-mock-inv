"""Synthetic completed sessions only: no real database, market data or AI calls."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.schemas.portfolio_brief import EntryPriceContext, PortfolioBriefFacts, PortfolioBriefResponse, PositionFacts
from app.services import entry_price_context as entry
from app.services import portfolio_brief_service as facts_service
from app.services.portfolio_brief_service import PortfolioFactsSnapshot
from app.services.position_replay import PositionReplayResult
from app.services import portfolio_brief_client as bridge
from tests.test_portfolio_brief_integration import facts, wire, ai_schema, RealAsyncClient

START = datetime(2026, 10, 21, 1, tzinfo=timezone.utc)


def position(**changes):
    data = dict(available=True, started_at=START, opening_order_id=1,
                opening_price=Decimal("125.25"), buy_count=1, sell_count=0)
    data.update(changes)
    return PositionFacts(**data)


def bars(count=25, end=date(2026, 10, 20)):
    days = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return [dict(date=day.isoformat(), open=str(100+i), close=str(100+i),
                 high=str(105+i), low=str(95+i)) for i, day in enumerate(sorted(days))]


class CalculationTests(unittest.TestCase):
    def test_prior_returns_range_and_opening_use_exact_decimal_session_offsets(self):
        result = entry.calculate_entry_price_context(position(), bars())
        self.assertTrue(result.available)
        self.assertEqual(result.anchor_at, START)
        self.assertEqual(result.prior_close_date, date(2026, 10, 20))
        self.assertEqual(result.prior_close, Decimal("124"))
        self.assertEqual(result.return_5_sessions, (Decimal(124)/Decimal(119)-1)*100)
        self.assertEqual(result.return_20_sessions, (Decimal(124)/Decimal(104)-1)*100)
        self.assertEqual((result.high_20_sessions, result.low_20_sessions, result.range_sessions),
                         (Decimal(129), Decimal(100), 20))
        self.assertEqual(result.opening_vs_prior_close_pct, (Decimal("125.25")/Decimal(124)-1)*100)

    def test_start_day_and_future_extreme_or_invalid_prices_have_zero_influence(self):
        rows = bars()
        expected = entry.calculate_entry_price_context(position(), rows)
        rows += [dict(date="2026-10-21", open="999999", close="999999", high="999999", low="1"),
                 dict(date="2026-10-22", open="NaN", close="0", high="Infinity", low="-1")]
        self.assertEqual(entry.calculate_entry_price_context(position(), rows), expected)

    def test_timezone_uses_kst_date_not_utc(self):
        pos = position(started_at=datetime(2026, 10, 20, 18, tzinfo=timezone.utc))
        result = entry.calculate_entry_price_context(pos, bars()+[dict(date="2026-10-21")])
        self.assertTrue(result.available)
        self.assertEqual(result.prior_close_date, date(2026, 10, 20))

    def test_weekend_and_missing_holiday_use_actual_last_bar(self):
        for anchor, end in [(datetime(2026,10,24,1,tzinfo=timezone.utc), date(2026,10,23)),
                            (START, date(2026,10,19))]:
            with self.subTest(anchor=anchor):
                rows = bars(end=end)
                result = entry.calculate_entry_price_context(position(started_at=anchor), rows)
                self.assertEqual(result.prior_close_date, end)
                self.assertEqual(result.return_5_sessions, (Decimal(124)/119-1)*100)

    def test_short_history_returns_only_supported_facts(self):
        for count in (1, 5, 6, 20, 21):
            with self.subTest(count=count):
                result = entry.calculate_entry_price_context(position(), bars(count))
                self.assertTrue(result.available)
                self.assertEqual(result.range_sessions, min(count,20))
                self.assertEqual(result.return_5_sessions is not None, count >= 6)
                self.assertEqual(result.return_20_sessions is not None, count >= 21)
                self.assertEqual(result.high_20_sessions, Decimal(104+count))

    def test_unknown_opening_does_not_discard_history_or_invent_average(self):
        result = entry.calculate_entry_price_context(position(opening_order_id=None, opening_price=None), bars())
        self.assertTrue(result.available)
        self.assertIsNone(result.opening_price)
        self.assertIsNone(result.opening_vs_prior_close_pct)
        self.assertIsNotNone(result.return_20_sessions)

    def test_unknown_position_has_no_anchor_or_prices(self):
        result = entry.calculate_entry_price_context(
            PositionFacts(available=False, reason="legacy_execution_time_unavailable"), bars())
        self.assertFalse(result.available)
        self.assertEqual(result.reason, "position_unavailable")
        self.assertIsNone(result.anchor_at)
        self.assertIsNone(result.opening_price)

    def test_empty_out_of_range_and_naive_anchor_never_guess(self):
        for rows in ([], bars(end=date(2026,10,30)), None):
            # All rows of the out-of-range fixture must be on/after the anchor.
            if isinstance(rows,list) and rows:
                rows = [row for row in rows if row["date"] >= "2026-10-21"]
            self.assertFalse(entry.calculate_entry_price_context(position(), rows).available)
        naive = position(started_at=START.replace(tzinfo=None))
        self.assertFalse(entry.calculate_entry_price_context(naive, bars()).available)

    def test_invalid_zero_negative_nonfinite_and_inconsistent_daily_bars_are_unavailable(self):
        for value in ("0", "-1", "NaN", "Infinity", True, None):
            rows = bars()
            rows[-1]["close"] = value
            self.assertFalse(entry.calculate_entry_price_context(position(), rows).available)
        rows = bars()
        rows[-1]["high"] = "1"
        self.assertFalse(entry.calculate_entry_price_context(position(), rows).available)

    def test_sort_dedup_and_conflicting_dates(self):
        rows = bars()
        expected = entry.calculate_entry_price_context(position(), rows)
        self.assertEqual(entry.calculate_entry_price_context(position(), rows[::-1]+[rows[-1]]), expected)
        rows.append({**rows[-1], "close": "123"})
        self.assertFalse(entry.calculate_entry_price_context(position(), rows).available)

    def test_contract_rejects_future_dates_inferred_unavailable_and_nonfinite_values(self):
        valid = entry.calculate_entry_price_context(position(), bars()).model_dump()
        for change in ({"prior_close_date": START.date()}, {"prior_close": "NaN"},
                       {"range_sessions": 0}, {"range_sessions": 5}, {"range_sessions": 19}, {"low_20_sessions": "999999"},
                       {"available": False, "reason": "historical_price_unavailable"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                EntryPriceContext(**(valid | change))


class LoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuses_existing_year_daily_path_and_skips_unknown_positions(self):
        with patch.object(entry, "get_chart_history", AsyncMock(return_value={"resolution":"1d","rows":bars()})) as load:
            result = await entry.load_entry_price_context("SYN001", position())
            self.assertTrue(result.available)
            load.assert_awaited_once_with("SYN001", "Y")
            load.reset_mock()
            await entry.load_entry_price_context("SYN001", PositionFacts(available=False, reason="invalid_trade_history"))
            load.assert_not_awaited()

    async def test_expected_provider_errors_degrade_but_programming_errors_propagate(self):
        for error in (entry.ChartUnavailable("private-marker"), httpx.ReadTimeout("private-marker"),
                      ValueError("private-marker"), KeyError("access_token")):
            with patch.object(entry,"get_chart_history",AsyncMock(side_effect=error)):
                result = await entry.load_entry_price_context("SYN001",position())
            self.assertFalse(result.available)
            self.assertNotIn("private-marker", result.model_dump_json())
        with patch.object(entry,"get_chart_history",AsyncMock(side_effect=RuntimeError("programming-error"))):
            with self.assertRaises(RuntimeError):
                await entry.load_entry_price_context("SYN001",position())

    async def test_wrong_resolution_and_malformed_history_are_unavailable(self):
        for value in (None, [], {"resolution":"5m","rows":bars()}, {"resolution":"1d","rows":None}):
            with patch.object(entry,"get_chart_history",AsyncMock(return_value=value)):
                self.assertFalse((await entry.load_entry_price_context("SYN001",position())).available)

    async def test_fact_bundle_preserves_current_data_when_history_fails_and_flags_partial(self):
        snapshot = PortfolioFactsSnapshot(1,"SYN001","가상종목",10,Decimal("100.25"),PositionReplayResult(position()))
        for rows in (None, bars(5), bars()):
            loader = AsyncMock(side_effect=entry.ChartUnavailable("private-marker")) if rows is None else AsyncMock(return_value={"resolution":"1d","rows":rows})
            with patch.object(entry,"get_chart_history",loader),                  patch.object(facts_service,"get_current_price",AsyncMock(return_value={"current_price":"130"})),                  patch.object(facts_service,"get_news_by_symbol",AsyncMock(return_value=[])):
                result = await facts_service.build_portfolio_facts(snapshot)
            self.assertEqual(result.holding.current_price,Decimal("130"))
            self.assertEqual(result.holding.quantity,10)
            self.assertEqual(result.entry_price_context.available,rows is not None)
            self.assertEqual("historical_price_unavailable" in result.limitations,rows is None)
            self.assertEqual("entry_price_history_partial" in result.limitations,rows is not None and len(rows)<21)
            self.assertIn("historical_news_unavailable",result.limitations)
            self.assertIsNone(result.price_context.position_start_price)

    async def test_history_succeeds_independently_of_current_price_failure(self):
        snapshot=PortfolioFactsSnapshot(1,"SYN001",None,10,Decimal("100"),PositionReplayResult(position()))
        with patch.object(entry,"get_chart_history",AsyncMock(return_value={"resolution":"1d","rows":bars()})),              patch.object(facts_service,"get_current_price",AsyncMock(side_effect=httpx.ConnectError("offline"))),              patch.object(facts_service,"get_news_by_symbol",AsyncMock(return_value=[])):
            result=await facts_service.build_portfolio_facts(snapshot)
        self.assertIsNone(result.holding.current_price)
        self.assertTrue(result.entry_price_context.available)
        self.assertIn("current_price_unavailable",result.limitations)
        self.assertNotIn("historical_price_unavailable",result.limitations)


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    def bundle(self, available=True):
        bundle=facts()
        if available:
            # Use the same current-position anchor and opening fill across contracts.
            pos=bundle.position
            bundle.entry_price_context=entry.calculate_entry_price_context(pos,bars())
        return bundle

    async def test_available_projection_matches_independent_ai_schema_and_response(self):
        bundle=self.bundle()
        payload=bridge.project_portfolio_brief(bundle,"sol")
        self.assertEqual(payload["entry_price_context"],bundle.entry_price_context.model_dump(mode="json"))
        ai_schema().PortfolioBriefRequest.model_validate(payload)
        self.assertIsInstance(payload["entry_price_context"]["prior_close"],str)
        for name in ("account_id","user_id","price_context"):
            self.assertNotIn(name,payload)
        trace=uuid4()
        data=wire("sol",trace)
        data["entry_context"]=["시작 직전 완료 세션 가격만 확인됩니다."]
        result=bridge._parse_result(data,bundle,"sol",trace)
        exposed=PortfolioBriefResponse(symbol_code=bundle.symbol_code,security_name=bundle.security_name,**result.brief())
        self.assertEqual(exposed.entry_context,data["entry_context"])

    async def test_unavailable_projection_and_output_never_reconstruct(self):
        bundle=self.bundle(False)
        ai_schema().PortfolioBriefRequest.model_validate(bridge.project_portfolio_brief(bundle))
        trace=uuid4()
        data=wire("luna",trace)
        data["entry_context"]=["invented-entry"]
        with self.assertRaises(bridge.PortfolioBriefUnavailable):
            bridge._parse_result(data,bundle,"luna",trace)

    async def test_malformed_entry_output_is_502_through_real_mocked_transport(self):
        for value in ("wrong",["x"]*4,[" "],[123],None):
            trace=uuid4()
            data=wire("luna",trace)
            data["entry_context"]=value
            def factory(**options):
                return RealAsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,json=data)),**options)
            with patch.object(bridge.httpx,"AsyncClient",side_effect=factory):
                with self.assertRaises(bridge.PortfolioBriefUnavailable) as caught:
                    await bridge.request_portfolio_brief("http://ai.test",self.bundle(),profile="luna",trace_id=trace)
            self.assertEqual(caught.exception.status_code,502)
