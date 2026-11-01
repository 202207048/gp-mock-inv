import asyncio
from datetime import datetime,timedelta
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,patch
import httpx
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.schemas.stock_brief import StockBriefRequest,StockInput,RelatedStock
from app.services.stock_brief_service import StockBriefService,StockUnavailable,article_inputs,select_related,KST,request_stock
from app.services.stock_brief_validation import sanitize_stock_output
from app.routers.ai_stock import router
from app.utils.deps import get_current_user

def quote():return dict(name='가상전자',price=123400,change_rate=-2.5,as_of=datetime.now(KST))
def articles():
 now=datetime.now(KST)
 return article_inputs([dict(title='실적 부진',date=now.isoformat(),source='신문',url=None)],'s-',now,now)
def generated():return dict(title='실적 부진',title_ids=['s-0'],recent_issues=[{'text':'실적 부진이 보도됐습니다.','ids':['s-0']}],positive_factors=[],risk_factors=[],related=[])
def request():return StockBriefRequest(stock=StockInput(code='000001',name='가상전자',change_rate=-2.5,explain_move=True,headlines=[h for h,e in articles().values()]),related=[],market_headlines=[])

class ServiceTests(IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.now=0;self.quote=AsyncMock(side_effect=lambda code:quote());self.related=AsyncMock(return_value=[])
  self.collect=AsyncMock(side_effect=lambda *a:(articles(),{},{}));self.generate=AsyncMock(side_effect=lambda r:generated())
  self.service=StockBriefService(self.quote,self.related,self.collect,self.generate,lambda:self.now)
 async def test_ttl_refresh_quote_only_then_expire(self):
  await self.service.get('000001');self.now=10799
  result=await self.service.get('000001');self.assertEqual(self.generate.await_count,1);self.assertEqual(self.quote.await_count,2)
  self.assertIs(type(result.model_dump(mode='json')['price']),int);self.assertIs(type(result.model_dump(mode='json')['change_rate']),float)
  self.now=10800;await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_concurrent_single_generation(self):
  started=asyncio.Event();release=asyncio.Event()
  async def generate(r):started.set();await release.wait();return generated()
  self.generate.side_effect=generate
  tasks=[asyncio.create_task(self.service.get('000001')) for _ in range(8)]
  await started.wait();release.set();await asyncio.gather(*tasks);self.generate.assert_awaited_once()
 async def test_failure_retry_after_five_minutes(self):
  self.generate.side_effect=RuntimeError('fake')
  self.assertFalse((await self.service.get('000001')).ai_available)
  self.now=299;await self.service.get('000001');self.generate.assert_awaited_once()
  self.now=300;await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_no_articles_no_generation_thirty_minutes(self):
  self.collect.side_effect=None;self.collect.return_value=({},{},{})
  await self.service.get('000001');self.now=1799;await self.service.get('000001');self.assertEqual(self.collect.await_count,1)
  self.now=1800;await self.service.get('000001');self.assertEqual(self.collect.await_count,2);self.generate.assert_not_awaited()
 async def test_direction_change_early_kept_later_regenerated(self):
  await self.service.get('000001');self.quote.side_effect=lambda c:{**quote(),'change_rate':2.5}
  self.now=1799;await self.service.get('000001');self.generate.assert_awaited_once()
  self.now=1800;await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_quote_failure_stale_or_unavailable(self):
  self.quote.side_effect=RuntimeError('fake')
  with self.assertRaises(StockUnavailable):await self.service.get('000001')
  self.quote.side_effect=lambda c:quote();old=await self.service.get('000001')
  self.quote.side_effect=RuntimeError('fake');self.now=20000
  self.assertEqual(await self.service.get('000001'),old)
 async def test_bounded_cache_and_independent_codes(self):
  for n in range(201):await self.service.get(f'{n:06}')
  self.assertEqual(len(self.service.cache),200);self.assertNotIn('000000',self.service.cache)
 async def test_service_header_and_timeout(self):
  from app.config import settings
  real=httpx.AsyncClient
  def handler(req):
   self.assertEqual(req.headers['Authorization'],'Bearer fixture');self.assertTrue(req.url.path.endswith('/brief/stock'))
   return httpx.Response(200,json=generated())
  with patch.object(settings,'AI_SERVICE_TOKEN',SecretStr('fixture')),patch.object(settings,'AI_SERVER_URL','http://local.test'),patch('app.services.stock_brief_service.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
   self.assertEqual((await request_stock(request()))['title'],'실적 부진')

class ValidationTests(TestCase):
 def test_title_rejection_isolated_from_lists(self):
  req=request()
  for change in ({'title':'가'*21},{'title_ids':['other']},{'title_ids':[]}):
   out=sanitize_stock_output({**generated(),**change},req);self.assertIsNone(out['title']);self.assertEqual(len(out['recent_issues']),1)
  req.stock.headlines[0].in_move_window=False
  self.assertIsNone(sanitize_stock_output(generated(),req)['title'])
  req=request();req.stock.change_rate=.99
  self.assertIsNone(sanitize_stock_output(generated(),req)['title'])
 def test_scope_limits_and_related_titles(self):
  req=request();h=req.stock.headlines[0].model_copy(update={'id':'m-0'})
  req.market_headlines=[h];req.related=[StockInput(code='000002',name='다른전자',change_rate=-2,explain_move=True,headlines=[h.model_copy(update={'id':'p000002-0'})])]
  data=generated();data['positive_factors']=[dict(text='시장 기사입니다.',ids=['m-0'])]
  data['risk_factors']=[dict(text=f'위험 {i}입니다.',ids=['s-0']) for i in range(5)]
  data['related']=[dict(code='000002',title='실적 부진',ids=['s-0'])]
  out=sanitize_stock_output(data,req)
  self.assertEqual(out['positive_factors'],[]);self.assertEqual(len(out['risk_factors']),3);self.assertIsNone(out['related'][0]['title'])
 def test_article_windows_speculative_filter(self):
  now=datetime(2026,11,1,12,tzinfo=KST);anchor=datetime(2026,10,30,15,tzinfo=KST)
  rows=[dict(title=t,date=d) for t,d in [('실적 발표','2026-10-30T10:00:00+09:00'),('주말 행사','2026-11-01T10:00:00+09:00'),('상승 전망','2026-10-30T10:00:00+09:00'),('오래된 뉴스','2026-10-20T10:00:00+09:00')]]
  out=article_inputs(rows,'s-',now,anchor)
  self.assertEqual(len(out),2);self.assertTrue(out['s-0'][0].in_move_window);self.assertFalse(out['s-1'][0].in_move_window)
 def test_related_selection(self):
  rows=[dict(itemCode=c,stockName=c,closePrice='1,000',fluctuationsRatio=str(v)) for c,v in [('000001',-10),('000002',3),('000003',-2),('000004',-4),('000005',-1),('000006',5)]]
  self.assertEqual([s.code for s in select_related(rows,'000001',-2)],['000004','000003','000005','000006'])
 def test_route_auth_code_and_unavailable(self):
  app=FastAPI();app.include_router(router,prefix='/api/ai')
  def denied():raise HTTPException(401)
  app.dependency_overrides[get_current_user]=denied
  with TestClient(app) as c:self.assertEqual(c.get('/api/ai/stock-brief/000001').status_code,401)
  app.dependency_overrides[get_current_user]=lambda:object()
  with patch('app.routers.ai_stock.service.get',AsyncMock(side_effect=StockUnavailable())):
   with TestClient(app) as c:
    self.assertEqual(c.get('/api/ai/stock-brief/abc').status_code,400)
    self.assertEqual(c.get('/api/ai/stock-brief/000001').status_code,503)


class CollectionTests(IsolatedAsyncioTestCase):
 async def test_naver_quote_and_integration_contract(self):
  from app.services.stock_brief_service import fetch_quote,fetch_related
  real=httpx.AsyncClient
  def handler(req):
   if req.url.path.endswith('/basic'):return httpx.Response(200,json=dict(stockName='가상전자',closePrice='1,391,000',fluctuationsRatio='-8.85',localTradedAt='2026-10-30T20:20:23+09:00'))
   return httpx.Response(200,json={'industryCompareInfo':[dict(itemCode='000002',stockName='다른전자',closePrice='1,000',fluctuationsRatio='-4.0')]})
  with patch('app.services.stock_brief_service.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
   self.assertEqual((await fetch_quote('000001'))['price'],1391000)
   self.assertEqual((await fetch_related('000001',-8.85))[0].code,'000002')
 async def test_collection_concurrent_and_contract(self):
  from app.services.stock_brief_service import collect_articles
  from app.schemas.theme_brief import MarketContext
  started=0;release=asyncio.Event();now=datetime.now(KST)
  async def news(code,count):
   nonlocal started
   started+=1
   if started==2:release.set()
   await release.wait()
   return [dict(title='실적 발표',date=now.isoformat(),source=None,url=None)]
  with patch('app.services.stock_brief_service.get_news_by_symbol',side_effect=news) as calls,patch('app.services.stock_brief_service.collect_market_context',AsyncMock(return_value=(MarketContext(),{}))):
   own,peers,market=await collect_articles('000001',[RelatedStock(code='000002',name='다른전자',price=100,change_rate=-2)],now,now)
  self.assertEqual(calls.await_count,2);self.assertIn('s-0',own);self.assertIn('p000002-0',peers['000002'])
  import importlib.util
  from pathlib import Path
  spec=importlib.util.spec_from_file_location('stock_contract',Path(__file__).resolve().parents[2]/'ai_server/app/stock_brief_schemas.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  self.assertEqual(module.StockBriefRequest.model_validate(request().model_dump(mode='json')).stock.code,'000001')
