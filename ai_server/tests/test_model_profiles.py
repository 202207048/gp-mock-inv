"""Request-local profiles: all provider responses are injected, never live."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import re
from threading import Barrier
from types import SimpleNamespace as NS
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.main as main
import app.model_profiles as profiles
from app.provider_manager import RuntimeProviderManager
from app.schemas import AIExecutionMetadata, NewsSentimentBatch, StockNewsBrief, UsageMetrics

TASKS = [("news_analysis", "/analyze/news"), ("stock_news_summary", "/summarize/stock-news")]
BRIEF = dict(headline="가상 기사", summary=["테스트 발췌문 요약"], tags=[], disclaimer="테스트 안내")
MODELS = {"luna": ("gpt-6-luna", "none"), "sol": ("gpt-6.1-sol", "low")}


def payload(task, profile="luna", *, metadata=True, case=0):
    body = {"articles": [{"title": f"case-{case}"}], "include_ai_metadata": metadata}
    if task == "news_analysis":
        body["articles"][0]["article_id"] = "a"
    else:
        body.update(symbol_code="005930", name="가상 종목")
    if profile is not None:
        body["profile"] = profile
    return body


@pytest.fixture
def api(monkeypatch):
    manager = RuntimeProviderManager("gemini", openai_key_getter=lambda: "unit-test-only",
                                     gemini_key_getter=lambda: "unit-test-only")
    monkeypatch.setattr(main.app.state, "provider_manager", manager)
    monkeypatch.setattr(main.app.state, "model_profiles", profiles.ModelProfileRegistry())
    monkeypatch.setattr(profiles.settings, "get_openai_api_key", lambda: "unit-test-only")
    with TestClient(main.app) as client:
        yield client, manager


def inject_sdk(monkeypatch, task, *, barrier=None, failure=False, on_call=None):
    cls = profiles.OpenAINewsAnalyzer if task == "news_analysis" else profiles.OpenAIStockNewsSummarizer
    calls = []

    def factory(**configuration):
        model, effort = configuration["model"], configuration["reasoning_effort"]
        def parse(**kwargs):
            case = int(re.search(r"case-(\d+)", kwargs["input"]).group(1))
            marker = (1000 if model == "gpt-6-luna" else 2000) + case
            calls.append((model, effort, kwargs["model"], kwargs["reasoning"], case))
            if barrier is not None:
                barrier.wait(timeout=15)
            if on_call is not None:
                on_call()
            if failure:
                raise RuntimeError("unit-test-secret-marker")
            parsed = (NewsSentimentBatch(articles=[dict(article_id="a",
                sentiment=dict(label="NEUTRAL", confidence=0.5), importance_score=0.5)])
                if task == "news_analysis" else StockNewsBrief(**BRIEF))
            return NS(output_parsed=parsed, usage=NS(input_tokens=marker, output_tokens=marker+10))
        return cls(**configuration, client=NS(responses=NS(parse=parse)))

    monkeypatch.setattr(profiles, "OpenAINewsAnalyzer" if task == "news_analysis"
                        else "OpenAIStockNewsSummarizer", factory)
    return calls


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("profile", ["luna", "sol"])
def test_profile_uses_actual_config_without_reading_or_mutating_global(api, monkeypatch, task, path, profile):
    client, manager = api
    initial = manager.get_settings()
    monkeypatch.setattr(manager, "get_provider", Mock(side_effect=AssertionError("No global lookup")))
    calls = inject_sdk(monkeypatch, task)
    response = client.post(path, json=payload(task, profile))
    assert response.status_code == 200
    ai = response.json()["ai"]
    model, effort = MODELS[profile]
    assert (ai["provider"], ai["model"], ai["profile_id"], ai["profile_revision"]) == ("openai", model, profile, 1)
    assert calls == [(model, effort, model, {"effort": effort}, 0)]
    assert manager.get_settings() == initial
    assert "unit-test-only" not in response.text


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("profile", ["", "Sol", " luna", "luna ", "x"*33, 42, {}, "unknown", "gpt-6-luna", "openai"])
def test_invalid_or_unknown_profile_has_no_adapter_creation(api, monkeypatch, task, path, profile):
    client, manager = api
    factory = Mock(side_effect=AssertionError("No adapter creation"))
    monkeypatch.setattr(main, "create_profile_adapter", factory)
    response = client.post(path, json=payload(task, profile))
    assert response.status_code == 422
    factory.assert_not_called()
    assert manager.get_provider() == "gemini"


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("field", ["model", "provider", "reasoning_effort", "base_url", "temperature"])
def test_raw_configuration_is_not_a_request_contract(api, monkeypatch, task, path, field):
    factory = Mock()
    monkeypatch.setattr(main, "create_profile_adapter", factory)
    response = api[0].post(path, json={**payload(task), field: "not-allowed"})
    assert response.status_code == 422
    factory.assert_not_called()


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("disabled", [False, True])
def test_task_and_disabled_preflight_without_adapter(api, monkeypatch, task, path, disabled):
    profile = replace(profiles.DEFAULT_PROFILES[0], enabled=not disabled,
                      supported_tasks=(task,) if disabled else ())
    monkeypatch.setattr(main.app.state, "model_profiles", profiles.ModelProfileRegistry([profile]))
    factory = Mock()
    monkeypatch.setattr(main, "create_profile_adapter", factory)
    response = api[0].post(path, json=payload(task))
    assert response.status_code == (503 if disabled else 422)
    factory.assert_not_called()
    assert api[1].get_provider() == "gemini"


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_credentials_prevents_sdk_initialization(api, monkeypatch, task, path, key):
    monkeypatch.setattr(profiles.settings, "get_openai_api_key", lambda: key)
    factory = Mock(side_effect=AssertionError("SDK must not be initialized"))
    monkeypatch.setattr(profiles, "OpenAINewsAnalyzer", factory)
    monkeypatch.setattr(profiles, "OpenAIStockNewsSummarizer", factory)
    response = api[0].post(path, json=payload(task))
    assert response.status_code == 503
    factory.assert_not_called()
    assert "ai" not in response.json()
    assert api[1].get_provider() == "gemini"

@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("profile", ["luna", "sol"])
def test_provider_failure_keeps_resolved_identity_without_fallback(api, monkeypatch, task, path, profile):
    calls = inject_sdk(monkeypatch, task, failure=True)
    alternate = Mock(side_effect=AssertionError("No fallback"))
    monkeypatch.setattr(main, "OpenAINewsAnalyzer", alternate)
    monkeypatch.setattr(main, "GeminiNewsAnalyzer", alternate)
    monkeypatch.setattr(main, "OpenAIStockNewsSummarizer", alternate)
    monkeypatch.setattr(main, "GeminiStockNewsSummarizer", alternate)
    response = api[0].post(path, json=payload(task, profile))
    assert response.status_code == 502
    ai = response.json()["ai"]
    assert ai["profile_id"] == profile and ai["model"] == MODELS[profile][0]
    assert ai["operation_id"] and all(v is None for v in ai["usage"].values())
    assert len(calls) == 1
    alternate.assert_not_called()
    assert "unit-test-secret-marker" not in response.text
    assert api[1].get_provider() == "gemini"


@pytest.mark.parametrize("task,path", TASKS)
def test_explicit_profile_without_metadata_keeps_legacy_response_shape(api, monkeypatch, task, path):
    inject_sdk(monkeypatch, task)
    response = api[0].post(path, json=payload(task, "sol", metadata=False))
    assert response.status_code == 200
    expected = {"articles"} if task == "news_analysis" else set(BRIEF) | {"provider", "model"}
    assert set(response.json()) == expected


@pytest.mark.parametrize("task,path", TASKS)
def test_resolve_once_and_use_immutable_snapshot(api, monkeypatch, task, path):
    registry = main.app.state.model_profiles
    resolve = Mock(wraps=registry.resolve)
    monkeypatch.setattr(registry, "resolve", resolve)
    updated = replace(profiles.DEFAULT_PROFILES[0], revision=2, enabled=False)
    inject_sdk(monkeypatch, task, on_call=lambda: setattr(
        main.app.state, "model_profiles", profiles.ModelProfileRegistry([updated])))
    response = api[0].post(path, json=payload(task))
    assert response.status_code == 200
    assert response.json()["ai"]["profile_revision"] == 1
    resolve.assert_called_once_with("luna", task)


@pytest.mark.parametrize("task,path", TASKS)
def test_deterministic_concurrent_profiles_isolate_config_operation_and_usage(api, monkeypatch, task, path):
    client, manager = api
    initial = manager.get_settings()
    # Explicit profile requests must not even consult process-global selection.
    with monkeypatch.context() as guarded:
        for method in ["get_settings", "get_provider", "set_provider"]:
            guarded.setattr(manager, method, Mock(side_effect=AssertionError("No global state access")))
        calls = inject_sdk(guarded, task, barrier=Barrier(4))
        traces = [str(uuid4()) for _ in range(12)]
        def run(case):
            profile = "luna" if case % 2 == 0 else "sol"
            return client.post(path, json=payload(task, profile, case=case),
                               headers={"X-Trace-ID": traces[case]})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(12)))
        operations = set()
        for case, response in enumerate(results):
            assert response.status_code == 200
            ai = response.json()["ai"]
            profile = "luna" if case % 2 == 0 else "sol"
            model, effort = MODELS[profile]
            assert (ai["profile_id"], ai["model"], ai["trace_id"]) == (profile, model, traces[case])
            marker = (1000 if profile == "luna" else 2000) + case
            assert ai["usage"]["input_tokens"] == marker
            assert ai["usage"]["output_tokens"] == marker + 10
            operations.add(ai["operation_id"])
            assert (model, effort, model, {"effort": effort}, case) in calls
        assert len(operations) == len(calls) == 12
    assert manager.get_settings() == initial


def test_catalog_safe_projection_and_missing_key_readiness(api, monkeypatch):
    forbidden = {"provider", "model", "generation", "api_key", "base_url", "reasoning_effort"}
    for configured in [True, False]:
        monkeypatch.setattr(profiles.settings, "get_openai_api_key", lambda: "unit-test-only" if configured else None)
        response = api[0].get("/profiles")
        assert response.status_code == 200
        assert [p["id"] for p in response.json()] == ["luna", "sol"]
        for profile in response.json():
            assert not forbidden.intersection(profile)
            assert profile["enabled"] is configured
            assert profile["supported_tasks"] == ["news_analysis", "stock_news_summary", "portfolio_brief"]
        assert "unit-test-only" not in response.text
        assert "OPENAI_API_KEY" not in response.text


def test_profile_and_generation_snapshot_are_frozen():
    registry = profiles.ModelProfileRegistry()
    context = registry.resolve("luna", "news_analysis")
    for value, field, replacement in [(context, "task", "stock_news_summary"),
                                      (context.profile, "model", "different"),
                                      (context.profile.generation, "reasoning_effort", "low")]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, field, replacement)
    with pytest.raises(TypeError):
        registry._profiles["unknown"] = context.profile


def test_metadata_comes_from_actual_context_and_rejects_mismatch():
    context = profiles.ModelProfileRegistry().resolve("luna", "news_analysis")
    metadata = AIExecutionMetadata(trace_id=uuid4(), operation_id=uuid4(), provider="openai",
                                   model="wrong", usage=UsageMetrics(), latency_ms=1)
    with pytest.raises(ValueError):
        profiles.profile_metadata(metadata, context)
    for fields in [dict(profile_id="luna"), dict(profile_revision=1),
                   dict(profile_id="luna", profile_revision=True)]:
        with pytest.raises(ValidationError):
            AIExecutionMetadata(**{**metadata.model_dump(), **fields})


@pytest.mark.parametrize("profile", [None, "luna", "sol"])
def test_profile_is_execution_control_not_summary_prompt_input(profile):
    from app.schemas import StockNewsSummaryRequest
    from app.summarizers.base import build_stock_news_prompt
    legacy = StockNewsSummaryRequest(**payload("stock_news_summary", None))
    selected = StockNewsSummaryRequest(**payload("stock_news_summary", profile))
    assert build_stock_news_prompt(legacy) == build_stock_news_prompt(selected)
    assert '"profile"' not in build_stock_news_prompt(selected)


@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("failure", [False, True])
def test_legacy_metadata_does_not_gain_null_profile_fields(api, monkeypatch, task, path, failure):
    inject_sdk(monkeypatch, task, failure=failure)
    context = profiles.ModelProfileRegistry().resolve("luna", task)
    adapter = profiles.create_profile_adapter(context)
    dependency = main.get_analyzer if task == "news_analysis" else main.get_stock_news_summarizer
    main.app.dependency_overrides[dependency] = lambda: adapter
    try:
        response = api[0].post(path, json=payload(task, None))
        assert response.status_code == (502 if failure else 200)
        assert "profile_id" not in response.json()["ai"]
        assert "profile_revision" not in response.json()["ai"]
    finally:
        main.app.dependency_overrides.pop(dependency, None)

@pytest.mark.parametrize("task,path", TASKS)
@pytest.mark.parametrize("failing_profile", ["luna", "sol"])
def test_concurrent_success_and_failure_keep_request_owned_metadata(api, monkeypatch, task, path, failing_profile):
    from collections import Counter
    from threading import Lock, local
    import app.execution as execution

    client, manager = api
    initial = manager.get_settings()
    real_adapter = (profiles.OpenAINewsAnalyzer if task == "news_analysis"
                    else profiles.OpenAIStockNewsSummarizer)
    operation_context = local()
    lock = Lock()
    observed, pending = {}, {}
    created, waves = [], []

    def capture_operation():
        operation = uuid4()
        operation_context.operation_id = str(operation)
        return operation

    def capture_overlap():
        with lock:
            waves.append(tuple(pending.values()))
            pending.clear()

    barrier = Barrier(4, action=capture_overlap)

    def factory(**configuration):
        with lock:
            created.append((configuration["model"], configuration["reasoning_effort"]))

        def parse(**kwargs):
            case = int(re.search(r"case-(\d+)", kwargs["input"]).group(1))
            profile = "luna" if case % 2 == 0 else "sol"
            marker = (1000 if profile == "luna" else 2000) + case
            with lock:
                assert case not in observed  # No retry or second profile execution.
                observed[case] = (operation_context.operation_id,
                                  kwargs["model"], kwargs["reasoning"])
                pending[case] = profile
            barrier.wait(timeout=15)
            if profile == failing_profile:
                raise RuntimeError("unit-test-secret-marker")
            parsed = (NewsSentimentBatch(articles=[dict(article_id=f"case-{case}",
                sentiment=dict(label="NEUTRAL", confidence=0.5), importance_score=0.5)])
                if task == "news_analysis" else StockNewsBrief(**{**BRIEF, "summary": [f"case-{case}"]}))
            return NS(output_parsed=parsed, usage=NS(input_tokens=marker, output_tokens=marker+10))

        return real_adapter(**configuration, client=NS(responses=NS(parse=parse)))

    with monkeypatch.context() as guarded:
        guarded.setattr(execution, "uuid4", capture_operation)
        guarded.setattr(profiles, "OpenAINewsAnalyzer" if task == "news_analysis"
                        else "OpenAIStockNewsSummarizer", factory)
        alternate = Mock(side_effect=AssertionError("No legacy fallback"))
        for name in ["OpenAINewsAnalyzer", "GeminiNewsAnalyzer",
                     "OpenAIStockNewsSummarizer", "GeminiStockNewsSummarizer"]:
            guarded.setattr(main, name, alternate)
        for method in ["get_settings", "get_provider", "set_provider"]:
            guarded.setattr(manager, method, Mock(side_effect=AssertionError("No global state access")))

        traces = [str(uuid4()) for _ in range(12)]
        def run(case):
            profile = "luna" if case % 2 == 0 else "sol"
            body = payload(task, profile, case=case)
            if task == "news_analysis":
                body["articles"][0]["article_id"] = f"case-{case}"
            return client.post(path, json=body, headers={"X-Trace-ID": traces[case]})

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(run, range(12)))

        assert len(waves) == 3 and all(len(wave) == 4 for wave in waves)
        assert any(set(wave) == {"luna", "sol"} for wave in waves)
        assert set(observed) == set(range(12))
        assert Counter(created) == Counter({MODELS["luna"]: 6, MODELS["sol"]: 6})
        operations = set()
        for case, response in enumerate(results):
            profile = "luna" if case % 2 == 0 else "sol"
            model, effort = MODELS[profile]
            failed = profile == failing_profile
            assert response.status_code == (502 if failed else 200)
            ai = response.json()["ai"]
            assert (ai["profile_id"], ai["profile_revision"], ai["provider"], ai["model"]) == (profile, 1, "openai", model)
            assert ai["trace_id"] == response.headers["X-Trace-ID"] == traces[case]
            assert observed[case] == (ai["operation_id"], model, {"effort": effort})
            operations.add(ai["operation_id"])
            expected_usage = dict.fromkeys(["input_tokens", "output_tokens", "reasoning_tokens", "total_tokens", "cached_tokens"])
            if not failed:
                marker = (1000 if profile == "luna" else 2000) + case
                expected_usage.update(input_tokens=marker, output_tokens=marker+10)
                if task == "news_analysis":
                    assert response.json()["articles"][0]["article_id"] == f"case-{case}"
                else:
                    assert response.json()["summary"] == [f"case-{case}"]
            assert ai["usage"] == expected_usage
            assert ai["latency_ms"] >= 0
            assert "unit-test-secret-marker" not in response.text
        assert len(operations) == 12
        alternate.assert_not_called()

    assert main.app.state.provider_manager is manager
    assert manager.get_settings() == initial
