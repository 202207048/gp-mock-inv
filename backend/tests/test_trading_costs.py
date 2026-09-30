"""Offline real SQLAlchemy transactions and API tests. Never uses a live account."""
import os
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'offline-cost-tests'

import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.database import Base, get_db
from app.models import Account, ItemMaster, Order, Portfolio, User
from app.schemas.order import OrderRequest
from app.services.trading_costs import POLICY, calculate_costs
from app.services.order_service import create_order
from app.routers import orders as routes
from app.utils.deps import get_current_user


class CostTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        self.user = User(user_id=1, login_id='local', passwd='unused', user_name='local', email='local@example.test')
        self.account = Account(account_id=1, user_id=1, balance=1000000, withdrawable_cash=1000000)
        self.stock = ItemMaster(symbol_code='TEST', name='Test equity', market_type='국내주식', tax_market='KOSPI', instrument_type='EQUITY')
        self.db.add_all([self.user, self.account, self.stock]); self.db.commit()

    def tearDown(self):
        self.db.close(); self.engine.dispose()

    def req(self, side='매수', qty=10, **kwargs):
        return OrderRequest(account_id=1, symbol_code='TEST', order_type=side, price=1, quantity=qty, cost_policy_version=kwargs.get('version', POLICY['version']))

    def test_buy_sell_cash_basis_and_immutable_cost_history(self):
        buy = create_order(self.db, self.req(), 1, Decimal(50000))
        self.assertEqual(buy.commission, 70)
        self.assertEqual(buy.cash_delta, -500070)
        self.assertEqual(self.account.withdrawable_cash, 499930)
        holding = self.db.query(Portfolio).one()
        self.assertEqual(holding.acquisition_cost, 500070)
        sale = create_order(self.db, self.req('매도', 5), 1, Decimal(50000))
        self.assertEqual((sale.commission, sale.transaction_tax, sale.rural_tax), (30, 125, 375))
        self.assertEqual(sale.realized_pnl, -565)
        self.assertEqual(self.account.withdrawable_cash, 749400)
        self.assertEqual(holding.acquisition_cost, 250035)
        final = create_order(self.db, self.req('매도', 5), 1, Decimal(51000))
        self.assertEqual(final.realized_pnl, 4426)
        self.assertEqual(self.db.query(Portfolio).count(), 0)
        self.assertEqual(self.account.withdrawable_cash, 1003861)
        self.assertEqual(self.db.get(Order, buy.order_id).commission, 70)

    def test_fee_shortfall_rejects_without_mutation(self):
        self.account.withdrawable_cash = Decimal(500000); self.db.commit()
        with self.assertRaises(HTTPException): create_order(self.db, self.req(), 1, Decimal(50000))
        self.assertEqual(self.account.withdrawable_cash, 500000)
        self.assertEqual(self.db.query(Order).count(), 0)
        self.assertEqual(self.db.query(Portfolio).count(), 0)

    def test_unknown_product_and_unsupported_policy_reject(self):
        for field, value in [('tax_market', None), ('instrument_type', 'ETF')]:
            old = getattr(self.stock, field); setattr(self.stock, field, value); self.db.commit()
            with self.assertRaises(HTTPException): create_order(self.db, self.req(), 1, Decimal(50000))
            setattr(self.stock, field, old); self.db.commit()
        with self.assertRaises(HTTPException): create_order(self.db, self.req(version='old'), 1, Decimal(50000))
        with self.assertRaises(HTTPException): create_order(self.db, self.req(), 2, Decimal(50000))
        with self.assertRaises(HTTPException): create_order(self.db, self.req('매도', 1), 1, Decimal(50000))
        self.assertEqual(self.db.query(Order).count(), 0)
        self.assertEqual(self.account.withdrawable_cash, 1000000)

    def test_rounding_and_markets(self):
        self.assertEqual(calculate_costs(66666, 1, '매수', 'KOSPI')['commission'], 0)
        self.assertEqual(calculate_costs(66667, 1, '매수', 'KOSPI')['commission'], 10)
        self.assertEqual(calculate_costs(1999, 1, '매도', 'KOSPI')['rural_tax'], 2)
        result = calculate_costs(50000, 5, '매도', 'KOSDAQ')
        self.assertEqual((result['transaction_tax'], result['rural_tax']), (500, 0))
        for value in ('NaN', 'Infinity', '-1', '0', '1.5'):
            with self.assertRaises(ValueError): calculate_costs(value, 1, '매수', 'KOSPI')

    def test_legacy_and_fractional_allocation_conservation(self):
        self.db.add(Portfolio(account_id=1, symbol_code='TEST', avg_price=Decimal('33333.333333'), hold_quantity=3, acquisition_cost=Decimal('100010')))
        self.db.commit()
        pnl = sum(create_order(self.db, self.req('매도', 1), 1, Decimal(40000)).realized_pnl for _ in range(3))
        self.assertEqual(pnl, Decimal(119760) - Decimal(100010))
        legacy = Order(account_id=1, symbol_code='TEST', order_type='매수', price=100, quantity=1, status='체결')
        self.db.add(legacy); self.db.commit()
        self.assertIsNone(legacy.commission)
        self.assertIsNone(legacy.cost_policy_version)

    def test_api_authoritative_price_policy_and_cost_history(self):
        app = FastAPI(); app.include_router(routes.router, prefix='/orders')
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[get_current_user] = lambda: self.user
        with TestClient(app) as client:
            policy = client.get('/orders/cost-policy?symbol_code=TEST')
            self.assertEqual(policy.status_code, 200)
            self.assertEqual(policy.json()['tax_market'], 'KOSPI')
            with patch.object(routes, 'get_current_price', AsyncMock(return_value={'current_price': 50000})):
                response = client.post('/orders', json=self.req().model_dump(mode='json'))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(Decimal(response.json()['price']), 50000)  # request sent 1
            self.assertEqual(Decimal(response.json()['commission']), 70)
            history = client.get('/orders?account_id=1').json()
            self.assertEqual(history[0]['cash_delta'], response.json()['cash_delta'])
            self.assertEqual(history[0]['cost_policy_version'], POLICY['version'])


if __name__ == '__main__': unittest.main()
