"""격리 SQLite와 가짜 시세로 체결 시각 및 기존 주문 동작을 검증한다."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateColumn

from tests import test_order_types as fixtures
from app.models import Account, Order, Portfolio
from app.schemas.order import OrderRequest, OrderResponse
from app.services import order_service as service

UTC = timezone.utc
NOW = datetime(2026, 10, 21, 1, 0, 0, 123456, tzinfo=UTC)


def stored_utc(value):
    # SQLite drops tzinfo; PostgreSQL TIMESTAMPTZ behavior is checked via DDL.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


class FilledAtTests(unittest.TestCase):
    setUp = fixtures.OrderTypeTests.setUp
    order = fixtures.OrderTypeTests.order

    def request(self, **changes):
        return OrderRequest(**(dict(account_id=1, symbol_code="005930", order_type="매수",
                                    price_type="시장가", price=50000, quantity=2) | changes))

    def create(self, db, now=NOW, **changes):
        return service.create_order(db, self.request(**changes), 1, Decimal(50000), now=now)

    def test_immediate_buy_uses_injected_time_and_preserves_costs(self):
        with self.Session() as db:
            order = self.create(db)
            self.assertEqual(stored_utc(order.filled_at), NOW)
            self.assertEqual(order.status, "체결")
            self.assertEqual(order.price, Decimal(50000))
            self.assertEqual(order.commission, 15)
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 899985)
            self.assertEqual(db.query(Portfolio).one().hold_quantity, 2)

    def test_immediate_sell_uses_its_fill_time(self):
        with self.Session() as db:
            self.create(db)
            later = NOW + timedelta(seconds=1)
            order = self.create(db, now=later, order_type="매도")
            self.assertEqual(stored_utc(order.filled_at), later)
            self.assertEqual(order.status, "체결")
            self.assertEqual(order.tax, 180)
            self.assertEqual(db.query(Portfolio).count(), 0)

    def test_pending_creation_and_unfilled_check_have_no_fill_time(self):
        with self.Session() as db:
            order = self.create(db, price_type="지정가")
            self.assertEqual(order.status, "대기")
            self.assertIsNone(order.filled_at)
            checked = service.check_pending_order(db, order.order_id, 1, 1, self.book,
                                                  now=NOW + timedelta(seconds=1))
            self.assertEqual(checked.status, "대기")
            self.assertIsNone(checked.filled_at)
            self.assertEqual(db.query(Portfolio).count(), 0)

    def test_delayed_buy_records_fill_time_and_keeps_submission_time(self):
        with self.Session() as db:
            order = self.create(db, price_type="지정가")
            order.created_at = NOW.replace(tzinfo=None)
            db.commit()
            later = NOW + timedelta(hours=1)
            book = dict(self.book, ask=49900, bid=49800)
            checked = service.check_pending_order(db, order.order_id, 1, 1, book, now=later)
            self.assertEqual(checked.status, "체결")
            self.assertEqual(stored_utc(checked.filled_at), later)
            self.assertEqual(stored_utc(checked.created_at), NOW)
            self.assertEqual(checked.price, 49900)
            self.assertEqual(db.query(Portfolio).one().hold_quantity, 2)

    def test_delayed_sell_records_fill_time(self):
        with self.Session() as db:
            self.create(db)
            order = self.create(db, price_type="지정가", order_type="매도")
            self.assertIsNone(order.filled_at)
            later = NOW + timedelta(minutes=1)
            checked = service.check_pending_order(db, order.order_id, 1, 1,
                                                  dict(self.book, bid=50000, ask=50100), now=later)
            self.assertEqual(checked.status, "체결")
            self.assertEqual(stored_utc(checked.filled_at), later)
            self.assertEqual(db.query(Portfolio).count(), 0)

    def test_rejected_pending_order_has_no_fill_time(self):
        with self.Session() as db:
            pending = self.create(db, price_type="지정가", quantity=19)
            self.create(db, quantity=19)
            checked = service.check_pending_order(db, pending.order_id, 1, 1,
                                                  dict(self.book, bid=49000, ask=49100),
                                                  now=NOW + timedelta(minutes=1))
            self.assertEqual(checked.status, "거부")
            self.assertIsNone(checked.filled_at)
            self.assertEqual(db.query(Portfolio).one().hold_quantity, 19)

    def test_cancelled_order_and_recheck_have_no_fill_time(self):
        result = self.order("지정가").json()
        self.assertEqual(self.client.put(f"/orders/{result['order_id']}/cancel").status_code, 200)
        self.book.update(bid=49000, ask=49100)
        self.assertEqual(self.client.post(f"/orders/{result['order_id']}/check").status_code, 200)
        with self.Session() as db:
            self.assertEqual(db.get(Order, result["order_id"]).status, "취소")
            self.assertIsNone(db.get(Order, result["order_id"]).filled_at)
            self.assertEqual(db.query(Portfolio).count(), 0)

    def test_rechecking_filled_order_does_not_overwrite_time(self):
        with self.Session() as db:
            order = self.create(db)
            checked = service.check_pending_order(db, order.order_id, 1, 1, self.book,
                                                  now=NOW + timedelta(days=1))
            self.assertEqual(stored_utc(checked.filled_at), NOW)
            self.assertEqual(db.query(Order).count(), 1)
            self.assertEqual(db.query(Portfolio).one().hold_quantity, 2)

    def test_default_clock_is_used_without_sleep(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        with self.Session() as db, patch.object(service, "datetime", Clock):
            order = service.create_order(db, self.request(), 1, Decimal(50000))
            self.assertEqual(stored_utc(order.filled_at), NOW)
            pending = self.create(db, price_type="지정가")
            checked = service.check_pending_order(db, pending.order_id, 1, 1,
                                                  dict(self.book, ask=49900, bid=49800))
            self.assertEqual(stored_utc(checked.filled_at), NOW)

    def test_quote_order_types_record_time_only_when_fully_executable(self):
        for kind in ["중간가", "최유리지정가", "최우선지정가"]:
            with self.subTest(kind=kind), self.Session() as db:
                book = dict(self.book, bid=50000, ask=50000)
                order = service.create_order(db, self.request(price_type=kind), 1,
                                             Decimal(50000), book=book, now=NOW)
                self.assertEqual(order.status, "체결")
                self.assertEqual(stored_utc(order.filled_at), NOW)
                waiting = service.create_order(db, self.request(price_type=kind), 1,
                                               Decimal(50000), book=dict(book, ask_quantity=1), now=NOW)
                self.assertEqual(waiting.status, "대기")
                self.assertIsNone(waiting.filled_at)

    def test_fill_time_orders_delayed_buy_after_completed_round_trip(self):
        with self.Session() as db:
            pending = self.create(db, price_type="지정가", quantity=5)
            pending.created_at = NOW.replace(tzinfo=None)
            db.commit()
            buy = self.create(db, now=NOW + timedelta(minutes=1), quantity=10)
            sell = self.create(db, now=NOW + timedelta(minutes=2), order_type="매도", quantity=10)
            filled = service.check_pending_order(db, pending.order_id, 1, 1,
                                                 dict(self.book, ask=49900, bid=49800),
                                                 now=NOW + timedelta(minutes=3))
            rows = db.query(Order).order_by(Order.filled_at, Order.order_id).all()
            self.assertEqual([o.order_id for o in rows], [buy.order_id, sell.order_id, pending.order_id])
            self.assertEqual(stored_utc(filled.filled_at), NOW + timedelta(minutes=3))
            self.assertEqual(stored_utc(filled.created_at), NOW)
            self.assertEqual(db.query(Portfolio).one().hold_quantity, 5)

    def test_legacy_null_is_not_repaired_by_recheck(self):
        with self.Session() as db:
            order = self.create(db)
            order.filled_at = None
            db.commit()
            result = service.check_pending_order(db, order.order_id, 1, 1, self.book,
                                                 now=NOW + timedelta(days=1))
            self.assertIsNone(result.filled_at)

    def test_rollback_discards_fill_and_financial_changes(self):
        with self.Session() as db:
            order = service.create_order(db, self.request(), 1, Decimal(50000), now=NOW, commit=False)
            self.assertEqual(order.filled_at, NOW)
            db.rollback()
            self.assertEqual(db.query(Order).count(), 0)
            self.assertEqual(db.query(Portfolio).count(), 0)
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 1000000)

    def test_order_response_exposes_fill_time_for_trade_sharing(self):
        result = self.order().json()
        self.assertIsNotNone(result['filled_at'])
        self.assertIn('filled_at', OrderResponse.model_fields)
        with self.Session() as db:
            self.assertIsNotNone(db.get(Order, result["order_id"]).filled_at)


class FilledAtMigrationTests(unittest.TestCase):
    def test_model_and_revision_have_nullable_utc_column_without_default(self):
        column = Order.__table__.c.filled_at
        self.assertTrue(column.nullable)
        self.assertTrue(column.type.timezone)
        self.assertIsNone(column.default)
        self.assertIsNone(column.server_default)
        self.assertIn("TIMESTAMP WITH TIME ZONE", str(CreateColumn(column).compile(dialect=postgresql.dialect())))
        scripts = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
        history = {revision.revision for revision in scripts.walk_revisions(base="base", head=scripts.get_current_head())}
        self.assertIn("f20261021_order_filled_at", history)
        self.assertEqual(scripts.get_revision("f20261021_order_filled_at").down_revision, "e20261007_misu")

    def test_migration_preserves_legacy_nulls_and_can_downgrade(self):
        path = Path(__file__).resolve().parents[1] / "alembic/versions/f20261021_order_filled_at.py"
        spec = spec_from_file_location("filled_at_migration", path)
        migration = module_from_spec(spec)
        spec.loader.exec_module(migration)
        engine = sa.create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE orders (order_id INTEGER PRIMARY KEY, price_type VARCHAR(10), status VARCHAR(10), created_at DATETIME)"))
            for index, kind in enumerate(["시장가", "지정가"], 1):
                conn.execute(sa.text("INSERT INTO orders VALUES (:id, :kind, :status, :created)"),
                             dict(id=index, kind=kind, status="체결", created="2026-10-01 01:00:00"))
            with patch.object(migration, "op", Operations(MigrationContext.configure(conn))):
                migration.upgrade()
                columns = {col["name"]: col for col in sa.inspect(conn).get_columns("orders")}
                self.assertTrue(columns["filled_at"]["nullable"])
                self.assertIsNone(columns["filled_at"]["default"])
                rows = conn.execute(sa.text("SELECT order_id, price_type, status, created_at, filled_at FROM orders ORDER BY order_id")).all()
                self.assertEqual(len(rows), 2)
                self.assertTrue(all(row.filled_at is None for row in rows))
                self.assertTrue(all(row.status == "체결" and row.created_at == "2026-10-01 01:00:00" for row in rows))
                self.assertEqual(sa.inspect(conn).get_table_names(), ["orders"])
                migration.downgrade()
                self.assertNotIn("filled_at", [c["name"] for c in sa.inspect(conn).get_columns("orders")])
                self.assertEqual(conn.execute(sa.text("SELECT COUNT(*) FROM orders")).scalar_one(), 2)
