"""Generation observations tested only with injected SDK clients/objects."""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
import app.execution as execution
from app.analyzers.base import AnalyzerError
from app.analyzers.openai import OpenAINewsAnalyzer
from app.analyzers.gemini import GeminiNewsAnalyzer
from app.schemas import Article, NewsSentimentBatch, StockNewsBrief, StockNewsSummaryRequest, UsageMetrics
from app.summarizers.openai import OpenAIStockNewsSummarizer
from app.summarizers.gemini import GeminiStockNewsSummarizer
from app.summarizers.base import SummarizerError

PREDICTIONS = {"articles": [{"article_id": "a", "sentiment": {"label": "NEUTRAL", "confidence": 0.5}, "importance_score": 0.5}]}
ARTICLES = [Article(article_id="a", title="Example")]
BRIEF = {"headline": "가상 뉴스 요약", "summary": ["제공된 발췌문입니다."], "tags": [], "disclaimer": "테스트 안내"}
REQUEST = {"symbol_code": "005930", "name": "Example", "articles": [{"title": "Example"}]}


@pytest.mark.parametrize("values", [
    {}, {"input_tokens": 10}, {"input_tokens": 0, "reasoning_tokens": 0},
    dict(input_tokens=10, output_tokens=20, reasoning_tokens=5, total_tokens=30, cached_tokens=2),
])
def test_usage_nullable_and_provided_zero(values):
    usage = UsageMetrics(**values)
    for name in UsageMetrics.model_fields:
        assert getattr(usage, name) == values.get(name)
    with pytest.raises(ValidationError):
        usage.input_tokens = 0


@pytest.mark.parametrize("bad", [-1, True, "1", 1.5])
def test_usage_rejects_invalid_counts(bad):
    with pytest.raises(ValidationError):
        UsageMetrics(input_tokens=bad)


def test_openai_official_usage_fields_and_missing_total():
    from openai.types.responses.response_usage import ResponseUsage, InputTokensDetails, OutputTokensDetails
    usage = ResponseUsage.model_construct(
        input_tokens=10, output_tokens=20, total_tokens=30,
        input_tokens_details=InputTokensDetails.model_construct(cached_tokens=2),
        output_tokens_details=OutputTokensDetails.model_construct(reasoning_tokens=5),
    )
    normalized = execution.openai_usage(NS(usage=usage))
    assert normalized.model_dump() == dict(input_tokens=10, output_tokens=20, reasoning_tokens=5, total_tokens=30, cached_tokens=2)
    assert execution.openai_usage(NS(usage=NS(input_tokens=0))).model_dump() == dict(input_tokens=0, output_tokens=None, reasoning_tokens=None, total_tokens=None, cached_tokens=None)
    assert all(v is None for v in execution.openai_usage(NS()).model_dump().values())


def test_gemini_official_usage_fields_and_missing_total():
    from google.genai.types import GenerateContentResponse, GenerateContentResponseUsageMetadata
    response = GenerateContentResponse(usage_metadata=GenerateContentResponseUsageMetadata(
        prompt_token_count=10, candidates_token_count=20, thoughts_token_count=5,
        total_token_count=35, cached_content_token_count=0,
    ))
    assert execution.gemini_usage(response).model_dump() == dict(input_tokens=10, output_tokens=20, reasoning_tokens=5, total_tokens=35, cached_tokens=0)
    assert execution.gemini_usage(NS(usage_metadata=NS(candidates_token_count=0))).model_dump() == dict(input_tokens=None, output_tokens=0, reasoning_tokens=None, total_tokens=None, cached_tokens=None)
    assert all(v is None for v in execution.gemini_usage(NS()).model_dump().values())


