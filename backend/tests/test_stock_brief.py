import asyncio
import json
from datetime import datetime,timedelta
from unittest import TestCase, IsolatedAsyncioTestCase
from unittest.mock import AsyncMock,patch
import httpx
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.schemas.stock_brief import StockBriefRequest,StockBriefResponse,StockInput,RelatedStock
from app.services.stock_brief_service import StockBriefService,StockUnavailable,article_inputs,select_related,KST,request_stock
from app.services.stock_brief_validation import price_talk,sanitize_stock_output
from app.routers.ai_stock import router
from app.utils.deps import get_current_user

def quote():return dict(name='가상전자',price=123400,change_rate=-2.5,as_of=datetime.now(KST))
def articles():
 now=datetime.now(KST)
 return article_inputs([dict(title='실적 부진',date=now.isoformat(),source='신문',url=None)],'s-',now,now)
def generated():return dict(title='실적 부진',title_ids=['s-0'],banner_summary='분기 실적 부진 확인',banner_ids=['s-0'],banner_tone='악재',recent_issues=[{'text':'실적 부진이 보도됐습니다.','ids':['s-0']}],positive_factors=[],risk_factors=[],related=[])
def request():return StockBriefRequest(stock=StockInput(code='000001',name='가상전자',change_rate=-2.5,explain_move=True,headlines=[h for h,e in articles().values()]),related=[],market_headlines=[])

