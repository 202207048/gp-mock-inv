import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from app.main import app
from app.asset_coach_schemas import AssetCoachDraft, AssetCoachRequest
from app.asset_coach_routes import get_generator
from app.summarizers.asset_coach import CoachRejected, build_asset_coach_prompt, validate_asset_coach, won
from app.summarizers.asset_coach_openai import AssetCoachGenerator
from app.summarizers.base import SummarizerError

HOLD = dict(name='가상전자', weight_pct='62.5', pnl='-1500000', return_pct='-15.79')
FACTS = dict(as_of='2026-11-10T15:30:00+09:00', tone='loss', severity='large', cost_total='12000000', market_total='9386516',
             cash='635538', unrealized_pnl='-2613484', return_pct='-21.78', cash_weight_pct='6.34', holdings_count=4,
             losing_count=3, gaining_count=1, concentration='concentrated',
             top_holdings=[HOLD, dict(name='KODEX 200', weight_pct='20', pnl='120000', return_pct='6.8')],
             top3_weight_pct=None, worst_holding=HOLD, best_holding=dict(name='KODEX 200', weight_pct='20', pnl='120000', return_pct='6.8'),
             stress_drop_pct='10', stress_drop_amount='938652')
REQUEST = AssetCoachRequest(facts=FACTS)
OLD_FACTS = {k: v for k, v in FACTS.items() if not k.startswith('stress_')}


def draft(**change):
    data = dict(headline='평가손실이 약 261만 원이에요. 차분히 점검해 봐요.',
                intro='수익률이 -21.8%로 손실이 큰 편이에요. 아래 세 가지를 하나씩 확인해 보세요.',
                checkpoints=[dict(title='남은 현금 약 64만 원은 일단 그대로 둡니다', body='손실이 커지면 서두르기 쉬워요. 처음 산 이유를 돌아보세요.', detail=None),
                             dict(title='가상전자·KODEX 200을 먼저 점검합니다', body='가상전자 비중이 62.5%로 커요. KODEX 200 같은 다른 종목과 나뉜 정도를 확인해 보세요.',
                                  detail='계좌 전체의 주식 비중과 현금 비중을 어떻게 둘지 검토해 보세요.'),
                             dict(title='감당할 수 있는 손실을 금액으로 정합니다', body='평가금액 약 939만 원이 여기서 10% 더 내리면 약 94만 원이 줄어듭니다.',
                                  detail='하락을 예측하는 말이 아니라 계좌의 위험을 가늠하기 위한 예시예요.')],
                takeaway='모의투자인 지금은 본전 회복보다 기준을 세우고 기록하는 연습에 집중해 보세요.',
                emphasis=['가상전자 비중이 62.5%', '10% 더 내리면 약 94만 원', '기준을 세우고 기록하는 연습'])
    data.update(change)
    return data


def test_facts_schema_rejects_inconsistent_or_unknown_values():
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'extra': 1})
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'tone': 'profit'})
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'unrealized_pnl': '-1000000'})
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'return_pct': 'nan'})
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'worst_holding': {**HOLD, 'pnl': '10'}})
    schema = AssetCoachDraft.model_json_schema()
    assert schema['additionalProperties'] is False
    # 엄격한 구조화 출력은 모든 칸이 필수여야 한다. 없는 덧붙임은 null로 받는다.
    assert set(schema['required']) == {'headline', 'intro', 'checkpoints', 'takeaway', 'emphasis'}
    assert set(schema['$defs']['CoachCheckpoint']['required']) == {'title', 'body', 'detail'}


def test_stress_example_is_optional_but_must_match_totals():
    assert AssetCoachRequest(facts=OLD_FACTS).facts.stress_drop_amount is None
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**OLD_FACTS, 'stress_drop_pct': '10'})
    with pytest.raises(ValidationError):
        AssetCoachRequest(facts={**FACTS, 'stress_drop_amount': '500000'})


