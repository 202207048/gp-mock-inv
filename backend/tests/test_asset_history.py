import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Account, ItemMaster, Portfolio, User
from app.models.asset_history import AssetHistory
from app.routers.portfolio import router
from app.services import asset_history_service as service
from app.utils.deps import get_current_user


class AssetHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([User(user_id=i, login_id=str(i), passwd='test', user_name=str(i), email=f'{i}@test.invalid') for i in [1, 2]])
            db.add_all([Account(account_id=1, user_id=1, withdrawable_cash=10000, balance=10000),
                        Account(account_id=2, user_id=2, withdrawable_cash=9000, balance=9000),
                        Account(account_id=3, user_id=1, withdrawable_cash=500, balance=500),
                        ItemMaster(symbol_code='SYN001', name='Test', market_type='국내주식')])
            db.add(Portfolio(account_id=1, symbol_code='SYN001', hold_quantity=2, avg_price=100))
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api/trading/portfolio')
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.quote = AsyncMock(return_value={'current_price': 125})
        patcher = patch.object(service, 'get_current_price', self.quote)
        patcher.start()
        self.addCleanup(patcher.stop)

    def save(self, account=1, **kwargs):
        return self.client.post(f'/api/trading/portfolio/history/snapshot?account_id={account}', **kwargs)

    def test_authentication_is_required(self):
        self.client.app.dependency_overrides.pop(get_current_user)
        self.assertIn(self.save().status_code, (401, 403))
        self.assertIn(self.client.get('/api/trading/portfolio/history?account_id=1').status_code, (401, 403))
        self.quote.assert_not_awaited()

    def test_migration_creates_unique_daily_rows_and_is_reversible(self):
        import importlib.util
        from pathlib import Path
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import inspect
        spec = importlib.util.spec_from_file_location('history_migration', Path(__file__).parents[1] / 'alembic/versions/l20261103_asset_history.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        AssetHistory.__table__.drop(self.engine)
        with self.engine.begin() as connection:
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                self.assertEqual(inspect(connection).get_unique_constraints('asset_history')[0]['column_names'], ['account_id', 'date'])
                migration.downgrade()
                self.assertFalse(inspect(connection).has_table('asset_history'))
                self.assertTrue(inspect(connection).has_table('account'))

    def test_server_valuation_persists_across_sessions_and_overwrites_same_day(self):
        response = self.save(json={'total': 999999999, 'date': '2000-01-01'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(float(response.json()['rows'][0]['total']), 10250)
        self.quote.return_value = {'current_price': 150}
        self.assertEqual(float(self.save().json()['rows'][0]['total']), 10300)
        with self.Session() as db:
            self.assertEqual(db.query(AssetHistory).count(), 1)
        # Independent request/database session retrieves the persisted server value.
        response = self.client.get('/api/trading/portfolio/history?account_id=1')
        self.assertEqual(float(response.json()['rows'][0]['total']), 10300)

    def test_owner_account_isolation_and_empty_account(self):
        for account in [2, 999]:
            self.assertEqual(self.save(account).status_code, 404)
            self.assertEqual(self.client.get(f'/api/trading/portfolio/history?account_id={account}').status_code, 404)
        self.quote.assert_not_awaited()
        self.assertEqual(float(self.save(3).json()['rows'][0]['total']), 500)
        self.assertEqual(self.client.get('/api/trading/portfolio/history?account_id=1').json()['rows'], [])

    def test_missing_quote_never_overwrites_valid_history(self):
        self.save()
        for price in [None, 0, -1, 'NaN', 'Infinity']:
            self.quote.return_value = {'current_price': price}
            self.assertEqual(self.save().status_code, 503)
        with self.Session() as db:
            self.assertEqual(db.query(AssetHistory).one().total, Decimal('10250'))

    def test_korean_midnight_week_boundary_and_old_weeks(self):
        sunday = datetime(2026, 11, 1, 14, 59, tzinfo=timezone.utc)
        monday = sunday + timedelta(minutes=2)
        with self.Session() as db:
            asyncio.run(service.record_asset_history(db, 1, 1, now=sunday))
            asyncio.run(service.record_asset_history(db, 1, 1, now=monday))
            current = service.read_asset_week(db, 1, 1, now=monday)
            prior = service.read_asset_week(db, 1, 1, week_offset=1, now=monday)
            self.assertEqual(str(current['week_start']), '2026-11-02')
            self.assertEqual([str(row['date']) for row in current['rows']], ['2026-11-02'])
            self.assertEqual([str(row['date']) for row in prior['rows']], ['2026-11-01'])
        self.assertEqual(self.client.get('/api/trading/portfolio/history?account_id=1&week_offset=-1').status_code, 422)

    def test_trade_during_price_read_rejects_inconsistent_snapshot(self):
        async def changed(code):
            with self.Session() as db:
                db.query(Account).filter_by(account_id=1).update({'withdrawable_cash': 8000})
                db.commit()
            return {'current_price': 125}
        self.quote.side_effect = changed
        self.assertEqual(self.save().status_code, 409)
        with self.Session() as db:
            self.assertEqual(db.query(AssetHistory).count(), 0)

    def test_debt_and_pending_proceeds_are_included(self):
        with patch.object(service, 'snapshot', return_value={'debt': Decimal(700), 'pending_proceeds': Decimal(300)}):
            response = self.save()
        self.assertEqual(float(response.json()['rows'][0]['total']), 9850)

    def test_daily_monitor_captures_without_browser_and_skips_completed_days(self):
        from app.services.asset_history_monitor import capture_daily
        before = datetime(2026, 11, 3, 6, 30, tzinfo=timezone.utc)
        after = datetime(2026, 11, 3, 7, 0, tzinfo=timezone.utc)
        asyncio.run(capture_daily(self.Session, before))
        self.quote.assert_not_awaited()
        asyncio.run(capture_daily(self.Session, after))
        self.assertEqual(self.quote.await_count, 1)
        asyncio.run(capture_daily(self.Session, after))
        self.assertEqual(self.quote.await_count, 1)
        with self.Session() as db:
            self.assertEqual(db.query(AssetHistory).count(), 3)

    def test_previous_record_crosses_empty_weeks_and_stays_account_scoped(self):
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        with self.Session() as db:
            for account, days, demo in [(1, 30, False), (1, 20, False), (1, 10, True), (2, 8, False)]:
                stamp = now - timedelta(days=days)
                db.add(AssetHistory(account_id=account, date=stamp.date(), observed_at=stamp,
                    cash=100, market=0, debt=0, pending_proceeds=0, total=100, is_demo=demo))
            db.commit()
            result = service.read_asset_week(db, 1, 1, now=now)
            self.assertEqual(result['rows'], [])
            self.assertEqual(result['previous']['date'], (now - timedelta(days=20)).date())
            self.assertIsNone(service.read_asset_week(db, 3, 1, now=now)['previous'])
            self.assertIsNone(service.read_asset_week(db, 1, 1, week_offset=10, now=now)['previous'])

    def test_range_validation_and_cross_week_records(self):
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        with self.Session() as db:
            for days in [30, 10, 2]:
                stamp = now - timedelta(days=days)
                db.add(AssetHistory(account_id=1, date=stamp.date(), observed_at=stamp,
                    cash=100, market=0, debt=0, pending_proceeds=0, total=100 + days, is_demo=False))
            db.commit()
            start, end = (now - timedelta(days=15)).date(), now.date()
            result = service.read_asset_range(db, 1, 1, start, end, now=now)
            self.assertEqual(len(result['rows']), 2)
            self.assertEqual(result['previous']['date'], (now - timedelta(days=30)).date())
            for left, right in [(end, start), (start, end + timedelta(days=1)), (start - timedelta(weeks=521), end)]:
                with self.assertRaises(Exception) as error:
                    service.read_asset_range(db, 1, 1, left, right, now=now)
                self.assertEqual(error.exception.status_code, 422)
            self.assertEqual(service.read_asset_range(db, 1, 1, end, end, now=now)['rows'], [])
        response = self.client.get('/api/trading/portfolio/history/range', params={'account_id': 1, 'start_date': str(start), 'end_date': str(end)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()['rows']), 2)
        self.assertEqual(self.client.get('/api/trading/portfolio/history/range', params={'account_id': 2, 'start_date': str(start), 'end_date': str(end)}).status_code, 404)
