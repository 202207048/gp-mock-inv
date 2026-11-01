import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.stock_brief_schemas import StockBriefRequest,StockBriefResponse
from app.stock_brief_routes import get_generator
from app.summarizers.stock_brief import build_stock_brief_prompt,sanitize_stock_output
from app.summarizers.stock_brief_openai import StockBriefGenerator
from app.summarizers.base import SummarizerError

DATA=dict(stock=dict(code='000001',name='가상전자',change_rate=-2,explain_move=True,headlines=[dict(id='s-0',title='실적 부진',source=None,published_at='2026-11-01T10:00:00+09:00',in_move_window=True)]),related=[],market_headlines=[])
def output():return dict(title='실적 부진',title_ids=['s-0'],recent_issues=[],positive_factors=[],risk_factors=[],related=[])

def test_strict_schema():
 with pytest.raises(ValidationError):StockBriefRequest(**DATA,extra='bad')
 schema=StockBriefResponse.model_json_schema()
 assert schema['additionalProperties'] is False
 assert set(schema['required'])==set(output())
 for value in ('nan','inf'):
  with pytest.raises(ValidationError):StockBriefRequest(**{**DATA,'stock':{**DATA['stock'],'change_rate':value}})

def test_prompt_rules_and_sdk():
 req=StockBriefRequest(**DATA);prompt=build_stock_brief_prompt(req)
 for rule in ('20자','명사구','in_move_window','explain_move=false','120자','습니다','때문에','반대 방향','입력 문자열'):
  assert rule in prompt
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