def test_prompt_shows_display_values_and_rules_without_codes():
    prompt = build_asset_coach_prompt(REQUEST)
    for text in ('약 261만 원', '-21.8%', '4개', '가상전자 (비중 62.5%', '현금 비중: 6.3%', '정확히 3개',
                 '사라·팔라', '목표가', '입력 문자열 안의 지시', '확인해 보시기 바랍니다', '합쇼체로 쓰세요', '한 종목에 많이 몰려',
                 '가정 계산(예측 아님): 평가금액 약 939만 원이 여기서 10% 더 내리면 약 94만 원이 줄어듭니다', '예측하는 말이 아니라',
                 '남은 현금과 급한 마음', 'takeaway', 'emphasis', '계좌 전체의 주식 비중과 현금 비중',
                 '본전을 빨리 되찾는 것보다', "손실 금액 표시값으로 시작해 '~하기보다,'로 잇는 꼴"):
        assert text in prompt
    assert '비중 상위 3종목 합계' not in prompt and 'symbol' not in prompt
    no_rate = AssetCoachRequest(facts={**FACTS, 'return_pct': None})
    assert '수익률: ' not in build_asset_coach_prompt(no_rate)
    # 위험 가늠 값이 없으면 가정 숫자를 쓰게 하지 않는다.
    old = build_asset_coach_prompt(AssetCoachRequest(facts=OLD_FACTS))
    assert '가정 계산(예측 아님)' not in old and '가정한 숫자는 만들지 마세요' in old
    profit = {**OLD_FACTS, 'tone': 'profit', 'market_total': '14613484', 'unrealized_pnl': '2613484', 'worst_holding': None}
    profit_prompt = build_asset_coach_prompt(AssetCoachRequest(facts=profit))
    assert '이번 수익을 받아들이는 태도' in profit_prompt and '이번 수익의 크기보다' in profit_prompt
    assert '본전을 빨리 되찾는' not in profit_prompt and '수익 금액 표시값으로 시작해' in profit_prompt


def test_won_display():
    assert won(-2613484) == '약 261만 원'
    assert won(123456789) == '약 1억 2,346만 원'
    assert won(9500) == '9,500원'


def test_accepts_fact_numbers_and_names_with_digits():
    result = validate_asset_coach(draft(), REQUEST.facts)
    assert result.disclaimer == '모의투자 연습용 설명이며 투자 권유가 아닙니다.'
    assert len(result.checkpoints) == 3
    # 정확한 금액, 만 원 내림, 비율 반올림도 사실에서 나온 숫자로 본다.
    validate_asset_coach(draft(headline='평가손실 2,613,484원, 약 261만 원이에요.'), REQUEST.facts)
    validate_asset_coach(draft(headline='수익률 -21.78%, 22% 가까운 손실이에요.'), REQUEST.facts)


def test_emphasis_keeps_only_phrases_found_in_the_body():
    result = validate_asset_coach(draft(emphasis=['가상전자 비중이 62.5%', '본문에 없는 말', '가상전자 비중이 62.5%',
                                                '평가손실이 약 261만 원', ' 기준을 세우고 기록하는 연습 ']), REQUEST.facts)
    # 제목(headline)에만 있는 구절과 본문에 없는 구절은 버린다.
    assert result.emphasis == ['가상전자 비중이 62.5%', '기준을 세우고 기록하는 연습']
    marked = draft()
    marked['checkpoints'][1]['body'] = '**가상전자 비중이 62.5%**로 커요. 나뉜 정도를 확인해 보세요.'
    result = validate_asset_coach(marked, REQUEST.facts)
    assert '*' not in result.checkpoints[1].body and result.checkpoints[0].detail is None
    assert result.takeaway.startswith('모의투자인 지금')


def test_scenario_needs_fact_numbers_and_a_not_a_forecast_note():
    plain = draft()
    plain['checkpoints'][2]['detail'] = '그 금액이 부담스럽다면 현금 비중을 검토해 보세요.'
    with pytest.raises(CoachRejected) as error:
        validate_asset_coach(plain, REQUEST.facts)
    assert str(error.value) == 'unlabeled_scenario'
    with pytest.raises(CoachRejected) as error:
        validate_asset_coach(draft(), AssetCoachRequest(facts=OLD_FACTS).facts)
    assert str(error.value) == 'unsupported_number'


