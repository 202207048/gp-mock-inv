"""Persistent survey reuse through the actual API, with no external AI calls."""
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ['DATABASE_URL'] = 'sqlite://'
os.environ['SECRET_KEY'] = 'offline-survey-tests'

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base, get_db
from app.models import User
from app.models.ai_propensity_advice import AiPropensityAdvice
from app.models.user_survey_response import UserSurveyResponse
from app.routers import ai as routes
from app.services.ai_service import _FALLBACK
from app.utils.deps import get_current_user

RESULT = dict(investor_type='위험중립형', risk_score=50, summary='기존 성향 설명',
              advice='기존 투자 조언', learning_roadmap=['분산 투자', '투자 기록', '위험 관리'])
ANSWERS = [dict(question_number=n, selected_answer=f'선택지 {n}') for n in range(1, 7)]


class SurveyReuseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([User(user_id=n, login_id=f'user{n}', passwd='unused', user_name='test', email=f'{n}@test.example') for n in (1, 2)])
            db.commit()
        app = FastAPI()
        app.include_router(routes.router, prefix='/ai')
        def session():
            with self.Session() as db:
                yield db
        self.user_id = 1
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=self.user_id)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.engine.dispose)
        mock = patch.object(routes, 'analyze_survey_answers', return_value=RESULT.copy())
        self.analyze = mock.start()
        self.addCleanup(mock.stop)

    def submit(self, answers=ANSWERS):
        response = self.client.post('/ai/survey', json={'answers': answers})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_identical_answers_return_original_content_without_writes_or_ai(self):
        first = self.submit()
        before = self.client.get('/ai/survey').json()
        self.analyze.side_effect = AssertionError('same answers must not call AI')
        again = self.submit(list(reversed(ANSWERS)))
        self.assertFalse(first['reused'])
        self.assertTrue(again['reused'])
        self.assertEqual(again['analysis'], first['analysis'])
        self.assertEqual(again['investment_style'], first['investment_style'])
        self.assertEqual(self.client.get('/ai/survey').json(), before)
        self.assertEqual(self.client.get('/ai/propensity').json()['analysis'], RESULT)
        with self.Session() as db:
            self.assertEqual(db.query(AiPropensityAdvice).count(), 1)
        self.assertEqual(self.analyze.call_count, 1)

    def test_changed_choice_reanalyzes_even_if_investor_type_is_same(self):
        self.submit()
        new_result = dict(RESULT, summary='변경된 선택지 분석')
        self.analyze.return_value = new_result
        changed = [dict(answer) for answer in ANSWERS]
        changed[0]['selected_answer'] = '다른 선택지'
        result = self.submit(changed)
        self.assertFalse(result['reused'])
        self.assertEqual(result['analysis'], new_result)
        self.assertTrue(self.submit(changed)['reused'])
        self.assertEqual(self.analyze.call_count, 2)

    def test_missing_invalid_or_failed_result_is_regenerated(self):
        for raw in (None, 'legacy plain text', '[]', '{}', json.dumps(_FALLBACK)):
            with self.subTest(raw=raw):
                with self.Session() as db:
                    db.query(AiPropensityAdvice).delete()
                    if raw is not None:
                        db.add(AiPropensityAdvice(user_id=1, ai_analysis_result=RESULT['investor_type'], ai_detailed_advice=raw))
                    db.commit()
                self.assertFalse(self.submit()['reused'])
        self.assertEqual(self.analyze.call_count, 5)

    def test_results_are_scoped_to_current_user(self):
        self.submit()
        self.user_id = 2
        self.assertFalse(self.submit()['reused'])
        self.assertEqual(self.analyze.call_count, 2)
        with self.Session() as db:
            self.assertEqual(db.query(AiPropensityAdvice).count(), 2)
            self.assertEqual(db.query(UserSurveyResponse).count(), 12)
