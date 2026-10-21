"""Offline withdrawal tests with enforced foreign keys; no real user data."""
import os
import unittest
from datetime import datetime
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'withdrawal-offline-tests'

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import Account, AiPropensityAdvice, Comment, Follow, ItemMaster, Order, OrderAutomation, Portfolio, Post, PostLike, User, UserSurveyResponse
from app.models.misu import MisuDebt, MisuPayment, MisuSettlement
from app.routers.auth import router
from app.utils.security import create_access_token, hash_password


class WithdrawalTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        event.listen(self.engine, 'connect', lambda conn, _: conn.execute('PRAGMA foreign_keys=ON'))
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.Session() as db:
            for uid in (1, 2):
                db.add(User(user_id=uid, login_id=f'user{uid}', passwd=hash_password('secret'), user_name='Tester', email=f'user{uid}@example.com'))
            db.add(ItemMaster(symbol_code='TEST', name='Example', market_type='stock'))
            db.flush()
            for uid in (1, 2):
                db.add(Account(account_id=uid, user_id=uid, account_name='Simulation', balance=100, withdrawable_cash=100))
                db.add(Post(post_id=uid, user_id=uid, title='Title', content='Content'))
                db.add(UserSurveyResponse(user_id=uid, question_number=1, selected_answer='Answer'))
                db.add(AiPropensityAdvice(user_id=uid, ai_analysis_result='Result'))
            db.flush()
            for uid in (1, 2):
                db.add(Order(order_id=uid, account_id=uid, symbol_code='TEST', order_type='buy', price=1, quantity=1))
                db.add(Portfolio(account_id=uid, symbol_code='TEST', hold_quantity=1, avg_price=1))
                db.add(Comment(post_id=uid, user_id=uid, content='Own reply'))
                db.add(PostLike(post_id=uid, user_id=uid))
            db.flush()
            for uid in (1, 2):
                db.add(OrderAutomation(account_id=uid, symbol_code='TEST', order_type='buy', quantity=1, kind='scheduled', expires_at=datetime(2030, 1, 1), client_request_id=f'job{uid}', order_id=uid))
                db.add(MisuDebt(account_id=uid, order_id=uid, request_id=f'debt{uid}', original=1, remaining=1, due_at=datetime(2030, 1, 1), liquidate_at=datetime(2030, 1, 2)))
                db.add(MisuSettlement(account_id=uid, order_id=uid, amount=1, available_at=datetime(2030, 1, 1)))
                db.add(MisuPayment(account_id=uid, request_id=f'pay{uid}', amount=1, created_at=datetime.now()))
            db.add(Comment(post_id=1, user_id=2, content='Reply on departing post'))
            db.add(Comment(post_id=2, user_id=1, content='Departing reply'))
            db.add(PostLike(post_id=1, user_id=2))
            db.add(Follow(follower_id=1, following_id=2))
            db.add(Follow(follower_id=2, following_id=1))
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api/users')
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': f'Bearer {create_access_token({"sub": "1"})}'}

    def remove(self, password='secret'):
        return self.client.request('DELETE', '/api/users/me', headers=self.headers, json={'passwd': password})

    def counts(self):
        with self.Session() as db:
            return {table.name: db.scalar(select(func.count()).select_from(table)) for table in Base.metadata.sorted_tables}

    def test_deletes_dependencies_preserves_other_user_and_revokes_token(self):
        self.assertEqual(self.remove().status_code, 204)
        with self.Session() as db:
            self.assertIsNone(db.get(User, 1))
            self.assertIsNotNone(db.get(User, 2))
            for model in (Account, Order, OrderAutomation, Portfolio, MisuDebt, MisuPayment, MisuSettlement, Post, Comment, PostLike, UserSurveyResponse, AiPropensityAdvice):
                self.assertEqual(db.scalar(select(func.count()).select_from(model)), 1, model.__name__)
            self.assertEqual(db.scalar(select(func.count()).select_from(Follow)), 0)
        self.assertEqual(self.client.get('/api/users/me', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.post('/api/users/login', json={'login_id': 'user1', 'passwd': 'secret'}).status_code, 401)

    def test_invalid_password_and_unauthenticated_request_delete_nothing(self):
        before = self.counts()
        self.assertEqual(self.remove('wrong').status_code, 400)
        self.assertEqual(self.remove('').status_code, 422)
        self.assertIn(self.client.request('DELETE', '/api/users/me', json={'passwd': 'secret'}).status_code, (401, 403))
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.get('/api/users/me', headers=self.headers).status_code, 200)

    def test_commit_failure_rolls_back_all_deletions(self):
        before = self.counts()
        with patch('sqlalchemy.orm.Session.commit', side_effect=RuntimeError('Simulated failure')):
            self.assertEqual(self.remove().status_code, 500)
        self.assertEqual(self.counts(), before)


if __name__ == '__main__':
    unittest.main()