@pytest.mark.parametrize('change,rule', [
    (dict(headline='삼성전자를 지금 매수하세요.'), 'forbidden'),
    (dict(headline='곧 회복할 전망이에요. 약 261만 원 손실이에요.'), 'forbidden'),
    (dict(headline='수익을 보장할 수는 없지만 약 261만 원 손실이에요.'), 'forbidden'),
    (dict(intro='가상전자 비중을 줄여 보세요. 손실은 약 261만 원이에요.'), 'stock_directive'),
    (dict(intro='가상전자는 손절을 고려해 보세요. 손실은 약 261만 원이에요.'), 'stock_directive'),
    (dict(intro='예시로 20% 더 떨어지면 손실은 약 300만 원이 돼요.'), 'unsupported_number'),
    (dict(intro='약 261만 원 손실이에요. 세 종목 비중을 줄일지 검토해 보세요.'), 'stock_directive'),
    (dict(intro='약 261만 원 손실이에요. 더 떨어질 것 같다면 기준을 확인해 보세요.'), 'forbidden'),
    (dict(takeaway='추가 매수하지 마세요.'), 'forbidden'),
    (dict(takeaway=''), 'length'),
    (dict(takeaway='가' * 161), 'length'),
    (dict(headline='차분히 점검해 봐요.', intro='손실이 큰 편이에요. 아래를 확인해 보세요.',
          checkpoints=[dict(title='마음가짐', body='처음 산 이유를 돌아보세요.', detail=None)] * 3), 'no_fact'),
    (dict(checkpoints=draft()['checkpoints'][:2]), 'checkpoint_count'),
    (dict(checkpoints=[dict(title='가' * 41, body='약 261만 원 손실이에요.', detail=None)] * 3), 'length'),
    (dict(checkpoints=[dict(title='마음가짐', body='약 261만 원 손실이에요.')] * 3), 'schema'),
    (dict(headline='가' * 61), 'length'),
    (dict(headline='약 261만 원 손실\n이에요.'), 'format'),
    (dict(headline='https://example.com 약 261만 원'), 'format'),
    (dict(extra='x'), 'schema'),
])
def test_rejects_unsafe_or_unsupported_output(change, rule):
    with pytest.raises(CoachRejected) as error:
        validate_asset_coach(draft(**change), REQUEST.facts)
    assert str(error.value) == rule


def _generator(*outputs):
    call = Mock(side_effect=[SimpleNamespace(output_text=json.dumps(o), usage=None) for o in outputs])
    return AssetCoachGenerator(client=SimpleNamespace(responses=SimpleNamespace(create=call))), call


def test_generator_retries_once_only_for_rejected_output():
    gen, call = _generator(draft(headline='삼성전자를 매수하세요.'), draft())
    assert gen.generate(REQUEST, uuid4()).headline.startswith('평가손실')
    assert call.call_count == 2
    kw = call.call_args.kwargs
    assert kw['model'] == 'gpt-6-luna' and kw['reasoning'] == {'effort': 'none'} and kw['text']['format']['strict']
    gen, call = _generator(draft(headline='매수하세요.'), draft(headline='매도하세요.'), draft())
    with pytest.raises(CoachRejected):
        gen.generate(REQUEST, uuid4())
    assert call.call_count == 2


def test_generator_does_not_retry_provider_errors():
    call = Mock(side_effect=RuntimeError('down'))
    gen = AssetCoachGenerator(client=SimpleNamespace(responses=SimpleNamespace(create=call)))
    with pytest.raises(SummarizerError) as error:
        gen.generate(REQUEST, uuid4())
    assert not isinstance(error.value, CoachRejected)
    call.assert_called_once()


