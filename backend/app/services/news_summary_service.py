"""Internal AI Server transport for stock-news summaries; no provider SDK calls."""
from typing import Annotated, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


NEWS_DISCLAIMER = "모의투자 연습용 설명이며 투자 권유가 아닙니다."
Text = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
Tag = Annotated[str, Field(strict=True, min_length=1, max_length=100)]


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["openai", "gemini"]
    model: str = Field(strict=True, min_length=1, max_length=200)


TokenCount = Annotated[int, Field(strict=True, ge=0)]


class UsageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_tokens: TokenCount | None = None


class AIExecutionMetadata(ProviderSettings):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    trace_id: UUID
    operation_id: UUID
    usage: UsageMetrics
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


class _ExecutionIdentity(ProviderSettings):
    """Validate execution integrity even when numeric diagnostics are unusable."""
    trace_id: UUID
    operation_id: UUID


class StockNewsSummary(ProviderSettings):
    ai: AIExecutionMetadata | None = None
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    summary: list[Text] = Field(min_length=1, max_length=3)
    tags: list[Tag] = Field(max_length=3)
    disclaimer: str = Field(strict=True, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def metadata_matches_result(self):
        if self.ai is not None and (self.ai.provider, self.ai.model) != (self.provider, self.model):
            raise ValueError("Execution metadata does not match result")
        return self

    def brief(self) -> dict:
        return self.model_dump(exclude={"provider", "model", "ai"})


class NewsSummaryUnavailable(Exception):
    """Safe failure independent of provider authentication or SDK error details."""


async def _request(base_url: str, path: str, payload: dict | None = None,
                   *, trace_id: UUID | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0 if payload is None else 30.0) as client:
            url = base_url.rstrip("/") + path
            headers = {"X-Trace-ID": str(trace_id)} if trace_id is not None else {}
            options = {"headers": headers} if headers else {}
            if payload is None:
                response = await client.get(url, **options)
            else:
                response = await client.post(url, json=payload, **options)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # Never forward upstream 401 or raw upstream responses to the browser.
        raise NewsSummaryUnavailable("Stock-news summary unavailable") from exc


async def get_summary_provider(base_url: str, *, trace_id: UUID | None = None) -> ProviderSettings:
    data = await _request(base_url, "/settings/provider", trace_id=trace_id)
    try:
        return ProviderSettings.model_validate(data)
    except ValidationError as exc:
        raise NewsSummaryUnavailable("Invalid AI Server provider response") from exc


async def summarize_stock_news(base_url: str, payload: dict,
                               *, trace_id: UUID | None = None) -> StockNewsSummary:
    data = await _request(base_url, "/summarize/stock-news", payload, trace_id=trace_id)
    try:
        # Keep the core result strict, independently of optional observations.
        core = dict(data) if isinstance(data, dict) else data
        metadata = core.pop("ai", None) if isinstance(core, dict) else None
        result = StockNewsSummary.model_validate(core)
        if metadata is None:
            return result
        if not isinstance(metadata, dict) or set(metadata) - AIExecutionMetadata.model_fields.keys():
            raise ValueError("Invalid execution metadata structure")
        identity = _ExecutionIdentity.model_validate({
            name: metadata[name] for name in _ExecutionIdentity.model_fields if name in metadata
        })
        if (identity.provider, identity.model) != (result.provider, result.model):
            raise NewsSummaryUnavailable("AI Server execution identity mismatch")
        if trace_id is not None and identity.trace_id != trace_id:
            raise NewsSummaryUnavailable("AI Server trace mismatch")
    except ValueError as exc:
        raise NewsSummaryUnavailable("Invalid AI Server summary response") from exc

    try:
        observation = AIExecutionMetadata.model_validate(metadata)
    except ValidationError:
        # No fabricated zero/partial usage: discard the entire observation only.
        return result
    return result.model_copy(update={"ai": observation})
