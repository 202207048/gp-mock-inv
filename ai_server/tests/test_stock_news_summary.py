import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
from app.provider_manager import RuntimeProviderManager
from app.schemas import StockNewsBrief, StockNewsSummaryRequest
from app.summarizers.base import SummarizerError, SummarizerUnavailableError, build_stock_news_prompt
from app.summarizers.gemini import GeminiStockNewsSummarizer
from app.summarizers.openai import OpenAIStockNewsSummarizer


REQUEST = {
    "symbol_code": "005930", "name": "Example", "change_rate": 1.2,
    "articles": [
        {"title": "Revenue increased", "summary": "Reported growth", "source": "Source", "date": "today"},
        {"title": "Outlook unchanged", "summary": "No change"},
    ],
}
BRIEF = {
    "headline": "오늘은 +1.2% 올랐고, 기사에서 같이 보이는 이슈는 실적입니다.",
    "summary": ["실적 증가를 발표했습니다.", "등락 원인인지는 기사만으로 확인되지 않습니다."],
    "tags": ["실적"], "disclaimer": "모의투자 연습용 설명이며 투자 권유가 아닙니다.",
}


class FakeSummarizer:
    def __init__(self, provider="openai", model="test-model"):
        self.provider, self.model = provider, model
        self.requests = []

    def summarize(self, request):
        self.requests.append(request)
        return StockNewsBrief(**BRIEF)


@pytest.fixture
def client():
    fake = FakeSummarizer()
    main.app.dependency_overrides[main.get_stock_news_summarizer] = lambda: fake
    try:
        with TestClient(main.app) as client:
            yield client, fake
    finally:
        main.app.dependency_overrides.pop(main.get_stock_news_summarizer, None)


@pytest.mark.parametrize("field,value", [
    ("symbol_code", " "), ("name", ""), ("articles", []),
    ("articles", [{"title": "x"}] * 6), ("articles", [{"title": ""}]),
    ("articles", [{"title": "x", "summary": "x" * 20001}]),
    ("change_rate", "nan"), ("unexpected", True),
])
def test_request_validation(client, field, value):
    api, fake = client
    assert api.post("/summarize/stock-news", json={**REQUEST, field: value}).status_code == 422
    assert not fake.requests


def test_stock_level_synthesis_contract(client):
    api, fake = client
    response = api.post("/summarize/stock-news", json=REQUEST)
    assert response.status_code == 200
    assert response.json() == {**BRIEF, "provider": "openai", "model": "test-model"}
    assert [a.title for a in fake.requests[0].articles] == [a["title"] for a in REQUEST["articles"]]
    assert len(fake.requests) == 1  # One synthesis, no per-article output/mapping.


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_runtime_switch_selects_summary_provider(monkeypatch, provider):
    manager = RuntimeProviderManager("openai", openai_key_getter=lambda: "unit-test-openai",
                                     gemini_key_getter=lambda: "unit-test-gemini")
    monkeypatch.setattr(main.app.state, "provider_manager", manager)
    made = []
    for label, attr in [("openai", "OpenAIStockNewsSummarizer"), ("gemini", "GeminiStockNewsSummarizer")]:
        def create(*, model, label=label):
            fake = FakeSummarizer(label, model)
            made.append(fake)
            return fake
        monkeypatch.setattr(main, attr, create)
    with TestClient(main.app) as api:
        assert api.put("/settings/provider", json={"provider": provider}).status_code == 200
        expected = api.get("/settings/provider").json()
        result = api.post("/summarize/stock-news", json=REQUEST)
        assert result.status_code == 200
        assert {k: result.json()[k] for k in ("provider", "model")} == expected
    assert len(made) == 1 and made[0].provider == provider


def test_provider_metadata_is_request_snapshot(monkeypatch):
    manager = RuntimeProviderManager("openai", gemini_key_getter=lambda: "unit-test-key")
    monkeypatch.setattr(main.app.state, "provider_manager", manager)
    class SwitchingFake(FakeSummarizer):
        def summarize(self, request):
            manager.set_provider("gemini")
            return super().summarize(request)
    monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", lambda **kw: SwitchingFake("openai", kw["model"]))
    with TestClient(main.app) as api:
        result = api.post("/summarize/stock-news", json=REQUEST).json()
    assert result["provider"] == "openai"
    assert manager.get_provider() == "gemini"


@pytest.mark.parametrize("failure,status", [(SummarizerError, 502), (SummarizerUnavailableError, 503)])
def test_safe_errors_without_fallback(monkeypatch, failure, status):
    monkeypatch.setattr(main.app.state, "provider_manager", RuntimeProviderManager("openai"))
    alternate = Mock()
    monkeypatch.setattr(main, "GeminiStockNewsSummarizer", alternate)
    if status == 503:
        monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", Mock(side_effect=failure("unit-test-secret-marker")))
    else:
        fake = FakeSummarizer()
        fake.summarize = Mock(side_effect=failure("unit-test-secret-marker"))
        monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", lambda **kw: fake)
    with TestClient(main.app) as api:
        result = api.post("/summarize/stock-news", json=REQUEST)
    assert result.status_code == status
    assert "unit-test-secret-marker" not in result.text
    alternate.assert_not_called()


