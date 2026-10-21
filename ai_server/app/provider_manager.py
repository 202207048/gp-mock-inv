from threading import RLock
from typing import Callable

from .settings import (
    GEMINI_MODEL,
    OPENAI_MODEL,
    get_gemini_api_key,
    get_openai_api_key,
)


class ProviderKeyUnavailableError(ValueError):
    """The selected provider has no configured API key."""


class RuntimeProviderManager:
    """Keeps the selected provider in process memory for this server run."""

    _PROVIDERS = {"openai", "gemini"}

    def __init__(
        self,
        initial_provider: str,
        *,
        openai_key_getter: Callable[[], str | None] = get_openai_api_key,
        gemini_key_getter: Callable[[], str | None] = get_gemini_api_key,
    ) -> None:
        self._provider = self.normalize_provider(initial_provider)
        self._key_getters = {
            "openai": openai_key_getter,
            "gemini": gemini_key_getter,
        }
        self._lock = RLock()

    @classmethod
    def normalize_provider(cls, provider: str) -> str:
        if not isinstance(provider, str):
            raise ValueError("Provider must be either 'openai' or 'gemini'")
        normalized = provider.strip().lower()
        if normalized not in cls._PROVIDERS:
            raise ValueError("Provider must be either 'openai' or 'gemini'")
        return normalized

    def get_provider(self) -> str:
        with self._lock:
            return self._provider

    def get_model(self) -> str:
        with self._lock:
            return OPENAI_MODEL if self._provider == "openai" else GEMINI_MODEL

    def get_settings(self) -> tuple[str, str]:
        with self._lock:
            model = OPENAI_MODEL if self._provider == "openai" else GEMINI_MODEL
            return self._provider, model

    def set_provider(self, provider: str) -> str:
        if provider not in self._PROVIDERS:
            raise ValueError("Provider must be a validated canonical value")
        key = self._key_getters[provider]()
        if key is None or not key.strip():
            raise ProviderKeyUnavailableError("Selected provider is not configured")
        model = OPENAI_MODEL if provider == "openai" else GEMINI_MODEL
        with self._lock:
            self._provider = provider
        return model
