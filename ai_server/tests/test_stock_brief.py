import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.stock_brief_schemas import StockBriefRequest,StockBriefResponse
from app.stock_brief_routes import get_generator
from app.summarizers.stock_brief import build_stock_brief_prompt,names_an_event,price_talk,restates_move,sanitize_stock_output
from app.summarizers.stock_brief_openai import StockBriefGenerator
from app.summarizers.base import SummarizerError

DATA=dict(stock=dict(code='000001',name='가상전자',change_rate=-2,explain_move=True,headlines=[dict(id='s-0',title='실적 부진',source=None,published_at='2026-11-01T10:00:00+09:00',in_move_window=True)]),related=[],market_headlines=[])
def output():return dict(title='실적 부진',title_ids=['s-0'],banner_summary=None,banner_ids=[],banner_detail=None,banner_tone=None,recent_issues=[],positive_factors=[],risk_factors=[],related=[])

def test_strict_schema():
 with pytest.raises(ValidationError):StockBriefRequest(**DATA,extra='bad')
 schema=StockBriefResponse.model_json_schema()
 assert schema['additionalProperties'] is False
 assert set(schema['required'])==set(output())
 for value in ('nan','inf'):
  with pytest.raises(ValidationError):StockBriefRequest(**{**DATA,'stock':{**DATA['stock'],'change_rate':value}})

def test_prompt_rules_and_sdk():
 req=StockBriefRequest(**DATA);prompt=build_stock_brief_prompt(req)
 for rule in ('20자','명사구','in_move_window','explain_move=false','120자','습니다','때문에','반대 방향','입력 문자열','banner_summary','24자 이내','화살표','banner_ids','말줄임표','그대로 복사','등락률만 봐도 아는 말','같은 업종','예측어','banner_tone','호재','악재','엇갈림','소식 내용만','relation','동종업체','널리 알려진 관계','같은 제품','제조사','다른 업종','반드시 쓰세요','주가 방향과 상관없이','banner_detail','60~120자','한두 문장','summary(기사 앞부분)','summary 문장을 그대로 옮기지','기사나 보도를 설명하는 말','등락과 반대 방향이면','제품이나 장비를 팔면 공급사','관련 종목 한미반도체는 HBM 장비 공급사'):
  assert rule in prompt
 # 항목마다 한 섹션이고, 규칙은 섹션 안에 한 줄씩 있다.
 heads=[line for line in prompt.splitlines() if line.startswith('[')]
 assert [h.split(']')[0]+']' for h in heads]==['[공통 규칙]','[title]','[banner_summary]','[banner_detail]','[banner_tone]','[recent_issues, positive_factors, risk_factors]','[related]']
 call=Mock(return_value=SimpleNamespace(output_text=json.dumps(output()),usage=None))
 gen=StockBriefGenerator(client=SimpleNamespace(responses=SimpleNamespace(create=call)))
 assert gen.generate(req,uuid4()).title=='실적 부진'
 call.assert_called_once();kw=call.call_args.kwargs
 assert kw['reasoning']=={'effort':'none'} and kw['model']=='gpt-6-luna' and kw['text']['format']['strict']

def test_auth_and_error_mapping(monkeypatch):
 class Fake:
  def generate(self,*a):raise SummarizerError('fake')
 app.dependency_overrides[get_generator]=Fake
 try:
  with TestClient(app) as c:
   assert c.post('/brief/stock',json=DATA).status_code==401
   assert c.post('/brief/stock',json=DATA,headers={'Authorization':'Bearer unit-test-service-token'}).status_code==502
   monkeypatch.delenv('AI_SERVICE_TOKEN')
   assert c.post('/brief/stock',json=DATA).status_code==503
 finally:app.dependency_overrides.clear()

@pytest.mark.parametrize('change',[{'title':'가'*21},{'title_ids':['missing']},{'title':'실적 영향으로 하락'},{'title':'매수 추천'}])
def test_invalid_title_only_removed(change):
 result=sanitize_stock_output({**output(),**change},StockBriefRequest(**DATA))
 assert result['title'] is None


def test_malformed_envelope_is_failure_not_abstention():
 with pytest.raises(ValueError):sanitize_stock_output({},StockBriefRequest(**DATA))