def adapter(provider, task, *, error=None, malformed=False):
    result = PREDICTIONS if task == "analysis" else BRIEF
    parsed = (NewsSentimentBatch if task == "analysis" else StockNewsBrief)(**result)
    if provider == "openai":
        response = NS(output_parsed=None if malformed else parsed, usage=NS(
            input_tokens=10, output_tokens=20, total_tokens=30,
            output_tokens_details=NS(reasoning_tokens=5), input_tokens_details=NS(cached_tokens=0)))
        call = Mock(return_value=response, side_effect=error)
        sdk = NS(responses=NS(parse=call))
        cls = OpenAINewsAnalyzer if task == "analysis" else OpenAIStockNewsSummarizer
    else:
        response = NS(text="not-json" if malformed else json.dumps(result), usage_metadata=NS(
            prompt_token_count=10, candidates_token_count=20, total_token_count=35,
            thoughts_token_count=5, cached_content_token_count=0))
        call = Mock(return_value=response, side_effect=error)
        sdk = NS(models=NS(generate_content=call))
        cls = GeminiNewsAnalyzer if task == "analysis" else GeminiStockNewsSummarizer
    return cls(api_key="unit-test-only", model="test-model", client=sdk), call


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("task", ["analysis", "summary"])
def test_adapters_one_call_context_and_sdk_only_latency(monkeypatch, provider, task):
    measured, call = adapter(provider, task)
    trace = uuid4()
    clock = iter([10.0, 10.025, 20.0, 20.05])
    monkeypatch.setattr(execution, "perf_counter", lambda: next(clock))
    run = measured.analyze_with_metadata if task == "analysis" else measured.summarize_with_metadata
    data = ARTICLES if task == "analysis" else StockNewsSummaryRequest(**REQUEST)
    first = run(data, trace_id=trace)
    second = run(data, trace_id=trace)
    assert call.call_count == 2
    assert first.metadata.trace_id == second.metadata.trace_id == trace
    assert first.metadata.operation_id != second.metadata.operation_id
    assert first.metadata.operation_id.version == 4
    assert first.metadata.latency_ms == pytest.approx(25)
    assert second.metadata.latency_ms == pytest.approx(50)
    assert first.metadata.provider == provider and first.metadata.model == "test-model"
    assert first.metadata.usage.total_tokens == (30 if provider == "openai" else 35)
    assert first.metadata.usage.cached_tokens == 0
    # Opt-in control is not part of the LLM prompt.
    kwargs = call.call_args.kwargs
    assert "include_ai_metadata" not in kwargs.get("input", kwargs.get("contents", ""))


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("task", ["analysis", "summary"])
@pytest.mark.parametrize("malformed", [False, True])
def test_failed_attempt_keeps_operation_without_fabricating_usage(provider, task, malformed):
    measured, call = adapter(provider, task, error=None if malformed else RuntimeError("unit-test-secret-marker"), malformed=malformed)
    run = measured.analyze_with_metadata if task == "analysis" else measured.summarize_with_metadata
    with pytest.raises(AnalyzerError if task == "analysis" else SummarizerError) as caught:
        run(ARTICLES if task == "analysis" else StockNewsSummaryRequest(**REQUEST), trace_id=uuid4())
    metadata = caught.value.metadata
    assert metadata.operation_id.version == 4 and metadata.latency_ms >= 0
    assert metadata.usage.input_tokens == (10 if malformed else None)
    assert "unit-test-secret-marker" not in str(caught.value)
    assert "unit-test-secret-marker" not in metadata.model_dump_json()
    call.assert_called_once()


@pytest.mark.parametrize("provider", ["openai", "gemini"])
@pytest.mark.parametrize("task", ["analysis", "summary"])
def test_routes_opt_in_metadata_and_legacy_shape(monkeypatch, provider, task):
    measured, call = adapter(provider, task)
    dependency = main.get_analyzer if task == "analysis" else main.get_stock_news_summarizer
    path = "/analyze/news" if task == "analysis" else "/summarize/stock-news"
    data = {"articles": [a.model_dump(mode="json") for a in ARTICLES]} if task == "analysis" else REQUEST
    main.app.dependency_overrides[dependency] = lambda: measured
    try:
        with TestClient(main.app, headers={"Authorization": "Bearer unit-test-service-token"}) as api:
            trace = str(uuid4())
            legacy = api.post(path, json=data)
            result = api.post(path, json={**data, "include_ai_metadata": True}, headers={"X-Trace-ID": trace})
            assert result.status_code == legacy.status_code == 200
            assert "ai" not in legacy.json()
            payload = result.json()
            meta = payload.pop("ai")
            assert payload == legacy.json()
            assert meta["trace_id"] == result.headers["X-Trace-ID"] == trace
            assert UUID(meta["operation_id"]).version == 4
            assert meta["provider"] == provider and meta["model"] == "test-model"
            assert meta["usage"]["reasoning_tokens"] == 5
            assert "unit-test-only" not in result.text
            assert set(meta) == {"trace_id", "operation_id", "provider", "model", "usage", "latency_ms"}
            assert call.call_count == 2
    finally:
        main.app.dependency_overrides.pop(dependency, None)


