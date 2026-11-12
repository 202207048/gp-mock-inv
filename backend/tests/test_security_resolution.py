"""Unseeded symbols use verified metadata; all prices remain server-owned."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from tests import test_order_types as order_fixtures
from tests import test_order_automations as automation_fixtures
from app.models import Account, ItemMaster, Order, Portfolio
from app.services import security_service as service
from app.routers import orders

JYP = {'symbol_code': '035900', 'name': 'JYP Ent.', 'market_type': '국내주식', 'sector_code': None}


class ResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_kis_first_and_exact_code_fallback(self):
        with patch.object(service.kis, 'get_listed_stock', AsyncMock(return_value=JYP)), \
             patch.object(service.kis, 'search_listed_stocks', AsyncMock()) as search:
            self.assertEqual(await service.resolve_listed_stock('035900'), JYP)
            search.assert_not_awaited()
        with patch.object(service.kis, 'get_listed_stock', AsyncMock(return_value=None)), \
             patch.object(service.kis, 'search_listed_stocks', AsyncMock(return_value=[dict(JYP, symbol_code='035901'), JYP])):
            self.assertEqual(await service.resolve_listed_stock('035900'), JYP)

    async def test_quote_reuse_and_mismatched_or_blank_results_rejected(self):
        with patch.object(service.kis, 'get_listed_stock', AsyncMock()) as kis, \
             patch.object(service.kis, 'search_listed_stocks', AsyncMock(return_value=[dict(JYP, symbol_code='035901'), dict(JYP, name=' ')])):
            self.assertIsNone(await service.resolve_listed_stock('035900', quote={'name': ''}))
            self.assertIsNone(await service.resolve_listed_stock('invalid', quote={'name': 'fake'}))
            kis.assert_not_awaited()


class UnseededOrderTests(unittest.TestCase):
    setUp = order_fixtures.OrderTypeTests.setUp
    order = order_fixtures.OrderTypeTests.order

    def test_all_order_types_register_unseeded_symbols(self):
        with patch.object(service.kis, 'get_listed_stock', AsyncMock(return_value=None)), \
             patch.object(service.kis, 'search_listed_stocks', AsyncMock(side_effect=lambda code, limit: [dict(JYP, symbol_code=code)])):
            for i, kind in enumerate(['시장가', '지정가', '중간가', '최유리지정가', '최우선지정가']):
                code = f'03590{i}'
                response = self.order(kind, symbol_code=code, price=100)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()['name'], JYP['name'])
                if kind == '시장가':
                    self.assertEqual(float(response.json()['price']), 50000)
                with self.Session() as db:
                    self.assertEqual(db.get(ItemMaster, code).name, JYP['name'])
            # A repeat order uses the registered symbol without another metadata lookup.
            with patch.object(orders, 'resolve_listed_stock', AsyncMock(side_effect=AssertionError('already registered'))):
                self.assertEqual(self.order(symbol_code='035900').status_code, 200)

    def test_missing_metadata_price_or_cash_does_not_create_order(self):
        for quote, results, quantity, expected in [
            ({'current_price': 50000, 'name': ''}, [], 1, 404),
            ({'current_price': 0, 'name': ''}, [JYP], 1, 400),
            ({'current_price': 50000, 'name': ''}, [JYP], 100, 400),
        ]:
            with patch.object(orders, 'get_current_price', AsyncMock(return_value=quote)), \
                 patch.object(service.kis, 'search_listed_stocks', AsyncMock(return_value=results)):
                response = self.order(symbol_code='035900', quantity=quantity)
                self.assertEqual(response.status_code, expected, response.text)
            with self.Session() as db:
                self.assertEqual(db.query(Order).count(), 0)
                self.assertEqual(db.query(Portfolio).count(), 0)
                self.assertEqual(db.get(Account, 1).withdrawable_cash, 1000000)
                self.assertIsNone(db.get(ItemMaster, '035900'))


class UnseededAutomationTests(unittest.TestCase):
    setUp = automation_fixtures.AutomationTests.setUp
    request = automation_fixtures.AutomationTests.request

    def test_scheduled_and_condition_orders_use_same_fallback(self):
        with patch.object(service.kis, 'get_listed_stock', AsyncMock(return_value=None)), \
             patch.object(service.kis, 'search_listed_stocks', AsyncMock(side_effect=lambda code, limit: [dict(JYP, symbol_code=code)])):
            for code, changes in [('035900', {}), ('035901', dict(kind='scheduled', trigger_operator=None,
                    trigger_price=None, scheduled_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()))]:
                response = self.client.post('/automations', json=self.request(symbol_code=code, **changes))
                self.assertEqual(response.status_code, 200, response.text)
                with self.Session() as db:
                    self.assertEqual(db.get(ItemMaster, code).name, JYP['name'])
                    self.assertEqual(db.get(Account, 1).withdrawable_cash, 1000000)
                    self.assertEqual(db.query(Order).count(), 0)