def test_numbers_must_come_from_cited_titles_or_rates():
 data={**DATA,'stock':{**DATA['stock'],'headlines':[{**DATA['stock']['headlines'][0],'title':'빅테크 실적 쇼크에 코스피 3%대 급락'}]}}
 req=StockBriefRequest(**data)
 ok=sanitize_stock_output({**output(),'title':'빅테크 쇼크·코스피 3%대 급락'},req)
 assert ok['title']=='빅테크 쇼크·코스피 3%대 급락'
 assert sanitize_stock_output({**output(),'title':'빅테크 쇼크·코스피 5% 급락'},req)['title'] is None
 assert sanitize_stock_output({**output(),'title':'2% 하락 속 실적 부진'},req)['title']=='2% 하락 속 실적 부진'

OWN='가상전자, AI 데이터센터 3곳 인허가 심사 강화…규제 논의'
def banner_request(**stock):
 head=DATA['stock']['headlines'][0]
 return StockBriefRequest(**{**DATA,'stock':{**DATA['stock'],'headlines':[{**head,'title':OWN,'in_move_window':False},{**head,'id':'s-1','title':'가상전자 신규 공급계약'}],**stock},
  'market_headlines':[{**head,'id':'m-0','title':'코스피 하락 마감'}],
  'related':[{**DATA['stock'],'code':'000002','name':'다른전자','headlines':[{**head,'id':'p000002-0','title':'다른전자 증설 발표'}]}]})
def banner(value,ids,**stock):
 data={**output(),'title':None,'title_ids':[],'banner_summary':value,'banner_ids':ids,'related':[dict(code='000002',title=None,ids=[])]}
 return sanitize_stock_output(data,banner_request(**stock))

V='AI 데이터센터 심사·규제 강화 가능성'

def test_banner_summary_cites_own_headlines_from_any_window_and_keeps_the_move_title():
 out=banner(V,['s-0'])
 assert out['banner_summary']==V and out['banner_ids']==['s-0']
 # 앞뒤와 안쪽의 남는 공백은 다듬는다.
 assert banner('  AI 데이터센터   심사·규제 강화 가능성 ',['s-0'])['banner_summary']==V
 # 등락을 설명하지 않는 종목(explain_move=false)도 배너 요약은 받을 수 있다.
 assert banner(V,['s-0'],explain_move=False,change_rate=0.2)['banner_summary']==V
 data={**output(),'banner_summary':V,'banner_ids':['s-0'],'title':'신규 공급계약','title_ids':['s-1'],'related':[dict(code='000002',title=None,ids=[])]}
 both=sanitize_stock_output(data,banner_request())
 assert both['title']=='신규 공급계약' and both['title_ids']==['s-1'] and both['banner_summary']==V
 assert StockBriefResponse.model_validate(both).banner_summary==V

@pytest.mark.parametrize('value,ids',[
 (V,['m-0']),(V,['p000002-0']),(V,['m-0','p000002-0']),   # 본 종목 기사가 하나도 없음
 (V,[]),(V,['missing']),(V,['s-0','s-0']),
 ('AI 데이터센터 심사 강화 → 인허가 지연 우려 → 규제 논의',['s-0']),('심사 강화 → 규제 논의',['s-0']),  # 화살표
 ('심사 강화',['s-0']),('가'*29,['s-0']),            # 6자 미만, 28자 초과
 ('AI 데이터센터 규제 강화 전망',['s-0']),           # 금지어
 ('AI 데이터센터 5곳 심사 강화',['s-0']),            # 인용 제목에 없는 숫자
 (V+'…',['s-0']),(V+'...',['s-0']),
 ('AI 데이터센터 심사를 강화했다',['s-0']),('데이터센터 규제를 논의합니다',['s-0']),
 ('AI DATA CENTER RULE',['s-0']),                    # 한국어 아님
 ('가상전자 주가 강세',['s-0']),('코스피 하락 속 규제 논의',['s-0']),('데이터센터 규제에 주가 약세',['s-0']),  # 주가·지수 말
 ('엔비디아 분기 실적 발표',['s-0']),                # 본 종목 기사에 없는 일
 ('가상전자 신규 공급계약',['s-1']),                 # 기사 제목 그대로
 (None,['s-0']),(123456,['s-0']),
])
def test_banner_summary_without_grounding_is_null(value,ids):
 out=banner(value,ids)
 assert out['banner_summary'] is None and out['banner_ids']==[]

def test_banner_summary_number_must_be_in_the_cited_title():
 value='AI 데이터센터 3곳 심사 강화'
 assert banner(value,['s-0'])['banner_summary']==value
 # 다른 기사에 있는 숫자나 종목 등락률은 근거가 아니다.
 assert banner(value,['s-1'])['banner_summary'] is None