def test_safe_initialization_failure(monkeypatch):
    monkeypatch.setattr(main.app.state, "provider_manager", RuntimeProviderManager("openai"))
    monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", Mock(side_effect=RuntimeError("unit-test-secret-marker")))
    with TestClient(main.app) as api:
        result = api.post("/summarize/stock-news", json=REQUEST)
    assert result.status_code == 502 and "unit-test-secret-marker" not in result.text


def test_openai_injected_client_structured_output():
    sdk = SimpleNamespace(responses=SimpleNamespace(parse=Mock(return_value=SimpleNamespace(output_parsed=StockNewsBrief(**BRIEF)))))
    summarizer = OpenAIStockNewsSummarizer(api_key="unit-test-secret-marker", client=sdk)
    assert summarizer.summarize(StockNewsSummaryRequest(**REQUEST)).model_dump() == BRIEF
    args = sdk.responses.parse.call_args.kwargs
    assert args["text_format"] is StockNewsBrief
    assert args["reasoning"] == {"effort": "none"}
    assert "unit-test-secret-marker" not in args["input"]
    assert "Revenue increased" in args["input"] and "Outlook unchanged" in args["input"]
    sdk.responses.parse.assert_called_once()


def test_gemini_injected_client_structured_output():
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(text=json.dumps(BRIEF)))))
    summarizer = GeminiStockNewsSummarizer(api_key="unit-test-key", client=sdk)
    assert summarizer.summarize(StockNewsSummaryRequest(**REQUEST)).model_dump() == BRIEF
    args = sdk.models.generate_content.call_args.kwargs
    assert args["config"].response_json_schema == StockNewsBrief.model_json_schema()
    sdk.models.generate_content.assert_called_once()


@pytest.mark.parametrize("cls", [OpenAIStockNewsSummarizer, GeminiStockNewsSummarizer])
@pytest.mark.parametrize("bad", [None, "not-json", {**BRIEF, "summary": "wrong-type"}, {**BRIEF, "tags": ["a"] * 4}])
def test_invalid_provider_output(cls, bad):
    if cls is OpenAIStockNewsSummarizer:
        sdk = SimpleNamespace(responses=SimpleNamespace(parse=Mock(return_value=SimpleNamespace(output_parsed=bad))))
    else:
        raw = bad if isinstance(bad, str) or bad is None else json.dumps(bad)
        sdk = SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(text=raw))))
    with pytest.raises(SummarizerError, match="stock-news summary failed"):
        cls(api_key="unit-test-key", client=sdk).summarize(StockNewsSummaryRequest(**REQUEST))


@pytest.mark.parametrize("cls", [OpenAIStockNewsSummarizer, GeminiStockNewsSummarizer])
def test_missing_key(cls):
    with pytest.raises(SummarizerUnavailableError):
        cls(api_key=" ", client=object())


@pytest.mark.parametrize("rate,expected", [(1.2, "+1.2%"), (-2.0, "-2%"), (0, "0%"), (None, "등락률 미확인")])
def test_prompt_preserves_price_and_excerpt_scope(rate, expected):
    request = StockNewsSummaryRequest(**{**REQUEST, "change_rate": rate})
    prompt = build_stock_news_prompt(request)
    assert expected in prompt and "전체 본문이 아닙니다" in prompt
    assert "원인으로 단정하지" in prompt


def test_output_schema_rejects_empty_or_extra_fields():
    for data in [{**BRIEF, "headline": " "}, {**BRIEF, "summary": []}, {**BRIEF, "extra": True}]:
        with pytest.raises(ValidationError):
            StockNewsBrief(**data)


@pytest.mark.parametrize("cls,module", [
    (OpenAIStockNewsSummarizer, "openai"), (GeminiStockNewsSummarizer, "google.genai"),
])
def test_sdk_initialization_is_bounded_and_does_not_retry(monkeypatch, cls, module):
    import importlib
    sdk = importlib.import_module(module)
    factory = Mock()
    monkeypatch.setattr(sdk, "OpenAI" if module == "openai" else "Client", factory)
    cls(api_key="unit-test-key")
    kwargs = factory.call_args.kwargs
    if module == "openai":
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 25.0
    else:
        assert kwargs["http_options"].timeout == 25000
        assert kwargs["http_options"].retry_options.attempts == 1


def test_openai_official_sdk_summary_roundtrip_without_network():
    import httpx
    from openai import OpenAI
    captured = {}
    def handler(request):
        captured.update(json.loads(request.read()))
        return httpx.Response(200, json={
            "id": "resp_test", "object": "response", "created_at": 1,
            "status": "completed", "model": "gpt-6-luna",
            "output": [{"type": "message", "id": "msg_test", "status": "completed",
                        "role": "assistant", "content": [{"type": "output_text", "annotations": [],
                                                           "text": json.dumps(BRIEF)}]}],
        })
    with OpenAI(api_key="unit-test-key", max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as sdk:
        brief = OpenAIStockNewsSummarizer(api_key="unit-test-key", client=sdk).summarize(StockNewsSummaryRequest(**REQUEST))
    assert brief.model_dump() == BRIEF
    assert captured["text"]["format"]["type"] == "json_schema"
    assert captured["text"]["format"]["strict"] is True
    assert "provider" not in captured["text"]["format"]["schema"]["properties"]
