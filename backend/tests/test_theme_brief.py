import asyncio
from datetime import datetime,timedelta
from decimal import Decimal
from unittest import IsolatedAsyncioTestCase,TestCase
from unittest.mock import AsyncMock,patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services.theme_service import select_themes,select_leaders,KST
from app.services.theme_brief_service import ThemeBriefService,ThemeUnavailable,recent_headlines,collect_headlines,request_themes
from app.schemas.theme_brief import Theme,Leader,ThemePublicResponse,Evidence
from app.routers.ai_theme import router
from app.utils.deps import get_current_user
from pydantic import SecretStr
import httpx

def group(i,rate=2,rise=8,fall=2,total=10):return dict(no=i,name='가상테마',changeRate=str(rate),riseCount=rise,fallCount=fall,steadyCount=0,totalCount=total)
def snapshot():return ThemePublicResponse(as_of=datetime.now(KST),generated_at=datetime.now(KST),rising=[Theme(id='1',name='가상테마',change_rate=2,rise_count=8,fall_count=2,steady_count=0,total_count=10,leaders=[Leader(code='000001',name='가상전자',price=100,change_rate=3)])],falling=[])
def evidence():return [Evidence(title='가상전자 공급계약 체결',source='가상신문',url='https://example.invalid/news',published_at=datetime.now(KST))]
def generated():return {'themes':[{'id':'1','reason':'가상전자 공급계약 체결 소식이 있어요.','headline_ids':['0']}], 'provider':'private','usage':{}}

class SelectionTests(TestCase):
 def test_selection_majority_size_and_limits(self):
  rising,falling=select_themes([group('security',4.92,3,18,21),group('small',8,3,0,4),group('zero',0)]+[group(str(i),i+1) for i in range(5)]+[group('f'+str(i),-i-1,2,8) for i in range(5)])
  self.assertEqual([r.id for r in rising],['4','3','2']);self.assertEqual([r.id for r in falling],['f4','f3','f2'])
  self.assertEqual(select_themes([]),([],[]))
 def test_leaders_and_decimal(self):
  stocks=[dict(itemCode=str(i),stockName='가상',closePrice='1,913',fluctuationsRatio=str(i-2)) for i in range(5)]
  self.assertEqual([x.code for x in select_leaders(stocks,'rising')],['4','3','2'])
  self.assertEqual([x.code for x in select_leaders(stocks,'falling')],['0','1','2'])
  self.assertEqual(select_leaders(stocks,'rising')[0].price,Decimal('1913'))
 def test_dates_dedup_and_limits(self):
  now=datetime(2026,10,30,15,tzinfo=KST)
  rows=[dict(title=str(i),source='뉴스',url=None,date='2026.10.30 14:00') for i in range(10)]
  rows +=[rows[0],dict(title='old',date='2026.09.01 00:00'),dict(title='future',date='2026.11.01 00:00')]
  out=recent_headlines(rows,now);self.assertEqual(len(out),8);self.assertEqual(len({x.title for x in out}),8)
  self.assertNotIn('old',[x.title for x in out])
 def test_auth_required(self):
  app=FastAPI();app.include_router(router,prefix='/api/ai')
  from fastapi import HTTPException
  def denied():raise HTTPException(401)
  app.dependency_overrides[get_current_user]=denied
  with TestClient(app) as c:self.assertEqual(c.get('/api/ai/theme-brief').status_code,401)

class ServiceTests(IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.now=0;self.fetch=AsyncMock(side_effect=lambda:snapshot());self.collect=AsyncMock(side_effect=lambda *a:evidence())
  self.generate=AsyncMock(side_effect=lambda *a:generated())
  self.service=ThemeBriefService(self.fetch,self.collect,self.generate,lambda:self.now)
 async def test_concurrent_cache_and_expiry(self):
  values=await asyncio.gather(*(self.service.get() for _ in range(10)))
  self.generate.assert_awaited_once();self.assertTrue(values[0].rising[0].reason)
  self.assertNotIn('provider',values[0].model_dump_json())
  self.now=1799;await self.service.get();self.assertEqual(self.generate.await_count,1)
  self.now=1800;await self.service.get();self.assertEqual(self.generate.await_count,2)
 async def test_ai_failure_five_minute_cache_but_thirty_minute_budget(self):
  self.generate.side_effect=RuntimeError('fixture')
  self.assertIsNone((await self.service.get()).rising[0].reason)
  self.now=299;await self.service.get();self.assertEqual(self.fetch.await_count,1)
  self.now=300;await self.service.get();self.assertEqual(self.fetch.await_count,2);self.assertEqual(self.generate.await_count,1)
  self.now=1800;await self.service.get();self.assertEqual(self.generate.await_count,2)
 async def test_naver_stale_then_unavailable(self):
  await self.service.get();self.fetch.side_effect=RuntimeError('offline')
  self.now=1800;self.assertTrue((await self.service.get()).rising)
  self.now=3601
  with self.assertRaises(ThemeUnavailable):await self.service.get()
  with self.assertRaises(ThemeUnavailable):await ThemeBriefService(self.fetch).get()
 async def test_no_headlines_no_generation(self):
  self.collect.return_value=[];self.collect.side_effect=None
  value=await self.service.get();self.generate.assert_not_awaited();self.assertIsNone(value.rising[0].reason)
 async def test_collect_at_most_five_each_and_eight_unique(self):
  theme=snapshot().rising[0];theme.leaders=theme.leaders*3
  now=datetime.now(KST)
  rows=[dict(title=str(i),source='뉴스',url=None,date=now.strftime('%Y.%m.%d %H:%M')) for i in range(10)]
  with patch('app.services.theme_brief_service.get_news_by_symbol',AsyncMock(return_value=rows)) as call:
   out=await collect_headlines(theme,now)
  self.assertEqual(len(out),5);self.assertEqual(call.await_count,3)
 async def test_request_service_auth_timeout_and_public_route(self):
  real=httpx.AsyncClient
  def handler(req):
   self.assertEqual(req.headers['Authorization'],'Bearer fixture')
   return httpx.Response(503,json={})
  from app.services.theme_brief_service import service as global_service
  from app.config import settings
  from app.schemas.theme_brief import ThemeBriefRequest,ThemeInput,Headline,LeaderInput
  request=ThemeBriefRequest(themes=[ThemeInput(id='1',name='테마',direction='rising',change_rate=2,rise_count=5,fall_count=1,total_count=6,leaders=[],headlines=[Headline(id='0',title='기사',published_at=datetime.now(KST))])])
  with patch.object(settings,'AI_SERVICE_TOKEN',SecretStr('fixture')),patch.object(settings,'AI_SERVER_URL','https://ai.test'),patch('app.services.theme_brief_service.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
   with self.assertRaises(httpx.HTTPStatusError):await request_themes(request)
  app=FastAPI();app.include_router(router,prefix='/api/ai');app.dependency_overrides[get_current_user]=lambda:object()
  with patch.object(global_service,'get',AsyncMock(return_value=snapshot())):
   with TestClient(app) as c:self.assertEqual(c.get('/api/ai/theme-brief').status_code,200)