def test_banner_summary_is_part_of_the_generated_contract():
 req=banner_request()
 body={**output(),'banner_summary':V,'banner_ids':['s-0'],'related':[dict(code='000002',title=None,ids=[])]}
 call=Mock(return_value=SimpleNamespace(output_text=json.dumps(body),usage=None))
 gen=StockBriefGenerator(client=SimpleNamespace(responses=SimpleNamespace(create=call)))
 assert gen.generate(req,uuid4()).banner_summary==V
 schema=call.call_args.kwargs['text']['format']['schema']
 assert {'banner_summary','banner_ids','banner_tone'}<=set(schema['required'])

MOVE_HEAD=[{**DATA['stock']['headlines'][0],'title':'美 반도체주 급등에 가상전자 장비 수주 기회'}]

def test_banner_may_name_a_move_abroad_but_not_a_domestic_price_move():
 value='美 반도체주 급등에 장비 수주 기회'
 assert banner(value,['s-0'],headlines=MOVE_HEAD)['banner_summary']==value
 for bad in ('반도체주 급등에 장비 수주 기회',      # 국내 등락
             '장비 수주 기회에 가상전자 강세','반도체 동반 반등·장비 수주 기회','장비 수주 기회·코스피 사이드카'):
  assert banner(bad,['s-0'],headlines=MOVE_HEAD)['banner_summary'] is None,bad


@pytest.mark.parametrize('value,moved',[
 ('가상전자 주가 강세',True),('가상전자 급등',True),('주가 급락 흐름',True),('반도체주 동반 상승세',True),
 ('코스피 급등',True),('데이터센터 관련주 일제히 상승',True),
 ('미국 반도체주 강세',False),('데이터센터 인허가 심사 강화',False),('엔비디아 급락 속 규제 논의',False),
])
def test_event_that_only_restates_the_price_move(value,moved):
 assert restates_move(value,'가상전자') is moved


def test_banner_words_may_come_from_any_own_headline_but_not_only_from_market_words():
 value='데이터센터 심사 속 신규 공급계약'
 assert banner(value,['s-0'])['banner_summary']==value
 # '급등' 같은 시장 말만 겹치면 근거가 아니다.
 assert banner('美 반도체주 급등에 업종 매수 유입',['s-0'],headlines=[{**MOVE_HEAD[0],'title':'가상전자 급등에 코스피 상승'}])['banner_summary'] is None


@pytest.mark.parametrize('tone',['호재','악재','엇갈림'])
def test_banner_tone_is_kept_only_with_a_banner(tone):
 data={**output(),'title':None,'title_ids':[],'banner_summary':V,'banner_ids':['s-0'],'banner_tone':tone,'related':[dict(code='000002',title=None,ids=[])]}
 out=sanitize_stock_output(data,banner_request())
 assert out['banner_tone']==tone and StockBriefResponse.model_validate(out).banner_tone==tone
 # 배너가 걸러지면 성격 표시도 남기지 않는다.
 assert sanitize_stock_output({**data,'banner_summary':'코스피 급등'},banner_request())['banner_tone'] is None

@pytest.mark.parametrize('tone',[None,'좋음','positive',1])
def test_unknown_banner_tone_is_dropped(tone):
 data={**output(),'title':None,'title_ids':[],'banner_summary':V,'banner_ids':['s-0'],'banner_tone':tone,'related':[dict(code='000002',title=None,ids=[])]}
 out=sanitize_stock_output(data,banner_request())
 assert out['banner_summary']==V and out['banner_tone'] is None


RV='2공장 증설에 고객사 수주 확대'
def related_out(**row):
 req=banner_request();req.related[0].headlines[0].title='다른전자 2공장 증설…고객사 수주 확대'
 base=dict(code='000002',title=None,ids=[],banner_summary=RV,banner_ids=['p000002-0'],banner_tone='호재')
 return sanitize_stock_output({**output(),'title':None,'title_ids':[],'related':[{**base,**row}]},req)