def test_route_auth_and_error_mapping(monkeypatch):
    body = json.loads(REQUEST.model_dump_json())

    class Failing:
        def generate(self, *args):
            raise CoachRejected('forbidden')

    class Working:
        def generate(self, *args):
            return validate_asset_coach(draft(), REQUEST.facts)

    auth = {'Authorization': 'Bearer unit-test-service-token'}
    try:
        with TestClient(app) as client:
            app.dependency_overrides[get_generator] = Failing
            assert client.post('/brief/asset-coach', json=body).status_code == 401
            assert client.post('/brief/asset-coach', json=body, headers=auth).status_code == 502
            app.dependency_overrides[get_generator] = Working
            response = client.post('/brief/asset-coach', json=body, headers=auth)
            assert response.status_code == 200 and len(response.json()['checkpoints']) == 3
            assert client.post('/brief/asset-coach', json={'facts': {**body['facts'], 'tone': 'profit'}}, headers=auth).status_code == 422
            monkeypatch.delenv('AI_SERVICE_TOKEN')
            assert client.post('/brief/asset-coach', json=body).status_code == 503
    finally:
        app.dependency_overrides.clear()


# 실제 계좌 모양(6종목, 한 종목 쏠림, 손실)으로 검사 규칙을 확인한다.
def _h(name, weight, pnl, rate):
    return dict(name=name, weight_pct=weight, pnl=pnl, return_pct=rate)


GARAM, NARAE, DAON = _h('가람전자', '79.72', '-2700000', '-15.43'), _h('나래전자', '15.84', '-210000', '-6.67'), _h('다온반도체', '3.91', '45200', '6.65')
ACCOUNT = dict(as_of='2026-11-10T18:55:00+09:00', tone='loss', severity='large', cost_total='21430000', market_total='18565200',
               cash='630000', unrealized_pnl='-2864800', return_pct='-13.37', cash_weight_pct='3.28', holdings_count=6,
               losing_count=2, gaining_count=1, concentration='concentrated', top_holdings=[GARAM, NARAE, DAON],
               top3_weight_pct='99.47', worst_holding=GARAM, best_holding=DAON, stress_drop_pct='10', stress_drop_amount='1856520')
ACCOUNT_FACTS = AssetCoachRequest(facts=ACCOUNT).facts


def advice(**change):
    """c1_body처럼 쓰면 둘째 항목의 본문을 바꾼다."""
    data = dict(
        headline='손실 약 286만 원을 서둘러 만회하기보다, 가람전자 쏠림부터 살펴봅니다.',
        intro='평가금액의 79.7%가 가람전자에 몰려 있습니다. 지금은 현금을 더 넣는 일을 서두르지 않는 편이 좋습니다.',
        checkpoints=[dict(title='현금 약 63만 원은 그대로 둡니다', body='현금을 더 넣기 전에 보유 이유부터 확인해 보세요.', detail=None),
                     dict(title='가람전자에 몰린 비중부터 점검합니다', body='가람전자가 평가금액의 79.7%예요.', detail=None),
                     dict(title='추가로 감당할 수 있는 손실을 금액으로 정합니다',
                          body='평가금액 약 1,857만 원이 여기서 10% 더 내리면 약 186만 원이 줄어듭니다. 하락을 예측하는 말이 아니라 위험을 가늠해 보는 계산이에요.', detail=None)],
        takeaway='본전 회복보다 매수·보유·매도 기준을 세우고 기록하는 연습을 해 봐요.', emphasis=[])
    for key, value in change.items():
        if key[0] == 'c' and key[1].isdigit():
            data['checkpoints'][int(key[1])][key[3:]] = value
        else:
            data[key] = value
    return data


def verdict(**change):
    try:
        validate_asset_coach(advice(**change), ACCOUNT_FACTS)
    except CoachRejected as error:
        return str(error)
    return None


