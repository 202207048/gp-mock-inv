import json
import unittest
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base, get_db
from app.models import Account, ItemMaster, Portfolio, User
from app.routers import ai_asset_coach as routes
from app.schemas.asset_coach import AssetCoachAdvice, unsafe
from app.services import asset_coach_service as coach
from app.services import kis_service
from app.services.asset_history_service import KST
from app.utils.deps import get_current_user

RealClient = httpx.AsyncClient
ADVICE = {'headline': '150원을 서둘러 만회하기보다, 몰린 비중부터 살펴봐요.', 'intro': '손실이 조금 있어요. 아래를 확인해 보세요.',
          'checkpoints': [{'title': '남은 현금 1,000원은 일단 그대로 둬요', 'body': '처음 산 이유를 돌아보세요.', 'detail': None},
                          {'title': 'A전자·B전자를 먼저 점검해요', 'body': 'A전자 비중이 55.2%예요.',
                           'detail': '계좌 전체의 주식 비중과 현금 비중을 검토해 보세요.'},
                          {'title': '감당할 수 있는 손실을 금액으로 정해요', 'body': '평가금액 1,450원이 10% 더 내리면 145원이 줄어요.',
                           'detail': '하락을 예측하는 말이 아니라 예시예요.'}],
          'takeaway': '모의투자인 지금은 기준을 세우는 연습에 집중해 보세요.',
          'emphasis': ['A전자 비중이 55.2%', '기준을 세우는 연습'],
          'disclaimer': '모의투자 연습용 설명이며 투자 권유가 아닙니다.'}
# 한 줄 정리·덧붙임·굵게 표시가 없는 이전 형식의 AI 서버 응답.
PLAIN_ADVICE = {'headline': ADVICE['headline'], 'intro': ADVICE['intro'], 'disclaimer': ADVICE['disclaimer'],
                'checkpoints': [{'title': item['title'], 'body': item['body']} for item in ADVICE['checkpoints']]}
PRICES = {'A': 80, 'B': 110, 'C': 50}


class AssetCoachTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            for i in (1, 2, 3):
                db.add(User(user_id=i, login_id=str(i), passwd='fixture', email=f'{i}@example.invalid', user_name='fixture'))
            db.add(Account(account_id=1, user_id=1, withdrawable_cash=1000))
            db.add(Account(account_id=2, user_id=2))
            db.add(Account(account_id=3, user_id=1))
            for code in ('A', 'B', 'C'):
                db.add(ItemMaster(symbol_code=code, name=code + '전자', market_type='국내주식'))
            db.add(Portfolio(account_id=1, symbol_code='A', avg_price=100, hold_quantity=10))
            db.add(Portfolio(account_id=1, symbol_code='B', avg_price=100, hold_quantity=5))
            db.add(Portfolio(account_id=1, symbol_code='C', avg_price=50, hold_quantity=2))
            db.add(Portfolio(account_id=2, symbol_code='A', avg_price=100, hold_quantity=1))
            db.add(Portfolio(account_id=3, symbol_code='A', avg_price=100, hold_quantity=0))
            db.commit()
        self.generate = AsyncMock(return_value=AssetCoachAdvice.model_validate(ADVICE))
        coach.service = coach.AssetCoachService(generate=self.generate, now=lambda: datetime(2026, 11, 10, 15, 0, tzinfo=KST))
        routes.service = coach.service
        app = FastAPI()
        app.include_router(routes.router, prefix='/api/ai')

        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.prices = dict(PRICES)
        price = AsyncMock(side_effect=lambda code: {'current_price': self.prices[code]})
        for p in (patch.object(kis_service, 'get_current_price', price),
                  patch.object(settings, 'AI_SERVER_URL', 'https://ai.test'),
                  patch.object(settings, 'AI_SERVICE_TOKEN', SecretStr('fixture-service'))):
            p.start()
            self.addCleanup(p.stop)

    def post(self, account_id=1):
        return self.client.post('/api/ai/asset-coach', json={'account_id': account_id})

    def test_facts_are_computed_from_the_account(self):
        self.assertEqual(self.post().status_code, 200)
        facts = self.generate.call_args.args[0]
        self.assertEqual((facts.cost_total, facts.market_total, facts.unrealized_pnl), (1600, 1450, -150))
        self.assertEqual((facts.tone, facts.severity, facts.return_pct), ('loss', 'moderate', Decimal('-9.38')))
        self.assertEqual((facts.holdings_count, facts.losing_count, facts.gaining_count), (3, 1, 1))
        self.assertEqual([(h.name, h.weight_pct) for h in facts.top_holdings],
                         [('A전자', Decimal('55.17')), ('B전자', Decimal('37.93')), ('C전자', Decimal('6.90'))])
        self.assertEqual(facts.concentration, 'moderate')
        self.assertEqual(facts.cash_weight_pct, Decimal('40.82'))
        self.assertIsNone(facts.top3_weight_pct)
        self.assertEqual((facts.worst_holding.name, facts.worst_holding.return_pct), ('A전자', Decimal('-20.00')))
        self.assertEqual((facts.best_holding.name, facts.best_holding.pnl), ('B전자', 50))
        # 위험 가늠 예시: 평가금액 1,450원의 10%.
        self.assertEqual((facts.stress_drop_pct, facts.stress_drop_amount), (10, 145))

    def test_response_and_cache(self):
        first = self.post().json()
        self.assertEqual((first['status'], first['tone'], first['cached']), ('ok', 'loss', False))
        self.assertEqual(len(first['checkpoints']), 3)
        self.assertEqual([item['detail'] for item in first['checkpoints']],
                         [None, '계좌 전체의 주식 비중과 현금 비중을 검토해 보세요.', '하락을 예측하는 말이 아니라 예시예요.'])
        self.assertEqual((first['takeaway'], first['emphasis']), (ADVICE['takeaway'], ADVICE['emphasis']))
        self.assertTrue(self.post().json()['cached'])
        self.assertEqual(self.generate.call_count, 1)
        # 같은 1%p 구간 안의 시세 변화는 저장본을 쓰고, 구간을 넘거나 보유가 바뀌면 다시 만든다.
        self.prices['C'] = 51
        self.assertTrue(self.post().json()['cached'])
        self.prices['A'] = 95
        self.assertFalse(self.post().json()['cached'])
        with self.Session() as db:
            db.query(Portfolio).filter_by(account_id=1, symbol_code='C').update({'hold_quantity': 3})
            db.commit()
        self.assertFalse(self.post().json()['cached'])
        self.assertEqual(self.generate.call_count, 3)

    def test_answer_without_the_newer_fields_still_works(self):
        self.generate.return_value = AssetCoachAdvice.model_validate(PLAIN_ADVICE)
        body = self.post().json()
        self.assertEqual((body['status'], body['takeaway'], body['emphasis']), ('ok', None, []))
        self.assertEqual([item['detail'] for item in body['checkpoints']], [None, None, None])

    def test_failed_generation_is_not_cached(self):
        self.generate.side_effect = [coach.HTTPException(502, 'x'), AssetCoachAdvice.model_validate(ADVICE)]
        self.assertEqual(self.post().status_code, 502)
        self.assertFalse(self.post().json()['cached'])

    def test_profit_and_flat_accounts(self):
        self.prices.update(A=130, B=100, C=50)
        self.post()
        facts = self.generate.call_args.args[0]
        self.assertEqual((facts.tone, facts.unrealized_pnl, facts.severity, facts.worst_holding), ('profit', 300, 'large', None))
        self.assertEqual(facts.concentration, 'concentrated')
        self.prices.update(A=100, B=100, C=50)
        self.post()
        facts = self.generate.call_args.args[0]
        self.assertEqual((facts.tone, facts.severity, facts.best_holding, facts.worst_holding), ('flat', 'small', None, None))

    def test_other_account_empty_holdings_and_price_failure(self):
        self.assertEqual(self.post(2).status_code, 404)
        self.assertEqual(self.post(3).json(), {'status': 'no_holdings'})
        self.prices['B'] = 0
        self.assertEqual(self.post().status_code, 503)
        self.assertEqual(self.generate.call_count, 0)
        self.assertEqual(self.client.post('/api/ai/asset-coach', json={'account_id': 1, 'extra': 1}).status_code, 422)

    def test_zero_cost_basis_has_no_return_rate(self):
        with self.Session() as db:
            db.query(Portfolio).filter_by(account_id=1).update({'avg_price': 0})
            db.commit()
        self.post()
        facts = self.generate.call_args.args[0]
        self.assertIsNone(facts.return_pct)
        self.assertIsNone(facts.top_holdings[0].return_pct)


class AIServerBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.facts = coach.build_facts([('A', 'A전자', 10, Decimal(100))], {'A': Decimal(80)}, Decimal(0),
                                       datetime(2026, 11, 10, 15, 0, tzinfo=KST))
        for p in (patch.object(settings, 'AI_SERVER_URL', 'https://ai.test'),
                  patch.object(settings, 'AI_SERVICE_TOKEN', SecretStr('fixture-service'))):
            p.start()
            self.addCleanup(p.stop)

    async def call(self, status, body):
        seen = []

        def handler(request):
            seen.append(request)
            return httpx.Response(status, json=body)
        with patch.object(coach.httpx, 'AsyncClient', lambda **kw: RealClient(transport=httpx.MockTransport(handler), **kw)):
            try:
                return await coach.request_coach(self.facts), seen
            except coach.HTTPException as exc:
                return exc.status_code, seen

    async def test_success_sends_only_account_facts(self):
        advice, seen = await self.call(200, {**ADVICE, 'future_field': 1})
        self.assertEqual(advice.headline, ADVICE['headline'])
        self.assertEqual((advice.takeaway, advice.checkpoints[2].detail), (ADVICE['takeaway'], '하락을 예측하는 말이 아니라 예시예요.'))
        payload_facts = json.loads(seen[0].content)['facts']
        self.assertEqual((payload_facts['stress_drop_pct'], payload_facts['stress_drop_amount']), ('10', '80'))
        request = seen[0]
        self.assertEqual(str(request.url), 'https://ai.test/brief/asset-coach')
        self.assertEqual(request.headers['authorization'], 'Bearer fixture-service')
        payload = json.loads(request.content)
        self.assertEqual(set(payload), {'facts'})
        self.assertNotIn('symbol', json.dumps(payload))
        self.assertNotIn('account', json.dumps(payload))

    async def test_error_mapping(self):
        self.assertEqual((await self.call(503, {}))[0], 503)
        self.assertEqual((await self.call(500, {}))[0], 502)
        self.assertEqual((await self.call(200, {**ADVICE, 'headline': 'A전자를 매수하세요.'}))[0], 502)
        self.assertEqual((await self.call(200, {**ADVICE, 'checkpoints': ADVICE['checkpoints'][:2]}))[0], 502)
        self.assertEqual((await self.call(200, {**ADVICE, 'takeaway': '지금 사세요.'}))[0], 502)
        risky = [dict(ADVICE['checkpoints'][0], detail='A전자는 손절하세요.'), *ADVICE['checkpoints'][1:]]
        self.assertEqual((await self.call(200, {**ADVICE, 'checkpoints': risky}))[0], 502)

    async def test_emphasis_is_limited_to_phrases_in_the_body(self):
        advice, _ = await self.call(200, {**ADVICE, 'emphasis': ['A전자 비중이 55.2%', '본문에 없는 말', '<b>굵게</b>', '150원을 서둘러']})
        self.assertEqual(advice.emphasis, ['A전자 비중이 55.2%'])
        advice, _ = await self.call(200, PLAIN_ADVICE)
        self.assertEqual((advice.takeaway, advice.emphasis), (None, []))
        with patch.object(settings, 'AI_SERVICE_TOKEN', SecretStr('')):
            self.assertEqual((await self.call(200, ADVICE))[0], 503)


class SecondCheckTests(unittest.TestCase):
    """화면에 나가기 전 백엔드가 한 번 더 보는 규칙."""

    def test_ordinary_coaching_sentences_pass(self):
        for text in ('처음 세운 목표가 무엇이었는지 돌아보세요.',
                     'A전자 매수 이유를 다시 확인해요',
                     '본전 회복보다 매수·보유·매도 기준을 세우고 기록하는 연습을 해 봐요.',
                     '보유한 종목을 계속 보유할 이유가 있는지부터 확인해 보세요.',
                     '보유 종목마다 산 이유를 정리해 보세요. 기준을 정리하세요.',
                     '그 금액도 부담스럽다면 계좌 전체의 주식 비중과 현금 비중을 어떻게 둘지 검토해 보세요.',
                     '평가금액 1,450원이 여기서 10% 더 내리면 145원이 줄어요.',
                     '처음 세운 목표가 무엇이었는지 돌아보시기 바랍니다. 기준을 정리하십시오.'):
            self.assertFalse(unsafe(text), text)

    def test_buy_pushes_orders_and_pointed_sell_advice_are_blocked(self):
        for text in ('현금으로 추가 매수해 보세요.', '물타기를 해 보는 것도 방법이에요.', 'A전자를 더 담아 보세요.',
                     '부담스럽다면 주식 비중을 줄이세요.', '부담스럽다면 주식을 정리하세요.', '일부를 현금화하세요.',
                     '주식을 일부 팔아 현금을 확보하세요.', '비중이 가장 큰 종목은 일부 매도를 검토해요.',
                     '한 종목 비중을 줄여 보세요.', '손실 종목은 먼저 정리해 보세요.',
                     'A전자의 목표가를 다시 확인해 보세요.', '목표 주가에 닿았는지 확인해 보세요.',
                     '평가금액이 10% 오르면 145원이 늘어요.', 'A전자를 지금 매수하세요.',
                     '부담스럽다면 주식 비중을 줄이십시오.', '주식을 정리하시기 바랍니다.', '현금으로 더 사 보시기 바랍니다.',
                     'A전자를 지금 매수하십시오.', '지금은 손절하시기 바랍니다.'):
            self.assertTrue(unsafe(text), text)

    def test_each_field_is_checked_on_its_own(self):
        # 제목의 '종목'과 본문의 '매도 기준'이 이어져 한 문장처럼 걸리지 않는다.
        ok = dict(ADVICE, checkpoints=[dict(ADVICE['checkpoints'][0], title='A전자 한 종목을 먼저 점검해요',
                                           body='매도 시점은 스스로 정해 보세요.'), *ADVICE['checkpoints'][1:]])
        AssetCoachAdvice.model_validate(ok)
        bad = dict(ADVICE, checkpoints=[dict(ADVICE['checkpoints'][0], detail='손실 종목은 먼저 정리해 보세요.'),
                                        *ADVICE['checkpoints'][1:]])
        with self.assertRaises(ValueError):
            AssetCoachAdvice.model_validate(bad)


if __name__ == '__main__':
    unittest.main()
