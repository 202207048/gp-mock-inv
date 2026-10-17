from app.provider_manager import ProviderKeyUnavailableError, RuntimeProviderManager


def test_provider_manager_initializes_from_given_environment_value():
    manager = RuntimeProviderManager("openai")
    assert manager.get_provider() == "openai"
    assert manager.get_model()


def test_provider_manager_changes_only_after_key_is_available():
    manager = RuntimeProviderManager(
        "openai",
        openai_key_getter=lambda: "test-openai-key",
        gemini_key_getter=lambda: "test-gemini-key",
    )
    assert manager.set_provider("gemini")
    assert manager.get_provider() == "gemini"
    assert manager.set_provider("openai")
    assert manager.get_provider() == "openai"


def test_provider_manager_rejects_missing_key_without_changing_state():
    manager = RuntimeProviderManager(
        "openai",
        openai_key_getter=lambda: "test-openai-key",
        gemini_key_getter=lambda: None,
    )
    try:
        manager.set_provider("gemini")
    except ProviderKeyUnavailableError:
        pass
    else:
        raise AssertionError("expected missing provider key to be rejected")
    assert manager.get_provider() == "openai"


def test_provider_manager_rejects_invalid_provider():
    manager = RuntimeProviderManager("openai")
    try:
        manager.set_provider("other")
    except ValueError:
        pass
    else:
        raise AssertionError("expected invalid provider to be rejected")
    assert manager.get_provider() == "openai"