class ServiceTests(IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.now=0;self.quote=AsyncMock(side_effect=lambda code:quote());self.related=AsyncMock(return_value=[])
  self.collect=AsyncMock(side_effect=lambda *a:(articles(),{},{}));self.generate=AsyncMock(side_effect=lambda r:generated())
  self.service=StockBriefService(self.quote,self.related,self.collect,self.generate,lambda:self.now,load=lambda code:None,save=lambda *args:None)
 async def test_sentence_citations_preserve_only_the_used_articles_and_persist(self):
  saved=[]
  self.service.save=lambda code,payload:saved.append(payload)
  own=articles()
  headline,article=own['s-0']
  own['s-1']=(headline.model_copy(update={'id':'s-1'}),article.model_copy(update={'title':'Second article'}))
  self.collect.side_effect=None;self.collect.return_value=(own,{},{})
  data=generated()
  data['positive_factors']=[{'text':'Second sentence.', 'ids':['s-1']}]
  self.generate.side_effect=None;self.generate.return_value=data
  result=await self.service.get('000001')
  self.assertEqual(result.citations[data['recent_issues'][0]['text']],[article])
  self.assertEqual([item.title for item in result.citations['Second sentence.']],['Second article'])
  self.assertEqual(saved[0]['citations'],result.model_dump(mode='json')['citations'])
 async def test_saved_snapshot_without_sentence_citations_is_regenerated(self):
  result=await self.service.get('000001')
  payload=result.model_dump(mode='json');payload.pop('citations')
  self.service.cache.clear()
  self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001')
  self.assertEqual(self.generate.await_count,2)
 async def test_banner_summary_is_copied_with_its_article_and_persisted(self):
  saved=[]
  self.service.save=lambda code,payload:saved.append(payload)
  own=articles()
  headline,article=own['s-0']
  own['s-1']=(headline.model_copy(update={'id':'s-1','title':'신규 공급계약 체결'}),article.model_copy(update={'title':'신규 공급계약 체결'}))
  self.collect.side_effect=None;self.collect.return_value=(own,{},{})
  self.generate.side_effect=None;self.generate.return_value={**generated(),'banner_summary':'고객사와 신규 공급계약','banner_ids':['s-1']}
  result=await self.service.get('000001')
  self.assertEqual(result.banner_summary,'고객사와 신규 공급계약');self.assertEqual(result.title,'실적 부진')
  self.assertEqual([e.title for e in result.evidence],['실적 부진','신규 공급계약 체결'])
  self.assertEqual(saved[0]['banner_summary'],'고객사와 신규 공급계약')
 async def test_banner_detail_is_copied_and_old_snapshots_without_it_are_regenerated(self):
  saved=[]
  self.service.save=lambda code,payload:saved.append(payload)
  text='가상전자의 분기 실적 부진이 공식 확인됐습니다.'
  self.generate.side_effect=None;self.generate.return_value={**generated(),'banner_detail':text}
  result=await self.service.get('000001')
  self.assertEqual(result.banner_detail,text);self.assertEqual(saved[0]['banner_detail'],text)
  payload=result.model_dump(mode='json');payload.pop('banner_detail')
  self.service.cache.clear();self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_saved_snapshot_without_banner_summary_is_regenerated(self):
  result=await self.service.get('000001')
  payload=result.model_dump(mode='json')
  self.service.cache.clear();self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001');self.assertEqual(self.generate.await_count,1)
  payload.pop('banner_summary')
  self.service.cache.clear()
  await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_saved_snapshot_without_banner_tone_is_regenerated(self):
  result=await self.service.get('000001')
  self.assertEqual(result.banner_tone,'악재')
  payload=result.model_dump(mode='json');payload.pop('banner_tone')
  self.service.cache.clear();self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_related_relation_is_kept_and_old_snapshots_without_it_are_regenerated(self):
  self.related.return_value=[RelatedStock(code='000002',name='다른전자',price=100,change_rate=-2)]
  row=dict(code='000002',title=None,ids=[],banner_summary=None,banner_ids=[],banner_tone=None,relation='반도체 경쟁사')
  self.generate.side_effect=lambda r:{**generated(),'related':[row]}
  result=await self.service.get('000001')
  self.assertEqual(result.related[0].relation,'반도체 경쟁사')
  payload=result.model_dump(mode='json');payload['related'][0].pop('relation')
  self.service.cache.clear();self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001');self.assertEqual(self.generate.await_count,2)
 async def test_saved_snapshot_with_null_banner_summary_is_reused(self):
  self.generate.side_effect=None;self.generate.return_value={**generated(),'banner_summary':None,'banner_ids':[]}
  result=await self.service.get('000001')
  self.assertIsNone(result.banner_summary)
  payload=result.model_dump(mode='json')
  self.service.cache.clear();self.service.load=lambda code:(payload,datetime.now(KST))
  await self.service.get('000001');self.generate.assert_awaited_once()
 async def test_older_ai_server_response_is_accepted_but_kept_only_thirty_minutes(self):
  saved=[]
  self.service.save=lambda code,payload:saved.append(payload)
  legacy=generated();legacy.pop('banner_summary');legacy.pop('banner_ids')
  self.generate.side_effect=None;self.generate.return_value=legacy
  result=await self.service.get('000001')
  self.assertTrue(result.ai_available);self.assertEqual(result.title,'실적 부진');self.assertIsNone(result.banner_summary)
  self.assertEqual(saved,[])
  self.now=1799;await self.service.get('000001');self.generate.assert_awaited_once()
  self.generate.return_value=generated()
  self.now=1800;result=await self.service.get('000001')
  self.assertEqual(self.generate.await_count,2);self.assertEqual(result.banner_summary,'분기 실적 부진 확인');self.assertEqual(len(saved),1)
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
 async def test_old_ai_server_that_rejects_article_summaries_gets_the_request_again_without_them(self):
  from app.config import settings
  real=httpx.AsyncClient;bodies=[]
  def handler(req):
   body=json.loads(req.content);bodies.append(body)
   return httpx.Response(422 if 'summary' in body['stock']['headlines'][0] else 200,json=generated())
  req=request();req.stock.headlines[0].summary='가상전자의 3분기 영업이익이 줄었다'
  with patch.object(settings,'AI_SERVICE_TOKEN',SecretStr('fixture')),patch.object(settings,'AI_SERVER_URL','http://local.test'),patch('app.services.stock_brief_service.httpx.AsyncClient',side_effect=lambda **kw:real(transport=httpx.MockTransport(handler),**kw)):
   self.assertEqual((await request_stock(req))['title'],'실적 부진')
  self.assertEqual(len(bodies),2);self.assertEqual(bodies[0]['stock']['headlines'][0]['summary'],'가상전자의 3분기 영업이익이 줄었다')
  self.assertNotIn('summary',bodies[1]['stock']['headlines'][0])

class ValidationTests(TestCase):
 def test_title_rejection_isolated_from_lists(self):
  req=request()
  for change in ({'title':'가'*21},{'title_ids':['other']},{'title_ids':[]}):
   out=sanitize_stock_output({**generated(),**change},req);self.assertIsNone(out['title']);self.assertEqual(len(out['recent_issues']),1)
  req.stock.headlines[0].in_move_window=False
  self.assertIsNone(sanitize_stock_output(generated(),req)['title'])
  req=request();req.stock.change_rate=.99
  self.assertIsNone(sanitize_stock_output(generated(),req)['title'])
 def test_banner_summary_needs_an_own_article_and_does_not_touch_the_title(self):
  req=request();head=req.stock.headlines[0]
  req.stock.headlines=[head.model_copy(update={'title':'美 반도체주 약세에 가상전자 3분기 실적 부진…감산 검토','in_move_window':False})]
  req.market_headlines=[head.model_copy(update={'id':'m-0','title':'코스피 하락 마감'})]
  def banner(value,ids):return sanitize_stock_output({**generated(),'banner_summary':value,'banner_ids':ids},req)
  value='3분기 실적 부진에 감산 검토'
  out=banner(value,['s-0'])
  self.assertEqual((out['banner_summary'],out['banner_ids']),(value,['s-0']))
  self.assertEqual(len(out['recent_issues']),1)
  self.assertEqual(banner(' 3분기 실적 부진에   감산 검토 ',['s-0'])['banner_summary'],value)
  abroad='美 반도체주 약세 속 감산 검토'
  self.assertEqual(banner(abroad,['s-0'])['banner_summary'],abroad)
  # 시장 기사를 함께 적으면 자기 기사만 남긴다.
  self.assertEqual(banner(value,['s-0','m-0'])['banner_ids'],['s-0'])
  for value,ids in ((value,['m-0']),(value,[]),(value,['missing']),(value,['s-0','s-0']),
    ('3분기 실적 부진 → 수익성 악화 우려 → 감산 검토',['s-0']),('감산',['s-0']),('가'*29,['s-0']),
    ('3분기 실적 부진에 가상전자 약세',['s-0']),('반도체주 동반 하락·감산 검토',['s-0']),('가상전자 주가 약세',['s-0']),('코스피 하락 마감',['s-0']),
    ('실적 부진 지속 전망',['s-0']),('4분기 실적 부진에 감산 검토',['s-0']),('3분기 실적 부진에 감산 검토…',['s-0']),
    ('3분기 실적이 부진했다',['s-0']),('감산을 검토합니다',['s-0']),('신제품 출시 지연',['s-0']),(None,['s-0'])):
   with self.subTest(value=value,ids=ids):
    out=banner(value,ids);self.assertIsNone(out['banner_summary']);self.assertEqual(out['banner_ids'],[])
    self.assertEqual(len(out['recent_issues']),1)
 def test_banner_tone_needs_a_banner_and_a_known_value(self):
  req=request()
  for tone in ('호재','악재','엇갈림'):
   self.assertEqual(sanitize_stock_output({**generated(),'banner_tone':tone},req)['banner_tone'],tone)
  for tone in (None,'좋음','positive',3):
   out=sanitize_stock_output({**generated(),'banner_tone':tone},req)
   self.assertIsNotNone(out['banner_summary']);self.assertIsNone(out['banner_tone'])
  self.assertIsNone(sanitize_stock_output({**generated(),'banner_summary':'코스피 급등'},req)['banner_tone'])
  legacy=generated();legacy.pop('banner_tone')
  self.assertIsNone(sanitize_stock_output(legacy,req)['banner_tone'])
 def test_banner_may_report_a_business_figure_but_not_a_price_move(self):
  req=request();head=req.stock.headlines[0]
  req.stock.headlines=[head.model_copy(update={'title':'가상전자 3분기 영업이익 상승…신규 공급계약 효과'})]
  def banner(value,ids=('s-0',)):return sanitize_stock_output({**generated(),'banner_summary':value,'banner_ids':list(ids)},req)
  value='신규 공급계약에 영업이익 상승'
  self.assertEqual(banner(value)['banner_summary'],value)
  self.assertIsNone(banner('신규 공급계약에 가상전자 상승')['banner_summary'])
  for part,price in (('영업이익 상승',False),('실적 반등',False),('수주 반등',False),('가상전자 반등',True),('반도체 동반 반등',True),('주가 상승',True),('HBM주 강세',True)):
   with self.subTest(part=part):self.assertIs(price_talk(part),price)
  # 배너가 비었을 때 어느 규칙에서 버려졌는지 로그로 남긴다.
  with self.assertLogs('app.services.stock_brief_validation','INFO') as logs:banner(value,['m-0'])
  self.assertIn('INFO:app.services.stock_brief_validation:stock_banner code=000001 result=ids',logs.output)
 def test_banner_detail_is_one_sentence_about_the_kept_banner(self):
  req=request();text='가상전자의 분기 실적 부진이 공식 확인됐습니다.'
  def detail(value,**change):return sanitize_stock_output({**generated(),'banner_detail':value,**change},req)['banner_detail']
  self.assertEqual(detail(text),text)
  # 배너가 없거나 걸러지면 상세 제목도 두지 않는다.
  self.assertIsNone(detail(text,banner_summary='코스피 급등'))
  self.assertIsNone(detail(text,banner_summary=None,banner_ids=[]))
  for value in (generated()['banner_summary'],'실적이 부진했습니다.','가상전자의 분기 실적 부진 확인과 수익성 악화 우려 확산',
    '가상전자의 분기 실적이 부진했습니다. 수익성 우려도 나왔습니다. 손실도 커졌습니다.','가상전자의 분기 실적 부진 소식에 주가가 약세를 보였습니다.',
    '가상전자의 2분기 실적 부진이 공식 확인됐습니다.','가상전자의 분기 실적 부진이 이어질 전망입니다.',
    '가상전자가 대규모 해외 투자 계획을 철회했다고 발표했습니다.','가상전자의 분기 실적 부진이 확인됐다고 보도됐습니다.',
    '가상전자의 분기 실적이 부진했으며, 기사에는 수익성 정보가 담겼습니다.',None,3):
   with self.subTest(value=value):self.assertIsNone(detail(value))
  # 인용한 본 종목 기사의 앞부분에 있는 숫자는 쓸 수 있고, 두 문장까지 받는다.
  long='가상전자의 3분기 영업이익이 줄며 분기 실적 부진이 확인됐습니다. 수익성 개선 시점도 늦어졌습니다.'
  self.assertIsNone(detail(long))
  req.stock.headlines[0].summary='가상전자의 3분기 영업이익이 줄었고 수익성 개선 시점이 늦어졌다'
  self.assertEqual(detail(long),long)
  req.stock.headlines[0].summary=None
  # 이전 AI 서버 응답에는 이 필드가 없다.
  self.assertNotIn('banner_detail',generated());self.assertIsNone(sanitize_stock_output(generated(),req)['banner_detail'])
  with self.assertLogs('app.services.stock_brief_validation','INFO') as logs:detail(generated()['banner_summary'])
  self.assertIn('INFO:app.services.stock_brief_validation:stock_banner_detail code=000001 result=phrase',logs.output)
 def test_title_from_the_banner_article_is_dropped_when_its_tone_opposes_the_move(self):
  def run(rate,tone):
   req=request();req.stock.change_rate=rate
   return sanitize_stock_output({**generated(),'banner_tone':tone},req)
  # 주가가 올랐는데 악재, 내렸는데 호재인 소식은 등락 사유가 될 수 없다. 배너와 성격 표시는 그대로 둔다.
  for rate,tone in ((2.5,'악재'),(-2.5,'호재')):
   out=run(rate,tone)
   self.assertEqual((out['title'],out['title_ids'],out['banner_summary'],out['banner_tone']),(None,[],'분기 실적 부진 확인',tone))
   self.assertEqual(len(out['recent_issues']),1)
  for rate,tone in ((2.5,'호재'),(-2.5,'악재'),(2.5,'엇갈림'),(2.5,None)):
   with self.subTest(rate=rate,tone=tone):self.assertEqual(run(rate,tone)['title'],'실적 부진')
  # 배너가 없으면 견줄 성격이 없어 그대로 둔다.
  req=request();req.stock.change_rate=2.5
  self.assertEqual(sanitize_stock_output({**generated(),'banner_summary':None,'banner_ids':[]},req)['title'],'실적 부진')
 def test_related_banner_is_checked_against_the_related_stock_headlines(self):
  req=request();head=req.stock.headlines[0]
  req.related=[StockInput(code='000002',name='다른전자',change_rate=1.5,explain_move=True,
   headlines=[head.model_copy(update={'id':'p000002-0','title':'다른전자 2공장 증설…고객사 수주 확대'})])]
  value='2공장 증설에 고객사 수주 확대'
  row=dict(code='000002',title=None,ids=[],banner_summary=value,banner_ids=['p000002-0'],banner_tone='호재')
  def related(**change):return sanitize_stock_output({**generated(),'related':[{**row,**change}]},req)['related'][0]
  self.assertEqual((related()['banner_summary'],related()['banner_ids'],related()['banner_tone']),(value,['p000002-0'],'호재'))
  out=related(banner_ids=['s-0']);self.assertIsNone(out['banner_summary']);self.assertIsNone(out['banner_tone'])
  self.assertEqual(related(banner_ids=['p000002-0','s-0'])['banner_ids'],['p000002-0'])
  self.assertIsNone(related(banner_tone='positive')['banner_tone'])
  legacy=related(**{'banner_summary':None,'banner_ids':[],'banner_tone':None})
  self.assertIsNone(legacy['banner_summary'])
  old=sanitize_stock_output({**generated(),'related':[dict(code='000002',title=None,ids=[])]},req)['related'][0]
  self.assertIsNone(old['banner_summary']);self.assertEqual(old['banner_ids'],[])
  self.assertIsNone(old['relation'])
  for value,kept in [('반도체 장비 경쟁사','반도체 장비 경쟁사'),('증설 장비 공급사','증설 장비 공급사'),('HBM 장비 고객사','장비 고객사'),('반도체 레이저 장비 업체','반도체 장비 업체'),('레이저 업체',None),
                     ('다른전자 경쟁사',None),('반도체 업종',None),('반도체 장비 제조사','반도체 장비 제조사'),('반도체 업체','반도체 업체'),('주가 동반 경쟁사',None),('2공장 협력사',None),(None,None)]:
   with self.subTest(value=value):self.assertEqual(related(relation=value)['relation'],kept)
 def test_unknown_ai_fields_are_ignored_and_missing_core_fields_still_fail(self):
  req=request()
  out=sanitize_stock_output({**generated(),'future_field':['x']},req)
  self.assertEqual(out['banner_summary'],'분기 실적 부진 확인');self.assertNotIn('future_field',out)
  legacy=generated();legacy.pop('banner_summary');legacy.pop('banner_ids')
  out=sanitize_stock_output(legacy,req);self.assertIsNone(out['banner_summary']);self.assertEqual(out['title'],'실적 부진')
  broken=generated();broken.pop('related')
  with self.assertRaises(ValueError):sanitize_stock_output(broken,req)
  parsed=StockBriefResponse.model_validate({**generated(),'future_field':1})
  self.assertEqual(parsed.banner_summary,'분기 실적 부진 확인');self.assertFalse(hasattr(parsed,'future_field'))
  self.assertIsNone(StockBriefResponse.model_validate(legacy).banner_summary)
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
  out=article_inputs([dict(title='&quot;기판·전력&quot; 상승',date='2026-10-30T10:00:00+09:00')],'s-',now,anchor)
  self.assertEqual(out['s-0'][0].title,'"기판·전력" 상승')
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
   return [dict(title='실적 발표',date=now.isoformat(),source=None,url=None,summary='테라팹이 2028년 하반기 양산을 &quot;목표로&quot;  빠르게 추진되고 있다....')]
  with patch('app.services.stock_brief_service.get_news_by_symbol',side_effect=news) as calls,patch('app.services.stock_brief_service.collect_market_context',AsyncMock(return_value=(MarketContext(),{}))):
   own,peers,market=await collect_articles('000001',[RelatedStock(code='000002',name='다른전자',price=100,change_rate=-2)],now,now)
  self.assertEqual(calls.await_count,2);self.assertIn('s-0',own);self.assertIn('p000002-0',peers['000002'])
  # 기사 앞부분은 본 종목 기사에만 붙인다.
  self.assertEqual(own['s-0'][0].summary,'테라팹이 2028년 하반기 양산을 "목표로" 빠르게 추진되고 있다')
  self.assertIsNone(peers['000002']['p000002-0'][0].summary)
  import importlib.util
  from pathlib import Path
  spec=importlib.util.spec_from_file_location('stock_contract',Path(__file__).resolve().parents[2]/'ai_server/app/stock_brief_schemas.py')
  module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
  self.assertEqual(module.StockBriefRequest.model_validate(request().model_dump(mode='json')).stock.code,'000001')


class TitleEventTests(TestCase):
 def test_title_must_name_what_happened(self):
  from app.services.stock_brief_validation import names_an_event
  for value,ok in [('코스피 4% 급등',False),('코스피·코스닥 동반 반등',False),('코스피 급반등',False),('美 훈풍에 코스피 급등',False),
                   ('외국인 순매수에 코스피 상승',False),('가상전자 급등',False),('코스피 반등 속 가상전자 회복',False),
                   ('美 반도체주 급등에 기판주 강세',True),('빅테크 실적 쇼크·코스피 급락',True),('AI 투자 수익화 지연',True)]:
   with self.subTest(value=value):self.assertIs(names_an_event(value,'가상전자'),ok)