@pytest.mark.parametrize("trace", [None, "not-a-uuid", "unit-test-secret-marker", str(UUID(int=0)), "x" * 100])
def test_missing_invalid_trace_gets_safe_uuid(trace):
    with TestClient(main.app, headers={"Authorization": "Bearer unit-test-service-token"}) as api:
        response = api.get("/settings/provider", headers={"X-Trace-ID": trace} if trace else {})
    assert response.status_code == 200
    assert UUID(response.headers["X-Trace-ID"]).version == 4
    assert "unit-test-secret-marker" not in response.headers["X-Trace-ID"]


@pytest.mark.parametrize("task", ["analysis", "summary"])
@pytest.mark.parametrize("malformed", [False, True])
def test_route_failure_is_not_success_and_keeps_safe_observation(task, malformed):
    measured, call = adapter("openai", task, error=None if malformed else RuntimeError("unit-test-secret-marker"), malformed=malformed)
    dependency = main.get_analyzer if task == "analysis" else main.get_stock_news_summarizer
    path = "/analyze/news" if task == "analysis" else "/summarize/stock-news"
    data = {"articles": [a.model_dump(mode="json") for a in ARTICLES]} if task == "analysis" else REQUEST
    main.app.dependency_overrides[dependency] = lambda: measured
    try:
        with TestClient(main.app, headers={"Authorization": "Bearer unit-test-service-token"}) as api:
            response = api.post(path, json={**data, "include_ai_metadata": True})
        assert response.status_code == 502
        assert response.json()["ai"]["trace_id"] == response.headers["X-Trace-ID"]
        assert response.json()["ai"]["usage"]["input_tokens"] == (10 if malformed else None)
        assert "unit-test-secret-marker" not in response.text
        call.assert_called_once()
    finally:
        main.app.dependency_overrides.pop(dependency, None)


def test_no_generation_operation_on_initialization_or_validation_failure(monkeypatch):
    factory = Mock(side_effect=main.SummarizerUnavailableError("unit-test-secret-marker"))
    monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", factory)
    monkeypatch.setattr(main.app.state.provider_manager, "get_settings", lambda: ("openai", "test-model"))
    with TestClient(main.app, headers={"Authorization": "Bearer unit-test-service-token"}) as api:
        response = api.post("/summarize/stock-news", json={**REQUEST, "include_ai_metadata": True})
    assert response.status_code == 503
    assert "ai" not in response.json() and "unit-test-secret-marker" not in response.text


def test_request_validation_never_creates_generation_operation(monkeypatch):
    measured, call = adapter("openai", "summary")
    operation_factory = Mock(return_value=uuid4())
    monkeypatch.setattr(execution, "uuid4", operation_factory)
    main.app.dependency_overrides[main.get_stock_news_summarizer] = lambda: measured
    try:
        with TestClient(main.app, headers={"Authorization": "Bearer unit-test-service-token"}) as api:
            response = api.post("/summarize/stock-news", json={**REQUEST, "articles": [], "include_ai_metadata": True}, headers={"X-Trace-ID": str(uuid4())})
        assert response.status_code == 422
        call.assert_not_called()
        operation_factory.assert_not_called()
    finally:
        main.app.dependency_overrides.pop(main.get_stock_news_summarizer, None)


def test_normalization_retains_only_numeric_allowlist():
    result = execution.openai_usage(NS(usage=NS(input_tokens=0, api_key="unit-test-secret-marker", reasoning_text="not-retained")))
    assert result.input_tokens == 0
    assert "unit-test-secret-marker" not in result.model_dump_json()
    assert "not-retained" not in result.model_dump_json()


def test_shared_adapter_observations_are_not_read_from_last_usage():
    from concurrent.futures import ThreadPoolExecutor
    measured, call = adapter("openai", "analysis")
    traces = [uuid4(), uuid4()]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda trace: measured.analyze_with_metadata(ARTICLES, trace_id=trace), traces))
    assert [outcome.metadata.trace_id for outcome in outcomes] == traces
    assert outcomes[0].metadata.operation_id != outcomes[1].metadata.operation_id
    measured.last_usage.clear()
    assert all(outcome.metadata.usage.input_tokens == 10 for outcome in outcomes)
    assert call.call_count == 2