@pytest.mark.parametrize('change', [
    dict(),
    # '목표 + 가'는 목표 주가가 아니다.
    dict(c0_body='처음 세운 목표가 무엇이었는지 돌아보세요.'),
    # 예측이 아니라고 밝히는 문장은 예측 표현으로 보지 않는다.
    dict(c2_body='평가금액 약 1,857만 원이 여기서 10% 더 내리면 약 186만 원이 줄어듭니다. 떨어질 것이라고 예측하는 말이 아니에요.'),
    # 왜 샀는지 돌아보게 하는 말은 매매 지시가 아니다.
    dict(c1_title='가람전자 매수 이유를 다시 확인해요'),
    dict(c1_body='가람전자를 매수한 이유를 적어 보세요. 비중은 79.7%예요.'),
    dict(c1_detail='가람전자를 보유한 이유를 정리해 보세요.'),
    dict(c0_detail='기준을 정리하세요.'),
    dict(intro='평가금액의 약 80%가 가람전자에 몰려 있습니다. 아래 세 가지를 확인해 봐요.'),
    dict(intro='6개 종목 중 2개가 손실이에요. 아래 세 가지를 확인해 봐요.'),
    dict(intro='다온반도체에서는 수익 약 5만 원이 났어요. 그래도 가람전자 비중이 79.7%예요.'),
    dict(c2_detail='그 금액도 부담스럽다면 계좌 전체의 주식 비중과 현금 비중을 어떻게 둘지 검토해 보세요.'),
    dict(c1_detail='“지금 이 주식 대신 같은 금액의 현금을 들고 있다면, 다시 이만큼 살까?” 하고 스스로 물어보세요.'),
    # 합쇼체로 쓴 같은 뜻의 문장.
    dict(c2_body='평가금액 약 1,857만 원이 여기서 10% 더 내리면 약 186만 원이 줄어듭니다. 떨어질 것이라고 예측하는 말이 아닙니다.'),
    dict(c0_body='처음 세운 목표가 무엇이었는지 돌아보시기 바랍니다.', c0_detail='기준을 정리하십시오.'),
    dict(c1_detail='가람전자를 보유한 이유를 정리해 보십시오.'),
])
def test_ordinary_coaching_sentences_pass(change):
    assert verdict(**change) is None


@pytest.mark.parametrize('change,rule', [
    # 종목을 찍지 않아도 더 사라고 권하는 말.
    (dict(c0_body='현금 약 63만 원으로 추가 매수해 보세요.'), 'forbidden'),
    (dict(c0_body='현금 약 63만 원으로 물타기를 해 보는 것도 방법이에요.'), 'forbidden'),
    (dict(c1_body='가람전자를 더 담아 보세요. 비중은 79.7%예요.'), 'forbidden'),
    # 이름 대신 가리키는 말로 팔거나 줄이라고 하는 말.
    (dict(c1_body='가람전자가 평가금액의 79.7%예요. 비중이 가장 큰 종목은 일부 매도를 검토해요.'), 'stock_directive'),
    (dict(c1_body='가람전자가 평가금액의 79.7%예요. 한 종목 비중을 줄여 보세요.'), 'stock_directive'),
    (dict(c1_body='가람전자가 평가금액의 79.7%예요. 손실 종목은 먼저 정리해 보세요.'), 'stock_directive'),
    (dict(c1_body='가람전자는 정리를 검토해 보세요. 비중은 79.7%예요.'), 'stock_directive'),
    (dict(c1_detail='어느 종목부터 팔아야 한다고 단정할 수는 없어요.'), 'stock_directive'),
    # 계좌 전체를 두고 시키는 말.
    (dict(c2_detail='부담스럽다면 주식을 일부 팔아 현금을 확보하세요.'), 'forbidden'),
    (dict(c2_detail='부담스럽다면 주식 비중을 줄이세요.'), 'forbidden'),
    (dict(c2_detail='부담스럽다면 주식을 정리하세요.'), 'forbidden'),
    (dict(c2_detail='부담스럽다면 일부를 현금화하세요.'), 'forbidden'),
    # 합쇼체로 시키거나 권하는 말.
    (dict(c2_detail='부담스럽다면 주식 비중을 줄이십시오.'), 'forbidden'),
    (dict(c2_detail='부담스럽다면 주식을 정리하시기 바랍니다.'), 'forbidden'),
    (dict(c2_detail='주식을 일부 팔아 현금을 확보하십시오.'), 'forbidden'),
    (dict(c0_body='현금 약 63만 원으로 더 사 보시기 바랍니다.'), 'forbidden'),
    (dict(takeaway='지금은 매수하지 마십시오.'), 'forbidden'),
    (dict(takeaway='지금은 손절하시기 바랍니다.'), 'forbidden'),
    (dict(c0_body='더 떨어질 것입니다. 보유 이유부터 확인해 보십시오.'), 'forbidden'),
    # 오르는 쪽 가정과 손익 방향이 뒤바뀐 말.
    (dict(c2_body='평가금액 약 1,857만 원이 10% 오르면 약 186만 원이 늘어요.'), 'upside_scenario'),
    (dict(headline='수익 약 286만 원에 들뜨기보다, 가람전자 쏠림부터 살펴봅니다.'), 'wrong_direction'),
    (dict(intro='약 286만 원의 수익이 났어요. 가람전자 비중은 79.7%예요.'), 'wrong_direction'),
    # 그대로 막는 것.
    (dict(c0_body='더 떨어질 것 같아 불안하다면 보유 이유부터 확인해 보세요.'), 'forbidden'),
    (dict(c0_body='가람전자의 목표가를 다시 확인해 보세요.'), 'forbidden'),
    (dict(c0_body='목표 주가에 닿았는지 확인해 보세요.'), 'forbidden'),
    (dict(c2_detail='주식은 원금이 보장되지 않는다는 점을 떠올려 보세요.'), 'forbidden'),
    (dict(takeaway='손실이니까 무조건 버틴다는 마음 대신 기준을 기록해 봐요.'), 'forbidden'),
    (dict(intro='평가금액의 79.7%가 가람전자에 몰려 있습니다. 10% 더 내리면 얼마가 줄어드는지도 확인해 봐요.'), 'unlabeled_scenario'),
    (dict(c1_title='가람미국필라델피아반도체나스닥·나래미국에스앤피오백·다온반도체를 먼저 점검합니다'), 'length'),
    (dict(headline='손실 약 ○○만 원을 서둘러 만회하기보다, 가람전자 쏠림부터 살펴봅니다.'), 'format'),
])
def test_risky_or_malformed_sentences_are_rejected(change, rule):
    assert verdict(**change) == rule


