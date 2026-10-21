"""Real middleware with dummy service credentials; no provider calls."""
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app import main
from app.analyzers.fake import FakeAnalyzer
from app.provider_manager import RuntimeProviderManager

TOKEN = "unit-test-service-token"
HEADERS = {"Authorization": "Bearer " + TOKEN}
PROTECTED = [
    ("POST", "/analyze/news"), ("POST", "/summarize/stock-news"),
    ("POST", "/brief/portfolio"), ("GET", "/profiles"),
    ("GET", "/settings/provider"), ("PUT", "/settings/provider"),
    ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
    ("GET", "/docs/oauth2-redirect"),
]


def test_public_health_without_configuration_or_credentials(monkeypatch):
    monkeypatch.delenv("AI_SERVICE_TOKEN")
    with TestClient(main.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("method,path", PROTECTED)
@pytest.mark.parametrize("configured", [None, "", "   "])
def test_unconfigured_service_fails_closed(monkeypatch, method, path, configured):
    if configured is None:
        monkeypatch.delenv("AI_SERVICE_TOKEN")
    else:
        monkeypatch.setenv("AI_SERVICE_TOKEN", configured)
    with TestClient(main.app) as client:
        response = client.request(method, path, headers=HEADERS, json={})
    assert response.status_code == 503
    assert response.json() == {"detail": "AI service unavailable"}
    assert TOKEN not in response.text


@pytest.mark.parametrize("method,path", PROTECTED)
def test_every_protected_route_rejects_missing_header(method, path):
    with TestClient(main.app) as client:
        response = client.request(method, path, json={})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert TOKEN not in response.text


@pytest.mark.parametrize("authorization", [
    "", "Basic unit-test", "Bearer", "Bearer ", "Bearer wrong-test-token",
    "Bearer  " + TOKEN, "Bearer " + TOKEN + " extra", "Bearer " + TOKEN + " ",
])
def test_malformed_or_wrong_credentials_do_not_mutate_provider(monkeypatch, authorization):
    manager = RuntimeProviderManager("openai", gemini_key_getter=lambda: "unit-test-only")
    monkeypatch.setattr(main.app.state, "provider_manager", manager)
    factory = Mock(side_effect=AssertionError("Unauthenticated adapter creation"))
    monkeypatch.setattr(main, "create_profile_adapter", factory)
    with TestClient(main.app) as client:
        response = client.put("/settings/provider", json={"provider": "gemini"},
                              headers={"Authorization": authorization})
        generation = client.post("/analyze/news", json={"profile": "luna"},
                                 headers={"Authorization": authorization})
    assert response.status_code == generation.status_code == 401
    assert manager.get_provider() == "openai"
    factory.assert_not_called()
    assert TOKEN not in response.text
    assert "wrong-test-token" not in response.text


def test_duplicate_authorization_is_rejected():
    with TestClient(main.app) as client:
        response = client.get("/profiles", headers=[("Authorization", HEADERS["Authorization"]),
                                                    ("Authorization", HEADERS["Authorization"])])
    assert response.status_code == 401


def test_correct_token_inference_catalog_settings_and_docs(monkeypatch):
    manager = RuntimeProviderManager("openai", gemini_key_getter=lambda: "unit-test-provider-key")
    monkeypatch.setattr(main.app.state, "provider_manager", manager)
    main.app.dependency_overrides[main.get_analyzer] = lambda: FakeAnalyzer()
    try:
        with TestClient(main.app, headers=HEADERS) as client:
            responses = [client.get("/profiles"), client.get("/settings/provider"),
                         client.put("/settings/provider", json={"provider": "GEMINI"}),
                         client.post("/analyze/news", json={"articles": [{"article_id": "one", "title": "News"}]}),
                         client.get("/openapi.json"), client.get("/docs"), client.get("/redoc")]
        assert all(r.status_code == 200 for r in responses)
        assert responses[2].json() == {"provider": "gemini", "model": "gemini-3.8-flash"}
        for response in responses:
            assert TOKEN not in response.text
            assert "unit-test-provider-key" not in response.text
    finally:
        main.app.dependency_overrides.clear()


def test_health_prefix_and_other_methods_are_not_public():
    with TestClient(main.app) as client:
        assert client.get("/health/extra").status_code == 401
        assert client.post("/health").status_code == 401
