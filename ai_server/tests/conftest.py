import pytest


@pytest.fixture(autouse=True)
def dummy_service_token(monkeypatch):
    monkeypatch.setenv("AI_SERVICE_TOKEN", "unit-test-service-token")