def test_related_banner_uses_the_related_stock_headlines():
 out=related_out()
 row=out['related'][0]
 assert (row['banner_summary'],row['banner_ids'],row['banner_tone'])==(RV,['p000002-0'],'호재')
 assert StockBriefResponse.model_validate(out).related[0].banner_tone=='호재'
 # 본 종목이나 시장 기사를 인용하면 관련 종목 배너로 받지 않는다.
 for ids in (['s-0'],['m-0'],['s-0','m-0']):
  row=related_out(banner_ids=ids)['related'][0]
  assert row['banner_summary'] is None and row['banner_ids']==[] and row['banner_tone'] is None
 assert related_out(banner_ids=['p000002-0','s-0'])['related'][0]['banner_ids']==['p000002-0']
 assert related_out(banner_summary='코스피 급등 → 업종 동반 → 다른전자 강세')['related'][0]['banner_summary'] is None
 assert related_out(banner_tone='좋음')['related'][0]['banner_tone'] is None


@pytest.mark.parametrize('value,ok',[
 ('코스피 4% 급등',False),('코스피·코스닥 동반 반등',False),('코스피 급반등',False),('美 훈풍에 코스피 급등',False),
 ('외국인 순매수에 코스피 상승',False),('가상전자 급등',False),('코스피 급등과 매수세 유입',False),('美 훈풍에 매수 사이드카 발동',False),('코스피 반등 속 가상전자 회복',False),
 ('美 반도체주 급등에 기판주 강세',True),('빅테크 실적 쇼크·코스피 급락',True),('AI 투자 수익화 지연',True),
])
def test_title_must_name_what_happened(value,ok):
 assert names_an_event(value,'가상전자') is ok
 head=DATA['stock']['headlines'][0]
 req=StockBriefRequest(**{**DATA,'stock':{**DATA['stock'],'headlines':[{**head,'title':value+' 소식'}]}})
 assert (sanitize_stock_output({**output(),'title':value},req)['title']==value) is ok


@pytest.mark.parametrize('value,kept',[
 ('반도체 장비 경쟁사','반도체 장비 경쟁사'),(' 반도체   동종업체 ','반도체 동종업체'),('경쟁사','경쟁사'),
 ('증설 장비 공급사','증설 장비 공급사'),('HBM 장비 고객사','장비 고객사'),('반도체 레이저 장비 업체','반도체 장비 업체'),('레이저 장비 업체','장비 업체'),('레이저 업체',None),('가상전자 고객사',None),('다른전자 경쟁사',None),
 ('반도체 장비 경쟁사입니다',None),('반도체 업종',None),('반도체 장비 제조사','반도체 장비 제조사'),('반도체 업체','반도체 업체'),('주가 동반 경쟁사',None),('2공장 협력사',None),
 ('반도체 장비 수혜 기대 경쟁사',None),('경쟁사 → 수주',None),(None,None),(3,None),
])
def test_related_relation_ends_with_a_role_and_uses_known_words(value,kept):
 out=related_out(relation=value)
 assert out['related'][0]['relation']==kept
 assert StockBriefResponse.model_validate(out).related[0].relation==kept
 assert 'relation' in StockBriefResponse.model_json_schema()['$defs']['RelatedBrief']['required']


@pytest.mark.parametrize('part,price',[
 ('영업이익 상승',False),('실적 반등',False),('D램 가격 상승',False),('수주 반등',False),('시장 점유율 하락',False),('장비 수요 확대',False),
 ('가상전자 반등',True),('반도체 동반 반등',True),('주가 상승',True),('동반 상승',True),('HBM주 강세',True),('코스피 급등',True),('실적 반등에 주가 상승',True),
])
def test_only_business_figures_may_rise_or_fall(part,price):
 assert price_talk(part) is price

def test_banner_may_report_a_business_figure():
 heads=[{**DATA['stock']['headlines'][0],'title':'가상전자 3분기 영업이익 상승…신규 공급계약 효과'}]
 value='신규 공급계약에 영업이익 상승'
 assert banner(value,['s-0'],headlines=heads)['banner_summary']==value
 assert banner('신규 공급계약에 가상전자 상승',['s-0'],headlines=heads)['banner_summary'] is None

def test_other_articles_in_banner_ids_are_dropped_and_own_ones_kept():
 out=banner(V,['s-0','m-0','p000002-0'])
 assert out['banner_summary']==V and out['banner_ids']==['s-0']

