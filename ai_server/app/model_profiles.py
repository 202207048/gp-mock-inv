"""Server-owned model allowlist and immutable per-request execution settings."""
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Iterable

from . import settings
from .analyzers.openai import OpenAINewsAnalyzer
from .schemas import AIExecutionMetadata, ModelProfileCatalogEntry, ProfileTask
from .summarizers.openai import OpenAIStockNewsSummarizer


class ProfileValidationError(ValueError):
    """Unknown/malformed profile or unsupported task, before generation."""


class ProfileUnavailableError(ValueError):
    """Known profile is disabled or missing server credentials."""


@dataclass(frozen=True)
class GenerationConfig:
    reasoning_effort: str


@dataclass(frozen=True)
class ModelProfile:
    id: str
    revision: int
    display_name: str
    provider: str
    model: str
    enabled: bool
    supported_tasks: tuple[ProfileTask, ...]
    generation: GenerationConfig
    description: str = ""
    category: str = "primary"


@dataclass(frozen=True)
class ResolvedProfile:
    profile: ModelProfile
    task: ProfileTask


DEFAULT_PROFILES = (
    ModelProfile(
        id="luna", revision=1, display_name="GPT-6 Luna", provider="openai",
        model="gpt-6-luna", enabled=True,
        supported_tasks=("news_analysis", "stock_news_summary"),
        generation=GenerationConfig(reasoning_effort="none"),
        description="기본 추천 뉴스 분석·요약 모델",
    ),
    ModelProfile(
        id="sol", revision=1, display_name="GPT-6.1 Sol", provider="openai",
        model="gpt-6.1-sol", enabled=True,
        supported_tasks=("news_analysis", "stock_news_summary"),
        generation=GenerationConfig(reasoning_effort="low"),
        description="고품질 비교 후보; 일반적인 품질 우위를 보장하지 않음",
        category="experimental",
    ),
)


class ModelProfileRegistry:
    def __init__(self, profiles: Iterable[ModelProfile] = DEFAULT_PROFILES) -> None:
        profiles = tuple(profiles)
        by_id = {profile.id: profile for profile in profiles}
        if len(by_id) != len(profiles):
            raise ValueError("Duplicate model profile ID")
        self._profiles = MappingProxyType(by_id)

    def resolve(self, profile_id: str, task: ProfileTask) -> ResolvedProfile:
        if not isinstance(profile_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", profile_id):
            raise ProfileValidationError("Invalid model profile")
        profile = self._profiles.get(profile_id)
        if profile is None:
            raise ProfileValidationError("Unknown model profile")
        if task not in profile.supported_tasks:
            raise ProfileValidationError("Model profile does not support this task")
        if not profile.enabled:
            raise ProfileUnavailableError("Model profile unavailable")
        return ResolvedProfile(profile=profile, task=task)

    def catalog(self) -> list[ModelProfileCatalogEntry]:
        configured = bool((settings.get_openai_api_key() or "").strip())
        return [ModelProfileCatalogEntry(
            id=p.id, revision=p.revision, display_name=p.display_name,
            description=p.description, category=p.category,
            enabled=p.enabled and configured, supported_tasks=p.supported_tasks,
        ) for p in self._profiles.values()]


def create_profile_adapter(context: ResolvedProfile):
    profile = context.profile
    key = settings.get_openai_api_key()
    if key is None or not key.strip():
        raise ProfileUnavailableError("Model profile is not configured")
    if profile.provider != "openai":
        raise ProfileUnavailableError("Model profile unavailable")
    factory = {
        "news_analysis": OpenAINewsAnalyzer,
        "stock_news_summary": OpenAIStockNewsSummarizer,
    }[context.task]
    return factory(api_key=key, model=profile.model,
                   reasoning_effort=profile.generation.reasoning_effort)


def profile_metadata(metadata: AIExecutionMetadata,
                     context: ResolvedProfile | None) -> AIExecutionMetadata:
    if context is None:
        return metadata
    profile = context.profile
    if (metadata.provider, metadata.model) != (profile.provider, profile.model):
        raise ValueError("Execution does not match resolved model profile")
    return metadata.model_copy(update={
        "profile_id": profile.id, "profile_revision": profile.revision,
    })
