"""No external broker/DB calls: debt, calendar, settlement and loss invariants."""
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch, AsyncMock
from fastapi import HTTPException
from tests.test_order_types import OrderTypeTests
from app.models import Account, Order, Portfolio
from app.models.misu import MisuDebt, MisuSettlement, MisuPayment
from app.schemas.order import OrderRequest
from app.services.order_service import create_order
from app.services import misu_service as misu
from app.services.misu_monitor import process_account
from app.routers import misu as routes
from app.routers import orders as order_routes

MONDAY = datetime(2026, 10, 12, 1, tzinfo=timezone.utc)


class MisuTests(unittest.TestCase):
    setUp = OrderTypeTests.setUp

    def request(self, **changes):
        return OrderRequest(**(dict(account_id=1, symbol_code='005930', order_type='매수',
            price_type='시장가', price=50000, quantity=4, funding_type='미수',
            misu_risk_ack=True, client_request_id=uuid4()) | changes))

    def buy(self, db, req=None, now=MONDAY):
        account = db.get(Account, 1)
        account.balance = account.withdrawable_cash = Decimal(100030)
        db.commit()
        return create_order(db, req or self.request(), 1, Decimal(50000), now=now)

    def test_debt_cash_and_equity_not_free_money(self):
        with self.Session() as db:
            order = self.buy(db)
            self.assertEqual(order.funding_type, '미수')
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 0)
            self.assertEqual(misu.snapshot(db, 1, MONDAY)['debt'], 100000)
            self.assertEqual(db.query(Portfolio).first().hold_quantity, 4)
            self.assertEqual(misu.snapshot(db, 1, MONDAY)['due_at'], MONDAY.replace(day=14, hour=8))

    def test_idempotent_order_and_conflicting_payload(self):
        with self.Session() as db:
            req = self.request()
            first = self.buy(db, req)
            again = create_order(db, req, 1, Decimal(51000), now=MONDAY)
            self.assertEqual(first.order_id, again.order_id)
            self.assertEqual(db.query(Order).count(), 1)
            with self.assertRaises(HTTPException) as error:
                create_order(db, req.model_copy(update={'quantity': 3}), 1, Decimal(50000), now=MONDAY)
            self.assertEqual(error.exception.status_code, 409)

    def test_required_margin_fee_ack_and_order_type(self):
        for changes in ({'quantity': 5}, {'misu_risk_ack': False}, {'client_request_id': None}, {'price_type': '지정가'}, {'order_type': '매도'}):
            with self.subTest(changes=changes), self.Session() as db:
                with self.assertRaises(HTTPException):
                    self.buy(db, self.request(**changes))
                db.rollback()
                self.assertEqual(db.query(MisuDebt).count(), 0)
                self.assertEqual(db.query(Order).count(), 0)

    def test_holidays_year_end_and_closed_session(self):
        self.assertEqual(misu.after_sessions(datetime(2026, 10, 8).date(), 2), datetime(2026, 10, 13).date())
        self.assertEqual(misu.after_sessions(datetime(2026, 12, 30).date(), 2), datetime(2027, 1, 5).date())
        with self.Session() as db, self.assertRaises(HTTPException):
            self.buy(db, now=MONDAY.replace(day=9))

    def test_regular_and_pending_buys_cannot_bypass_outstanding_debt(self):
        with self.Session() as db:
            self.buy(db)
            for kind in ['시장가', '지정가', '중간가']:
                with self.subTest(kind=kind), self.assertRaises(HTTPException):
                    create_order(db, self.request(funding_type='현금', price_type=kind), 1, Decimal(1), now=MONDAY)

    def test_same_day_sale_settles_on_time_after_worker_downtime(self):
        with self.Session() as db:
            self.buy(db)
            create_order(db, self.request(order_type='매도', funding_type='현금'), 1, Decimal(50000), now=MONDAY)
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 0)
            state = misu.snapshot(db, 1, MONDAY)
            self.assertEqual(state['pending_proceeds'], 199610)
            misu.settle_account(db, db.get(Account, 1), MONDAY + timedelta(days=4))
            db.commit()
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 99610)
            self.assertIsNone(db.query(MisuDebt).first().frozen_until)
            misu.settle_account(db, db.get(Account, 1), MONDAY + timedelta(days=4))
            self.assertEqual(db.get(Account, 1).withdrawable_cash, 99610)

    def test_next_day_sale_is_late_and_freeze_survives_payment(self):
        with self.Session() as db:
            self.buy(db)
            create_order(db, self.request(order_type='매도', funding_type='현금'), 1, Decimal(50000), now=MONDAY + timedelta(days=1))
            misu.settle_account(db, db.get(Account, 1), MONDAY + timedelta(days=4))
            db.commit()
            self.assertEqual(misu.snapshot(db, 1, MONDAY + timedelta(days=4))['debt'], 0)
            self.assertIsNotNone(misu.snapshot(db, 1, MONDAY + timedelta(days=4))['frozen_until'])

    def test_forced_sale_can_leave_debt_and_never_double_sells(self):
        with self.Session() as db:
            self.buy(db)
            now = MONDAY + timedelta(days=3)
            quotes = {'005930': {'price': 20000, 'observed_at': now}}
            process_account(db, 1, quotes, now); db.commit()
            self.assertEqual(db.query(Portfolio).count(), 0)
            self.assertEqual(db.query(Order).filter_by(funding_type='반대매매').count(), 1)
            self.assertEqual(db.query(MisuSettlement).first().amount, 79844)
            process_account(db, 1, quotes, now); db.commit()
            self.assertEqual(db.query(Order).count(), 2)
            misu.settle_account(db, db.get(Account, 1), now + timedelta(days=7)); db.commit()
            self.assertEqual(misu.snapshot(db, 1, now + timedelta(days=7))['debt'], 20156)

    def test_missing_or_stale_quote_never_liquidates(self):
        with self.Session() as db:
            self.buy(db)
            now = MONDAY + timedelta(days=3)
            process_account(db, 1, {'005930': {'price': 20000, 'observed_at': MONDAY}}, now); db.commit()
            self.assertEqual(db.query(Order).count(), 1)
            self.assertTrue(misu.snapshot(db, 1, now)['overdue'])

    def test_payment_authorization_validation_and_duplicate(self):
        self.client.app.include_router(routes.router, prefix='/misu')
        with self.Session() as db:
            self.buy(db)
        payload = {'amount': 50000, 'client_request_id': str(uuid4())}
        self.assertEqual(self.client.post('/misu/2/repay', json=payload).status_code, 404)
        self.assertEqual(self.client.get('/misu/2').status_code, 404)
        self.assertEqual(self.client.post('/misu/1/repay', json=payload | {'amount': -1}).status_code, 422)
        self.assertEqual(self.client.post('/misu/1/repay', json=payload | {'amount': 100001}).status_code, 400)
        for _ in range(2):
            response = self.client.post('/misu/1/repay', json=payload)
            self.assertEqual(response.status_code, 200, response.text)
        with self.Session() as db:
            self.assertEqual(db.query(MisuPayment).count(), 1)
            self.assertEqual(db.query(MisuDebt).first().remaining, 50000)

    def test_order_api_requires_monitor_and_fresh_quote_and_uses_server_price(self):
        request = self.request().model_dump(mode='json')
        with self.Session() as db:
            account = db.get(Account, 1)
            account.balance = account.withdrawable_cash = 100030
            db.commit()
        with patch('app.services.misu_monitor.healthy', return_value=False):
            self.assertEqual(self.client.post('/orders', json=request).status_code, 503)
        class MondayClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return MONDAY
        with patch('app.services.misu_monitor.healthy', return_value=True), patch('app.services.order_service.datetime', MondayClock):
            with patch.object(order_routes, 'fetch_execution_quote', new=AsyncMock(return_value=None)):
                self.assertEqual(self.client.post('/orders', json=request).status_code, 400)
            with patch.object(order_routes, 'fetch_execution_quote', new=AsyncMock(side_effect=lambda symbol, now: {'price': Decimal(50000), 'observed_at': now})):
                response = self.client.post('/orders', json=request | {'price': 1})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(Decimal(response.json()['price']), 50000)
            self.assertEqual(response.json()['funding_type'], '미수')


if __name__ == '__main__':
    unittest.main()
