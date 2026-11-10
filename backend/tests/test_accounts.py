"""Account numbers, creation validation and ownership stay consistent."""
import os
import unittest
from unittest.mock import patch
os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'accounts-offline-tests'
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import Account, User
from app.routers.accounts import router
from app.utils.deps import get_current_user
from app.services.auth_service import register_user
from app.schemas.auth import RegisterRequest


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.Session() as db:
            for uid in (1, 2):
                db.add(User(user_id=uid, login_id=f'user{uid}', passwd='hash', user_name='Tester', email=f'u{uid}@example.com'))
            db.flush()
            db.add_all([Account(user_id=1, account_name='Legacy default', balance=10000000, withdrawable_cash=10000000), Account(user_id=2, account_name='Other', balance=100000, withdrawable_cash=100000)])
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/accounts')
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: User(user_id=1)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_existing_and_new_accounts_have_stable_unique_numbers(self):
        legacy = self.client.get('/accounts').json()[0]
        self.assertEqual(legacy['account_number'], 'SM-0000-0000-0001')
        numbers = {legacy['account_number']}
        for amount in (100000, 12000000, 100000000):
            response = self.client.post('/accounts', json={'account_name': ' Long term ', 'initial_balance': amount})
            self.assertEqual(response.status_code, 200, response.text)
            row = response.json()
            self.assertEqual(row['account_name'], 'Long term')
            self.assertEqual(float(row['balance']), amount)
            self.assertEqual(float(row['withdrawable_cash']), amount)
            self.assertNotIn(row['account_number'], numbers)
            numbers.add(row['account_number'])
            self.assertEqual(self.client.get(f"/accounts/{row['account_id']}").json()['account_number'], row['account_number'])
        self.assertEqual(self.client.get('/accounts').json()[0], legacy)
        self.assertEqual(self.client.get('/accounts/2').status_code, 404)

    def test_invalid_amounts_and_names_do_not_create_accounts(self):
        for amount in (0, -1, 99999, 100000001, '100000.5', 'NaN', 'Infinity'):
            self.assertEqual(self.client.post('/accounts', json={'initial_balance': amount}).status_code, 422)
        for name in (' ', '', 'x' * 101):
            self.assertEqual(self.client.post('/accounts', json={'account_name': name}).status_code, 422)
        self.assertEqual(len(self.client.get('/accounts').json()), 1)

    def test_signup_default_gets_number_without_special_backfill(self):
        with self.Session() as db, patch('app.services.auth_service.hash_password', return_value='hash'):
            user = register_user(db, RegisterRequest(login_id='newuser', passwd='secret', user_name='New', email='new@example.com'))
            row = db.query(Account).filter_by(user_id=user.user_id).one()
            self.assertRegex(row.account_number, r'^SM-\d{4}-\d{4}-\d{4}$')
            before = row.account_number
            row.account_name = 'Renamed'
            db.commit(); db.refresh(row)
            self.assertEqual(row.account_number, before)

    def test_authentication_required(self):
        del self.client.app.dependency_overrides[get_current_user]
        self.assertIn(self.client.post('/accounts', json={}).status_code, (401, 403))
