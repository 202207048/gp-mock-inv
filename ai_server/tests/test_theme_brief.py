from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import json
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.theme_routes import get_generator
from app.theme_schemas import ThemeBriefRequest
from app.summarizers.theme import validate_theme_output,build_theme_prompt
from app.summarizers.theme_openai import ThemeGenerator

DATA={'themes':[{'id':'1','name':'반도체','direction':'rising','change_rate':'2.5','rise_count':8,'fall_count':2,'total_count':10,
 'leaders':[{'name':'가상전자','change_rate':'3.5'}],
 'headlines':[{'id':'h1','title':'가상전자 공급계약 체결','source':'가상신문','published_at':'2026-10-30T09:00:00+09:00'}]}], 'profile':'luna'}
def output(reason='가상전자 공급계약 체결 소식이 있어요.',ids=None):
 return {'themes':[{'id':'1','reason':reason,'headline_ids':['h1'] if ids is None else ids}]}

@pytest.mark.parametrize('change',[
 {'reason':'a'*81}, {'reason':'매수하세요 소식이 있어요.'}, {'reason':'실적이 증가할 것 소식이 있어요.'},
 {'headline_ids':['missing']}, {'headline_ids':[]}, {'headline_ids':['h1']*4},
 {'reason':'금리가 99% 하락했어요.'}, {'reason':None,'headline_ids':['h1']},
])
def test_unsafe_item_degrades_to_null(change):
 d=output();d['themes'][0].update(change)
 item=validate_theme_output(d,ThemeBriefRequest(**DATA)).themes[0]
 assert item.reason is None and item.headline_ids==[]

@pytest.mark.parametrize('rows',[[],[{'id':'other','reason':None,'headline_ids':[]}],
 [output()['themes'][0]]*2])
def test_identity_mismatch_returns_requested_ids_null(rows):
 r=validate_theme_output({'themes':rows},ThemeBriefRequest(**DATA))
 assert [t.id for t in r.themes]==['1'] and r.themes[0].reason is None


def test_valid_and_one_invalid_item_are_isolated():
 data=deepcopy(DATA);data['themes'].append(deepcopy(data['themes'][0]));data['themes'][1]['id']='2'
 rows=output()['themes']+[{'id':'2','reason':'잘못된 주장','headline_ids':['h1']}]
 r=validate_theme_output({'themes':rows},ThemeBriefRequest(**data))
 assert r.themes[0].reason and r.themes[1].reason is None


def test_single_sdk_call_and_title_prompt():
 call=Mock(return_value=SimpleNamespace(output_text=json.dumps(output()),usage=None))
 gen=ThemeGenerator(client=SimpleNamespace(responses=SimpleNamespace(create=call)))
 assert gen.generate(ThemeBriefRequest(**DATA),uuid4()).themes[0].reason
 call.assert_called_once()
 assert '제목에 없는' in build_theme_prompt(ThemeBriefRequest(**DATA))


def test_service_auth_and_route(monkeypatch):
 class Fake:
  def generate(self,request,trace):return validate_theme_output(output(),request)
 app.dependency_overrides[get_generator]=Fake
 try:
  with TestClient(app) as c:
   assert c.post('/brief/theme',json=DATA).status_code==401
   assert c.post('/brief/theme',json=DATA,headers={'Authorization':'Bearer unit-test-service-token'}).status_code==200
   monkeypatch.delenv('AI_SERVICE_TOKEN')
   assert c.post('/brief/theme',json=DATA).status_code==503
 finally:app.dependency_overrides.clear()


def test_opposite_direction_is_prompt_rule_and_fake_can_abstain():
 data=deepcopy(DATA);data['themes'][0]['direction']='falling'
 request=ThemeBriefRequest(**data)
 assert '방향이 반대' in build_theme_prompt(request)
 assert validate_theme_output(output(None,[]),request).themes[0].reason is None


def test_paraphrase_market_citation_and_rounding():
 data=deepcopy(DATA)
 data['market']={'indices':[{'name':'코스피','change_rate':'-4.04'}],
 'headlines':[{'id':'m-1','title':'코스피 4.04% 급락에 반도체 약세','source':'뉴스','published_at':'2026-10-30T09:00:00+09:00'}]}
 request=ThemeBriefRequest(**data)
 assert validate_theme_output(output('코스피가 4% 하락한 가운데 반도체도 약세였어요.',['m-1']),request).themes[0].reason
 assert validate_theme_output(output('코스피가 99% 하락했어요.',['m-1']),request).themes[0].reason is None
 assert validate_theme_output(output('공급계약 소식과 함께 움직였어요.'),request).themes[0].reason


@pytest.mark.parametrize('reason',['실적 개선 기대 소식이 있어요.','업황 개선 전망 소식이 있어요.'])
def test_speculative_reason_degrades_to_null(reason):
 assert validate_theme_output(output(reason),ThemeBriefRequest(**DATA)).themes[0].reason is None


def test_prompt_avoids_card_numbers_but_allows_news_context_numbers():
 prompt=build_theme_prompt(ThemeBriefRequest(**DATA))
 assert '화면에 따로 표시되므로 문장에 반복하지 말고' in prompt
 assert '기사에서 읽히는 소식과 맥락에 집중' in prompt
 assert '맥락에 필요한 숫자는 사용할 수' in prompt