@pytest.mark.parametrize('change', [
    # 아직 막지 않는 문장. 규칙을 넣으면 위의 거부 목록으로 옮긴다.
    dict(c1_body='가람전자는 좋은 종목이에요. 비중은 79.7%예요.'),
    dict(c1_body='가람전자는 곧 반등할 수 있어요. 비중은 79.7%예요.'),
    dict(c0_body='기다리면 회복돼요. 현금 약 63만 원은 그대로 둡니다.'),
    dict(c1_body='가람전자에서 수익 약 270만 원이 났어요.'),
    dict(c0_body='3개월 안에 손실 약 286만 원을 메워 보세요.'),
    dict(intro='비중이 작은 4종목도 있어요. 아래 세 가지를 확인해 봐요.'),
])
def test_known_gaps_still_pass(change):
    assert verdict(**change) is None


def test_emphasis_is_one_per_paragraph_and_never_ends_mid_sentence():
    data = advice(c1_body='가람전자가 평가금액의 79.7%이고, 나래전자는 15.8%예요.',
                  emphasis=['가람전자가 평가금액의 79.7%이고', '나래전자는 15.8%', '가람전자가 평가금액의 79.7%',
                            '약 186만 원이 줄어듭니다.', '기준을 세우고 기록하는 연습', '현금을 더 넣기 전에'])
    result = validate_asset_coach(data, ACCOUNT_FACTS)
    # 이어지는 말('~이고', '~전에')로 끝난 구절과 같은 문단의 둘째 구절은 버리고, 끝의 마침표는 떼고, 세 개까지만 남긴다.
    assert result.emphasis == ['나래전자는 15.8%', '약 186만 원이 줄어듭니다', '기준을 세우고 기록하는 연습']
    # 문장을 통째로 옮긴 긴 구절과 꾸미는 말에서 끊긴 구절은 굵게 하지 않는다.
    whole = '평가금액 약 1,857만 원이 여기서 10% 더 내리면 약 186만 원이 줄어듭니다.'
    data = advice(c0_body='손실 약 270만 원을 기록한 가람전자를 계속 보유할 이유부터 확인해 보세요.',
                  emphasis=[whole, '손실 약 270만 원을 기록한', '가람전자가 평가금액의 79.7%'])
    assert validate_asset_coach(data, ACCOUNT_FACTS).emphasis == ['가람전자가 평가금액의 79.7%']