@pytest.mark.parametrize('value,ids,why',[
 (V,['s-0'],'kept'),(None,[],'none'),(V,['m-0'],'ids'),
 ('AI 데이터센터 심사 강화 → 규제 논의',['s-0'],'phrase'),('AI 데이터센터 심사를 강화했다',['s-0'],'phrase'),
 ('AI 데이터센터 규제 강화 전망',['s-0'],'forbidden'),
 ('데이터센터 규제에 주가 약세',['s-0'],'price_talk'),('코스피 급등',['s-0'],'price_talk'),
 ('엔비디아 분기 실적 발표',['s-0'],'ungrounded'),
 ('AI 데이터센터 5곳 심사 강화',['s-0'],'numbers'),
 ('가상전자 신규 공급계약',['s-1'],'copied'),
])
def test_log_says_why_a_banner_is_missing(value,ids,why,caplog):
 # 배너가 비었을 때 모델이 쓰지 않은 것인지, 어느 규칙에서 버려졌는지 로그로 구분한다. 문구는 남기지 않는다.
 with caplog.at_level(logging.INFO,logger='app.summarizers.stock_brief'):banner(value,ids)
 assert f'stock_banner code=000001 result={why}' in caplog.messages
 assert not any('데이터센터' in m for m in caplog.messages)

D='가상전자의 AI 데이터센터 3곳에 대한 인허가 심사가 강화되고 규제 논의가 이어졌습니다.'
def detail(value,**change):
 data={**output(),'title':None,'title_ids':[],'banner_summary':V,'banner_ids':['s-0'],'banner_detail':value,'related':[dict(code='000002',title=None,ids=[])],**change}
 return sanitize_stock_output(data,banner_request())

def test_banner_detail_is_one_sentence_about_the_kept_banner():
 out=detail(D)
 assert out['banner_summary']==V and out['banner_detail']==D
 assert StockBriefResponse.model_validate(out).banner_detail==D
 assert 'banner_detail' in StockBriefResponse.model_json_schema()['required']
 assert detail('  가상전자의 AI 데이터센터 3곳에 대한 인허가 심사가 강화되고   규제 논의가 이어졌습니다. ')['banner_detail']==D
 # 배너가 없거나 걸러지면 상세 제목도 두지 않는다.
 assert detail(D,banner_summary=None,banner_ids=[])['banner_detail'] is None
 assert detail(D,banner_summary='코스피 급등 → 업종 동반 → 가상전자 강세')['banner_detail'] is None

@pytest.mark.parametrize('value,why',[
 (D,'kept'),(None,'none'),('AI 데이터센터 심사 강화 → 규제 논의 → 인허가 지연','phrase'),('심사가 강화됐습니다.','phrase'),(V,'sentence'),
 ('AI 데이터센터 3곳 인허가 심사 강화와 정부의 규제 논의 확대','sentence'),                     # 문장이 아님
 ('가상전자의 AI 데이터센터 심사가 강화됐습니다. 규제 논의도 이어졌습니다. 인허가도 늦어졌습니다.','sentence'),  # 세 문장
 ('가상전자의 AI 데이터센터 인허가 심사가 강화될 전망입니다.','forbidden'),
 ('가상전자의 AI 데이터센터 심사 강화 소식에 주가가 약세를 보였습니다.','price_talk'),
 ('가상전자의 AI 데이터센터 5곳에 대한 인허가 심사가 강화됐습니다.','numbers'),
 ('가상전자가 대규모 해외 투자 계획을 철회했다고 발표했습니다.','ungrounded'),
])
def test_banner_detail_rules_and_the_logged_reason(value,why,caplog):
 with caplog.at_level(logging.INFO,logger='app.summarizers.stock_brief'):out=detail(value)
 assert out['banner_summary']==V and (out['banner_detail']==value if why=='kept' else out['banner_detail'] is None)
 assert f'stock_banner_detail code=000001 result={why}' in caplog.messages
 assert not any('데이터센터' in m for m in caplog.messages)

