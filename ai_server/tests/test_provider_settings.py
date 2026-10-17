import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.analyzers.fake import FakeAnalyzer
from app.main import app
from app.provider_manager import RuntimeProviderManager
from app.settings import GEMINI_MODEL, OPENAI_MODEL


@pytest.fixture
def provider_client(monkeypatch):
    manager = RuntimeProviderManager(
        "openai",
        openai_key_getter=lambda: "unit-test-openai-key",
        gemini_key_getter=lambda: "unit-test-gemini-key",
    )
    monkeypatch.setattr(app.state, "provider_manager", manager)
    with TestClient(app) as client:
        yield client


def test_get_provider_settings_reports_initial_openai(provider_client):
    response = provider_client.get("/settings/provider")
    assert response.status_code == 200
    assert response.json() == {"provider": "openai", "model": OPENAI_MODEL}


@pytest.mark.parametrize("provider", ["gemini", "Gemini", "GEMINI"])
def test_openai_switches_to_canonical_gemini(provider_client, provider):
    response = provider_client.put("/settings/provider", json={"provider": provider})
    assert response.status_code == 200
    assert response.json() == {"provider": "gemini", "model": GEMINI_MODEL}
    assert provider_client.get("/settings/provider").json()["provider"] == "gemini"


@pytest.mark.parametrize("provider", ["openai", "OpenAI", "OPENAI"])
def test_gemini_switches_to_canonical_openai(provider_client, provider):
    provider_client.put("/settings/provider", json={"provider": "gemini"})
    response = provider_client.put("/settings/provider", json={"provider": provider})
    assert response.status_code == 200
    assert response.json() == {"provider": "openai", "model": OPENAI_MODEL}
    assert provider_client.get("/settings/provider").json()["provider"] == "openai"


def test_news_analysis_uses_provider_selected_at_runtime(provider_client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        main_module,
        "OpenAINewsAnalyzer",
        lambda: calls.append("openai") or FakeAnalyzer(),
    )
    monkeypatch.setattr(
        main_module,
        "GeminiNewsAnalyzer",
        lambda: calls.append("gemini") or FakeAnalyzer(),
    )
    response = provider_client.put("/settings/provider", json={"provider": "GEMINI"})
    assert response.status_code == 200
    result = provider_client.post(
        "/analyze/news",
        json={"articles": [{"article_id": "a", "title": "Test"}]},
    )
    assert result.status_code == 200
    assert calls == ["gemini"]


@pytest.mark.parametrize("invalid_provider", ["claude", "unit-test-secret-marker"])
def test_invalid_provider_is_rejected_without_echoing_or_mutating(
    provider_client, invalid_provider
):
    response = provider_client.put(
        "/settings/provider",
        json={"provider": invalid_provider},
    )
    assert response.status_code == 422
    assert invalid_provider not in response.text
    assert provider_client.get("/settings/provider").json()["provider"] == "openai"


def test_missing_target_key_rejects_switch_and_keeps_current_provider(monkeypatch):
    manager = RuntimeProviderManager(
        "openai",
        openai_key_getter=lambda: "unit-test-openai-key",
        gemini_key_getter=lambda: None,
    )
    monkeypatch.setattr(app.state, "provider_manager", manager)
    with TestClient(app) as client:
        response = client.put("/settings/provider", json={"provider": "GEMINI"})
        assert response.status_code == 503
        assert response.json() == {"detail": "Selected provider is not configured"}
        assert client.get("/settings/provider").json()["provider"] == "openai"


def test_provider_response_never_contains_api_keys(provider_client):
    response = provider_client.put("/settings/provider", json={"provider": "GEMINI"})
    assert response.status_code == 200
    assert "unit-test-openai-key" not in response.text
    assert "unit-test-gemini-key" not in response.text
    settings = provider_client.get("/settings/provider")
    assert settings.status_code == 200
    assert "unit-test-openai-key" not in settings.text
    assert "unit-test-gemini-key" not in settings.text
    assert settings.json() == {"provider": "gemini", "model": GEMINI_MODEL}


def test_manager_requires_canonical_value_before_mutation(provider_client):
    manager = app.state.provider_manager
    with pytest.raises(ValueError):
        manager.set_provider("GEMINI")
    assert manager.get_provider() == "openai"