def test_prompt_avoids_repetition_and_leaking_fact_labels():
    prompt = build_asset_coach_prompt(AssetCoachRequest(facts=ACCOUNT))
    # 한 종목 쏠림 계좌에서는 상위 3종목 합계를 빼고 가장 큰 종목을 중심으로 쓴다.
    assert '비중 상위 3종목 합계' not in prompt and "형식 예: 'A에 몰린 비중부터 점검합니다'" in prompt
    assert prompt.count('계좌 전체의 주식 비중과 현금 비중') == 1
    for text in ('headline에 쓴 손익 금액을 다시 쓰지 말고', '아래 항목 제목을 나열하지 마세요', "'그 금액'", 'title에는 금액을 넣지 않습니다',
                 "'위험 가늠 예시로', '가정 계산으로', '이는' 같은 말로 시작하지 마세요", '가정 계산(예측 아님)',
                 '“지금 이 주식 대신 같은 금액의 현금을 들고 있다면, 다시 이만큼 살까?”', '따옴표와 함께',
                 '굵게 보여 줄 핵심 구절 2~3개', '한 문단에서 하나만', "'~하고', '~전에', '~한'처럼 이어지는 말에서 끊지 마세요",
                 '문장을 통째로 옮기지 말고 마침표 없이', '비중 상위 종목(주식 평가금액 안에서의 비중): 가람전자 (비중 79.7%',
                 "'이는', '이 계산은'으로 문장을 시작하지 마세요", '얼마의 손익이 났다는 사실만 씁니다',
                 '손실이 가장 큰 종목은 첫째나 둘째 body에서 이름과 금액으로 한 번만 짚습니다', '추가로 감당할 수 있는 손실을 금액으로 정합니다',
                 "'추가 매수해 보세요', '물타기를 해 보세요'", "오르는 쪽 가정('10% 오르면')은 쓰지 마세요"):
        assert text in prompt
    spread = {**ACCOUNT, 'concentration': 'moderate'}
    assert '비중 상위 3종목 합계: 평가금액의 99.5%' in build_asset_coach_prompt(AssetCoachRequest(facts=spread))
    profit = {**ACCOUNT, 'tone': 'profit', 'cost_total': '15700400', 'unrealized_pnl': '2864800', 'return_pct': '18.25',
              'worst_holding': None, 'best_holding': _h('가람전자', '79.72', '2700000', '22.31')}
    profit_prompt = build_asset_coach_prompt(AssetCoachRequest(facts=profit))
    assert '수익이 가장 큰 종목은 첫째나 둘째 body에서' in profit_prompt and '수익이 줄어도 괜찮은 폭을 금액으로 정합니다' in profit_prompt


def _shape(**change):
    return build_asset_coach_prompt(AssetCoachRequest(facts={**ACCOUNT, **change}))


