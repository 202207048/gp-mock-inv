"""Public availability checks and registration-time duplicate protection."""
import os
import unittest
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'availability-offline-tests'

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import User
from app.routers.auth import router


class LoginIdAvailabilityTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.Session = sessionmaker(bind=engine)
        with self.Session() as db:
            db.add(User(login_id='existing', passwd='hash', user_name='Existing', email='existing@example.com'))
            db.commit()
        app = FastAPI()
        app.include_router(router, prefix='/api/users')
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def check(self, login_id):
        return self.client.get('/api/users/check-login-id', params={'login_id': login_id})

    def test_public_check_exposes_only_id_and_availability_and_is_not_cached(self):
        for login_id, available in [('existing', False), ('new_user', True), ('Existing', True)]:
            response = self.check(login_id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {'login_id': login_id, 'available': available})
            self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_invalid_ids_rejected_by_check_and_registration(self):
        for login_id in ['', 'abc', 'a' * 21, '한글아이디', 'user name', 'user!']:
            with self.subTest(login_id=login_id):
                self.assertEqual(self.check(login_id).status_code, 422)
                response = self.client.post('/api/users/register', json={
                    'login_id': login_id, 'passwd': 'testpass', 'user_name': 'Test', 'email': 'test@example.com'})
                self.assertEqual(response.status_code, 422)

    def test_availability_does_not_reserve_id_and_registration_rechecks(self):
        self.assertTrue(self.check('new_user').json()['available'])
        payload = {'login_id': 'new_user', 'passwd': 'testpass', 'user_name': 'Test', 'email': 'new@example.com'}
        with patch('app.services.auth_service.hash_password', return_value='test-hash'):
            self.assertEqual(self.client.post('/api/users/register', json=payload).status_code, 200)
        self.assertFalse(self.check('new_user').json()['available'])
        response = self.client.post('/api/users/register', json={**payload, 'email': 'other@example.com'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('아이디', response.json()['detail'])


if __name__ == '__main__':
    unittest.main()
