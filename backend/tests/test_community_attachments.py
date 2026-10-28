"""Offline API tests for persisted attachments, ownership and voting."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'community-offline-tests'

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import Account, ItemMaster, Order, Post, User
from app.models.community import PostVote
from app.routers.community import router
from app.utils.deps import get_current_user
from app.services.withdrawal_service import withdraw_user

PNG = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXZkAAAAASUVORK5CYII='


class CommunityAttachmentsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        event.listen(self.engine, 'connect', lambda conn, _: conn.execute('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.Session() as db:
            for uid in (1, 2):
                db.add(User(user_id=uid, login_id=f'user{uid}', passwd='hash', user_name=f'User {uid}', email=f'u{uid}@example.com'))
            db.add(ItemMaster(symbol_code='005930', name='삼성전자', market_type='국내주식'))
            db.flush()
            for uid in (1, 2):
                db.add(Account(account_id=uid, user_id=uid, account_name='Mock', balance=0, withdrawable_cash=0))
            db.flush()
            for oid, aid, status in ((1, 1, '체결'), (2, 2, '체결'), (3, 1, '대기')):
                db.add(Order(order_id=oid, account_id=aid, symbol_code='005930', order_type='매수', price=70000, quantity=3, status=status))
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/community')
        self.user_id = 1
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: User(user_id=self.user_id)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, **extra):
        return self.client.post('/community/posts', json={'title': '생각', 'content': '본문', **extra})

    def test_sale_share_persists_name_and_historical_profit(self):
        from datetime import datetime, timezone, timedelta
        from app.routers.orders import router as orders_router
        self.client.app.include_router(orders_router, prefix='/orders')
        now = datetime(2026, 10, 28, tzinfo=timezone.utc)
        with self.Session() as db:
            db.get(Order, 1).filled_at = now
            db.add(Order(order_id=4, account_id=1, symbol_code='005930', order_type='매도',
                         price=73500, quantity=2, status='체결', filled_at=now + timedelta(minutes=1)))
            db.commit()
        listing = self.client.get('/orders', params={'account_id': 1})
        self.assertEqual(listing.status_code, 200, listing.text)
        sale = next(row for row in listing.json() if row['order_id'] == 4)
        self.assertEqual(sale['name'], '삼성전자')
        self.assertEqual(float(sale['profit_loss']), 7000)
        self.assertEqual(float(sale['profit_rate']), 5)
        pid = self.create(trade_id=4).json()['post_id']
        shared = self.client.get(f'/community/posts/{pid}').json()['attachments']['trade']
        self.assertEqual(shared['name'], sale['name'])
        self.assertEqual(float(shared['profit_loss']), 7000)
        self.assertEqual(float(shared['profit_rate']), 5)
        self.assertNotIn('account_id', shared)
        self.assertNotIn('order_id', shared)

    def test_all_attachments_survive_list_and_detail(self):
        response = self.create(images=[PNG], symbol_code='005930', trade_id=1, poll={'question': '전망?', 'options': ['상승', '하락']})
        self.assertEqual(response.status_code, 200, response.text)
        pid = response.json()['post_id']
        detail = self.client.get(f'/community/posts/{pid}').json()
        listing = self.client.get('/community/posts').json()['items'][0]
        self.assertEqual(listing['content'], '본문')
        self.assertEqual(listing['attachments'], detail['attachments'])
        data = detail['attachments']
        self.assertEqual(data['images'], [PNG])
        self.assertEqual(data['symbol_name'], '삼성전자')
        self.assertEqual(data['trade']['quantity'], 3)
        self.assertNotIn('account_id', data['trade'])
        self.assertNotIn('user_id', data['trade'])
        self.assertEqual(data['poll']['total'], 0)

    def test_rejects_foreign_pending_and_missing_trades(self):
        for trade_id in (2, 3, 999):
            self.assertEqual(self.create(trade_id=trade_id).status_code, 422)
        with self.Session() as db:
            self.assertEqual(db.query(Post).count(), 0)

    def test_validation_and_legacy_plain_posts(self):
        for fields in ({'images': [PNG] * 5}, {'images': ['data:image/svg+xml;base64,PHN2Zz4=']},
                       {'images': ['data:image/png;base64,YWJj']}, {'images': ['data:image/png;base64,!!!']},
                       {'poll': {'question': 'Q', 'options': ['same', 'same']}},
                       {'poll': {'question': ' ', 'options': ['a', 'b']}}, {'title': ' '}):
            self.assertEqual(self.create(**fields).status_code, 422, fields)
        self.assertEqual(self.create().status_code, 200)

    def test_vote_is_per_user_and_can_change_without_increasing_total(self):
        pid = self.create(poll={'question': 'Q', 'options': ['A', 'B']}).json()['post_id']
        url = f'/community/posts/{pid}/vote'
        self.assertEqual(self.client.put(url, json={'option_index': 0}).json()['counts'], [1, 0])
        self.assertEqual(self.client.put(url, json={'option_index': 0}).json()['total'], 1)
        self.assertEqual(self.client.put(url, json={'option_index': 1}).json()['counts'], [0, 1])
        self.user_id = 2
        self.assertIsNone(self.client.get(f'/community/posts/{pid}').json()['attachments']['poll']['selected'])
        self.assertEqual(self.client.put(url, json={'option_index': 0}).json()['counts'], [1, 1])
        self.assertEqual(self.client.put(url, json={'option_index': 3}).status_code, 422)
        self.assertEqual(self.client.put('/community/posts/999/vote', json={'option_index': 0}).status_code, 404)
        self.user_id = 1
        self.assertEqual(self.client.delete(f'/community/posts/{pid}').status_code, 200)
        with self.Session() as db:
            self.assertEqual(db.query(PostVote).count(), 0)

    def test_search_results_outside_local_stock_table_can_be_tagged(self):
        with patch('app.routers.community.get_listed_stock', new=AsyncMock(return_value={'name': '검색 종목'})):
            response = self.create(symbol_code='000660')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/community/posts').json()['items'][0]['attachments']['symbol_name'], '검색 종목')

    def test_withdrawal_cleans_votes_on_own_and_other_posts(self):
        first = self.create(poll={'question': 'Q', 'options': ['A', 'B']}).json()['post_id']
        self.user_id = 2
        second = self.create(poll={'question': 'Q', 'options': ['A', 'B']}).json()['post_id']
        self.client.put(f'/community/posts/{first}/vote', json={'option_index': 0})
        self.user_id = 1
        self.client.put(f'/community/posts/{second}/vote', json={'option_index': 1})
        with self.Session() as db, patch('app.services.withdrawal_service.verify_password', return_value=True):
            withdraw_user(db, 1, 'test')
            self.assertEqual(db.query(PostVote).count(), 0)
            self.assertIsNotNone(db.get(Post, second))

    def test_category_filter_and_quote_use_server_original(self):
        first = self.create(symbol_code='005930').json()['post_id']
        self.create()
        listing = self.client.get('/community/posts?symbol_code=005930').json()
        self.assertEqual(listing['total'], 1)
        self.assertEqual(listing['items'][0]['post_id'], first)
        quoted = self.create(quote_post_id=first).json()['post_id']
        detail = self.client.get(f'/community/posts/{quoted}').json()
        self.assertEqual(detail['attachments']['quote']['content'], '본문')
        self.assertEqual(detail['attachments']['quote']['author_name'], 'User 1')
        self.assertEqual(self.create(quote_post_id=999).status_code, 404)
        self.assertTrue(self.client.get('/community/capabilities').json()['quotes'])


if __name__ == '__main__':
    unittest.main()
