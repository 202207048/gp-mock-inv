"""인증과 계좌 범위는 실제 router/격리 SQLite, 시세·뉴스는 fake로 검증한다."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Account, ItemMaster, Order, Portfolio, User
from app.routers import ai as routes
from app.schemas.portfolio_brief import PortfolioBriefFacts, PositionFacts
from app.services import portfolio_brief_service as service
from app.utils.deps import get_current_user

UTC = timezone.utc
START = datetime(2026, 10, 21, 1, tzinfo=UTC)
NEWS = dict(title="가온기술, 신규 제품 공개", summary="기업용 제품을 공개했다.",
            source="가상 매체", date="2026.10.21 10:00", url="https://example.invalid/news/one")


class PortfolioFactsApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([User(user_id=1, login_id="one", passwd="unused", user_name="one", email="one@example.test"),
                        User(user_id=2, login_id="two", passwd="unused", user_name="two", email="two@example.test"),
                        Account(account_id=1, user_id=1, balance=10000, withdrawable_cash=10000),
                        Account(account_id=2, user_id=2, balance=10000, withdrawable_cash=10000),
                        ItemMaster(symbol_code="SYN001", name="가온기술", market_type="국내주식"),
                        ItemMaster(symbol_code="OTHER", name="다른종목", market_type="국내주식")])
            db.add_all([Portfolio(account_id=1, symbol_code="SYN001", hold_quantity=10, avg_price=Decimal("100.25")),
                        Portfolio(account_id=2, symbol_code="SYN001", hold_quantity=999, avg_price=1),
                        Portfolio(account_id=1, symbol_code="OTHER", hold_quantity=3, avg_price=2)])
            self.add_order(db, 1, quantity=10)
            self.add_order(db, 2, account_id=2, quantity=999, filled_at=None)
            self.add_order(db, 3, symbol_code="OTHER", quantity=3, filled_at=None)
            db.commit()
        self.app = FastAPI()
        self.app.include_router(routes.router, prefix="/api/ai")
        def session():
            with self.Session() as db:
                yield db
        self.app.dependency_overrides[get_db] = session
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.quote = AsyncMock(return_value={"current_price": 120.5})
        self.news = AsyncMock(return_value=[dict(NEWS)])
        for name, fake in [("get_current_price", self.quote), ("get_news_by_symbol", self.news)]:
            patcher = patch.object(service, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_order(self, db, order_id, **changes):
        row = Order(**(dict(order_id=order_id, account_id=1, symbol_code="SYN001", status="체결",
                            order_type="매수", price_type="시장가", quantity=10, price=Decimal("100.25"),
                            created_at=START - timedelta(days=10),
                            filled_at=START + timedelta(minutes=order_id)) | changes))
        db.add(row)
        return row

    def get(self, account_id=1, symbol="SYN001"):
        return self.client.get(f"/api/ai/portfolio-brief/facts/{symbol}?account_id={account_id}")

    def test_owned_holding_with_exact_pnl_news_and_typed_contract(self):
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        PortfolioBriefFacts.model_validate(data)
        self.assertEqual((data["account_id"], data["symbol_code"], data["security_name"]),
                         (1, "SYN001", "가온기술"))
        self.assertEqual(data["holding"]["quantity"], 10)
        self.assertEqual(Decimal(data["holding"]["avg_price"]), Decimal("100.25"))
        self.assertEqual(Decimal(data["holding"]["unrealized_pnl"]), Decimal("202.50"))
        self.assertEqual(Decimal(data["holding"]["return_rate"]),
                         (Decimal("120.5") - Decimal("100.25")) / Decimal("100.25") * 100)
        self.assertTrue(data["position"]["available"])
        self.assertEqual([t["order_id"] for t in data["trades"]], [1])
        self.assertEqual(data["recent_news"], [NEWS])
        self.quote.assert_awaited_once_with("SYN001")
        self.news.assert_awaited_once_with("SYN001", 5)
        self.assertEqual(data["limitations"], ["historical_news_unavailable", "historical_price_unavailable"])

    def test_other_user_and_nonexistent_account_are_hidden_before_external_calls(self):
        for account_id in [2, 999]:
            self.assertEqual(self.get(account_id=account_id).status_code, 404)
        self.quote.assert_not_awaited()
        self.news.assert_not_awaited()

    def test_unheld_zero_and_negative_holding_are_not_brief_targets(self):
        self.assertEqual(self.get(symbol="MISSING").status_code, 404)
        for quantity in [0, -1]:
            with self.Session() as db:
                db.query(Portfolio).filter_by(account_id=1, symbol_code="SYN001").one().hold_quantity = quantity
                db.commit()
            self.assertEqual(self.get().status_code, 404)
        self.quote.assert_not_awaited()

    def test_authentication_cannot_be_bypassed(self):
        self.app.dependency_overrides.pop(get_current_user)
        self.assertIn(self.get().status_code, [401, 403])
        self.quote.assert_not_awaited()
        self.news.assert_not_awaited()

    def test_invalid_account_input_is_rejected(self):
        for value in [0, -1, "not-an-id"]:
            self.assertEqual(self.get(account_id=value).status_code, 422)
        self.quote.assert_not_awaited()

    def test_quote_failure_keeps_db_facts_and_news_without_exposing_error(self):
        self.quote.side_effect = RuntimeError("unit-test-private-marker")
        response = self.get()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["position"]["available"])
        self.assertEqual(data["holding"]["quantity"], 10)
        for field in ["current_price", "unrealized_pnl", "return_rate"]:
            self.assertIsNone(data["holding"][field])
        self.assertIn("current_price_unavailable", data["limitations"])
        self.assertEqual(data["recent_news"], [NEWS])
        self.assertNotIn("unit-test-private-marker", response.text)

    def test_missing_zero_negative_and_nonfinite_quote_are_not_fake_prices(self):
        for quote in [{}, {"current_price": 0}, {"current_price": -1}, {"current_price": "NaN"},
                      {"current_price": "Infinity"}, {"current_price": True}, None]:
            with self.subTest(quote=quote):
                self.quote.return_value = quote
                response = self.get()
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.json()["holding"]["current_price"])
                self.assertIn("current_price_unavailable", response.json()["limitations"])

    def test_news_empty_exception_and_invalid_rows_degrade_safely(self):
        for rows in [[], None, [{"title": " "}], [{"title": "title", "source": {"bad": "type"}}]]:
            self.news.return_value = rows
            response = self.get()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["recent_news"], [])
            self.assertIn("news_unavailable", response.json()["limitations"])
        self.news.side_effect = RuntimeError("unit-test-private-marker")
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertIn("news_unavailable", response.json()["limitations"])
        self.assertNotIn("unit-test-private-marker", response.text)

    def test_legacy_position_unavailable_keeps_holding_price_and_news(self):
        with self.Session() as db:
            db.get(Order, 1).filled_at = None
            db.commit()
        data = self.get().json()
        self.assertFalse(data["position"]["available"])
        self.assertEqual(data["position"]["reason"], "legacy_execution_time_unavailable")
        self.assertIsNone(data["position"]["started_at"])
        self.assertEqual(data["trades"], [])
        self.assertEqual(Decimal(data["holding"]["current_price"]), Decimal("120.5"))
        self.assertEqual(data["recent_news"], [NEWS])
        self.assertIn("legacy_execution_time_unavailable", data["limitations"])

    def test_mismatch_and_ambiguous_timeline_are_not_presented_as_facts(self):
        with self.Session() as db:
            db.get(Order, 1).quantity = 9
            db.commit()
        data = self.get().json()
        self.assertEqual(data["position"]["reason"], "portfolio_quantity_mismatch")
        self.assertEqual(data["trades"], [])
        with self.Session() as db:
            db.get(Order, 1).quantity = 10
            self.add_order(db, 4, order_type="매도", filled_at=START + timedelta(minutes=4))
            self.add_order(db, 5, filled_at=START + timedelta(minutes=4))
            db.commit()
        data = self.get().json()
        self.assertEqual(data["position"]["reason"], "ambiguous_execution_order")
        self.assertEqual(data["trades"], [])

    def test_simultaneous_opening_buys_keep_time_without_false_precision(self):
        with self.Session() as db:
            db.get(Order, 1).quantity = 6
            self.add_order(db, 4, quantity=4, filled_at=START + timedelta(minutes=1))
            db.commit()
        data = self.get().json()
        self.assertTrue(data["position"]["available"])
        self.assertIsNone(data["position"]["opening_order_id"])
        self.assertIsNone(data["position"]["opening_price"])
        self.assertEqual(data["position"]["buy_count"], 2)
        self.assertIn("opening_trade_ambiguous", data["limitations"])

    def test_completed_round_trip_is_excluded_from_current_trades(self):
        with self.Session() as db:
            self.add_order(db, 4, order_type="매도")
            self.add_order(db, 5)
            db.commit()
        data = self.get().json()
        self.assertEqual(data["position"]["opening_order_id"], 5)
        self.assertEqual([t["order_id"] for t in data["trades"]], [5])

    def test_nonfilled_orders_do_not_make_timeline_legacy(self):
        with self.Session() as db:
            for index, status in enumerate(["대기", "취소", "거부"], 4):
                self.add_order(db, index, status=status, filled_at=None)
            db.commit()
        data = self.get().json()
        self.assertTrue(data["position"]["available"])
        self.assertEqual([t["order_id"] for t in data["trades"]], [1])

    def test_zero_average_price_does_not_invent_pnl_or_return(self):
        with self.Session() as db:
            db.query(Portfolio).filter_by(account_id=1, symbol_code="SYN001").one().avg_price = 0
            db.commit()
        data = self.get().json()
        self.assertEqual(Decimal(data["holding"]["avg_price"]), 0)
        self.assertIsNone(data["holding"]["unrealized_pnl"])
        self.assertIsNone(data["holding"]["return_rate"])
        self.assertIn("invalid_average_price", data["limitations"])

    def test_fee_tax_do_not_rewrite_portfolio_average(self):
        with self.Session() as db:
            db.get(Order, 1).commission = 12
            db.get(Order, 1).tax = 34
            db.commit()
        data = self.get().json()
        self.assertEqual(Decimal(data["holding"]["avg_price"]), Decimal("100.25"))
        self.assertEqual(Decimal(data["holding"]["unrealized_pnl"]), Decimal("202.50"))

    def test_history_market_price_is_not_substituted_with_opening_fill(self):
        data = self.get().json()
        self.assertEqual(Decimal(data["position"]["opening_price"]), Decimal("100.25"))
        self.assertIsNone(data["price_context"]["position_start_price"])
        self.assertIsNone(data["price_context"]["change_since_position_start"])
        for name in ["buy_reason", "why_user_bought", "news_caused_move", "good_buy",
                     "bad_buy", "should_sell", "expected_price", "position_window_news"]:
            self.assertNotIn(name, str(data))

    def test_core_db_failure_is_not_hidden_as_success(self):
        with patch.object(routes, "load_portfolio_facts", side_effect=RuntimeError("private-db-marker")):
            response = self.get()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-db-marker", response.text)
        self.quote.assert_not_awaited()
        self.news.assert_not_awaited()

    def test_db_facts_are_loaded_with_one_read_statement_and_without_mutation(self):
        statements = []
        def record(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", record)
        try:
            self.assertEqual(self.get().status_code, 200)
        finally:
            event.remove(self.engine, "before_cursor_execute", record)
        self.assertEqual(len(statements), 1)
        self.assertTrue(statements[0].lstrip().upper().startswith("SELECT"))

    def test_new_request_uses_current_holding_instead_of_a_stale_cache(self):
        first = self.get().json()
        with self.Session() as db:
            db.query(Portfolio).filter_by(account_id=1, symbol_code="SYN001").one().hold_quantity = 15
            self.add_order(db, 4, quantity=5)
            db.commit()
        second = self.get().json()
        self.assertEqual(first["holding"]["quantity"], 10)
        self.assertEqual(second["holding"]["quantity"], 15)
        self.assertEqual(second["position"]["buy_count"], 2)
        self.assertEqual(self.quote.await_count, 2)


class PositionContractTests(unittest.TestCase):
    def test_unavailable_position_cannot_contain_inferred_fields(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            PositionFacts(available=False, reason="legacy_execution_time_unavailable", started_at=START)
        with self.assertRaises(ValidationError):
            PositionFacts(available=True)