NOTE='가상전자는 2027년까지 데이터센터 3곳의 인허가를 다시 받아야 하며, 정부는 전력 사용 기준도 함께 손볼 계획이다'
LONG='가상전자는 2027년까지 AI 데이터센터 3곳의 인허가를 다시 받아야 합니다. 정부도 전력 사용 기준을 손보며 규제 논의가 이어졌습니다.'
def test_banner_detail_can_use_the_cited_article_summary_for_more_detail(caplog):
 # 기사 앞부분(summary)에 있는 사실과 숫자로 제목보다 자세히, 두 문장까지 쓸 수 있다.
 def run(value,note=NOTE,ids=('s-0',)):
  req=banner_request();req.stock.headlines[0].summary=note
  data={**output(),'title':None,'title_ids':[],'banner_summary':V,'banner_ids':list(ids),'banner_detail':value,'related':[dict(code='000002',title=None,ids=[])]}
  with caplog.at_level(logging.INFO,logger='app.summarizers.stock_brief'):return sanitize_stock_output(data,req)['banner_detail']
 assert run(LONG)==LONG
 # 앞부분이 없거나 인용하지 않은 기사의 앞부분이면 그 숫자는 근거가 없다.
 assert run(LONG,note=None) is None and 'stock_banner_detail code=000001 result=numbers' in caplog.messages
 assert run(LONG,ids=('s-1',)) is None
 # 앞부분 문장을 그대로 옮기면 받지 않는다.
 same='가상전자는 2027년까지 데이터센터 3곳의 인허가를 다시 받아야 합니다'
 assert run(same+'.',note=same+' 그리고 정부도') is None and 'stock_banner_detail code=000001 result=copied' in caplog.messages
 assert StockBriefRequest.model_json_schema()['$defs']['Headline']['properties']['summary']

def test_banner_detail_must_be_the_same_news_as_the_banner(caplog):
 # 배너가 인용한 다른 기사의 소식을 쓰면 카드와 상세 제목이 서로 다른 이야기가 된다.
 other='가상전자가 고객사와 신규 공급계약을 체결했다고 밝혔습니다.'
 with caplog.at_level(logging.INFO,logger='app.summarizers.stock_brief'):out=detail(other,banner_ids=['s-0','s-1'])
 assert out['banner_summary']==V and out['banner_detail'] is None
 assert 'stock_banner_detail code=000001 result=unrelated' in caplog.messages

def test_title_from_the_banner_article_is_dropped_when_its_tone_opposes_the_move():
 head=DATA['stock']['headlines'][0]
 def run(rate,tone,title_ids=('s-0',)):
  req=banner_request(change_rate=rate,headlines=[{**head,'title':OWN},{**head,'id':'s-1','title':'가상전자 신규 공급계약'}])
  data={**output(),'title':'AI 데이터센터 규제 논의','title_ids':list(title_ids),'banner_summary':V,'banner_ids':['s-0'],'banner_tone':tone,'related':[dict(code='000002',title=None,ids=[])]}
  return sanitize_stock_output(data,req)
 # 주가가 올랐는데 악재, 내렸는데 호재인 소식은 등락 사유가 될 수 없다. 배너와 성격 표시는 그대로 둔다.
 for rate,tone in ((2,'악재'),(-2,'호재')):
  out=run(rate,tone)
  assert (out['title'],out['title_ids'],out['banner_summary'],out['banner_tone'])==(None,[],V,tone)
 for rate,tone in ((2,'호재'),(-2,'악재'),(2,'엇갈림'),(2,None)):
  assert run(rate,tone)['title']=='AI 데이터센터 규제 논의'
 # 배너와 다른 기사에서 가져온 사유는 그대로 둔다.
 assert run(2,'악재',title_ids=['s-1'])['title']=='AI 데이터센터 규제 논의'
 # 관련 종목 행에도 같은 규칙을 쓴다.
 row=dict(code='000002',title='2공장 증설 발표',ids=['p000002-0'],banner_summary=RV,banner_ids=['p000002-0'],banner_tone='호재',relation=None)
 req=banner_request();req.related[0].headlines[0].title='다른전자 2공장 증설…고객사 수주 확대'
 assert sanitize_stock_output({**output(),'title':None,'title_ids':[],'related':[row]},req)['related'][0]['title'] is None
 req.related[0].change_rate=2
 assert sanitize_stock_output({**output(),'title':None,'title_ids':[],'related':[row]},req)['related'][0]['title']=='2공장 증설 발표'

@pytest.mark.parametrize('value',[
 '가상전자가 AI 데이터센터 심사 강화에 대응했으며, 기사에는 규제 논의 정보가 담겼습니다.',
 '가상전자의 AI 데이터센터 인허가 심사가 강화됐다고 보도됐습니다.',
 '가상전자의 AI 데이터센터 심사 강화가 언급된 가운데 규제 논의가 이어졌습니다.',
])
def test_banner_detail_must_not_describe_the_article_itself(value,caplog):
 with caplog.at_level(logging.INFO,logger='app.summarizers.stock_brief'):out=detail(value)
 assert out['banner_detail'] is None and 'stock_banner_detail code=000001 result=meta' in caplog.messages