def test_prompt_follows_the_shape_of_the_account():
    usual = _shape()
    assert '남은 현금과 급한 마음' in usual and '현금 비중: 3.3% (현금 약 63만 원)' in usual
    assert '계좌 전체(현금 포함)에서 주식 비중' not in usual

    # 작은 손익: 만회를 서두르라는 틀을 쓰지 않는다.
    small = _shape(severity='small', cost_total='18600000', unrealized_pnl='-34800', return_pct='-0.19')
    assert '작은 손실을 대하는 태도' in small and "형식 예: '작은 손실에 마음 쓰기보다," in small
    assert '방향: 작은 손익에 마음 쓰기보다' in small and '본전을 빨리 되찾는 것보다' not in small
    assert '서둘러 만회하기보다' not in small and '남은 현금과 급한 마음' not in small

    # 현금이 절반 이상: 쏠림을 과장하지 않고 계좌 전체에서 주식 비중을 알려 준다.
    rich = _shape(cash='60000000', cash_weight_pct='76.37')
    assert '계좌 전체(현금 포함)에서 주식 비중: 23.6%' in rich and '계좌 대부분이 현금입니다' in rich
    assert "'주식 평가금액 안에서'라고 밝히고" in rich and "현금이 대부분인 계좌라 '쏠림', '몰린'이라고 쓰지 않습니다" in rich
    assert '다음 주문에 얼마까지 쓸지 정해 보자고' in rich and '계좌 전체의 주식 비중과 현금 비중' not in rich
    said = advice(intro='계좌 전체로는 주식이 23.6%뿐이에요. 지금은 다음 주문을 서두르지 않는 편이 좋습니다.',
                  c0_title='남은 현금 약 6,000만 원 중 다음 주문에 쓸 금액을 정합니다')
    validate_asset_coach(said, AssetCoachRequest(facts={**ACCOUNT, 'cash': '60000000', 'cash_weight_pct': '76.37'}).facts)

    # 현금이 1만 원 미만: '남은 현금 0원'을 쓰게 하지 않는다.
    empty = _shape(cash='0', cash_weight_pct='0')
    assert '- 현금: 거의 없습니다' in empty and '현금 비중:' not in empty and '0원' not in empty
    assert '남은 현금이 거의 없으니 현금 금액은 쓰지 말고' in empty and '남은 현금 ○○만 원은' not in empty

    # 한 종목만 보유: 100% 쏠림이라고 쓰지 않고 단수로 가리킨다.
    only = _h('가람전자', '100', '-2864800', '-13.37')
    single = _shape(holdings_count=1, losing_count=1, gaining_count=0, top_holdings=[only], top3_weight_pct=None,
                    worst_holding=only, best_holding=None)
    assert '- 쏠림: 주식은 한 종목뿐입니다' in single and '100.0%' not in single and '한 종목 보유.' in single
    assert '- 보유 종목: 가람전자 (손실 약 286만 원, 수익률 -13.4%)' in single and '비중 상위 종목' not in single
    assert "'이 종목들'처럼 여럿으로 가리키지 않습니다" in single and '이 종목을 보유한 이유부터 살펴봅니다' in single
    assert '손실이 가장 큰 종목은 첫째나 둘째 body에서' not in single

    # 새로 만든 계좌: 한 종목, 작은 손익, 현금 대부분.
    fresh = _shape(holdings_count=1, losing_count=1, gaining_count=0, top_holdings=[_h('가람전자', '100', '-16000', '-0.54')],
                   top3_weight_pct=None, worst_holding=_h('가람전자', '100', '-16000', '-0.54'), best_holding=None,
                   severity='small', cost_total='2976000', market_total='2960000', unrealized_pnl='-16000', return_pct='-0.54',
                   cash='56000000', cash_weight_pct='94.98', stress_drop_amount='296000')
    assert '계좌 전체로는 주식이 5.0%뿐이고 나머지는 현금이라는 점을 함께 씁니다' in fresh
    assert "형식 예: '작은 손실에 마음 쓰기보다, 다음 주문의 기준부터 정해 봅니다.'" in fresh

    # 고르게 나뉜 계좌: 쏠림 대신 종목 수를 짚는다.
    even = _shape(concentration='diversified', top_holdings=[_h('가람전자', '18', '-300000', '-5'), _h('나래전자', '17', '-210000', '-6.67')],
                  top3_weight_pct='51', worst_holding=_h('가람전자', '18', '-300000', '-5'))
    assert '여러 종목 보유.' in even and '보유 종목 수와 손실·수익 종목 수를 짚습니다' in even
    assert '종목마다 보유한 이유부터 살펴봅니다' in even and '몰린 비중부터' not in even
    assert '비중 상위 3종목 합계: 평가금액의 51.0%' in even
