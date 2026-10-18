"""Internal AI Server transport for stock-news summaries; no provider SDK calls."""
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError


NEWS_DISCLAIMER = "모의투자 연습용 설명이며 투자 권유가 아닙니다."
Text = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
Tag = Annotated[str, Field(strict=True, min_length=1, max_length=100)]


class ProviderSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    provider: Literal["openai", "gemini"]
    model: str = Field(strict=True, min_length=1, max_length=200)


class StockNewsSummary(ProviderSettings):
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    summary: list[Text] = Field(min_length=1, max_length=3)
    tags: list[Tag] = Field(max_length=3)
    disclaimer: str = Field(strict=True, min_length=1, max_length=1000)

    def brief(self) -> dict:
        return self.model_dump(exclude={"provider", "model"})


class NewsSummaryUnavailable(Exception):
    """Safe failure independent of provider authentication or SDK error details."""


async def _request(base_url: str, path: str, payload: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=5.0 if payload is None else 30.0) as client:
            url = base_url.rstrip("/") + path
            if payload is None:
                response = await client.get(url)
            else:
                response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # Never forward upstream 401 or raw upstream responses to the browser.
        raise NewsSummaryUnavailable("Stock-news summary unavailable") from exc


async def get_summary_provider(base_url: str) -> ProviderSettings:
    data = await _request(base_url, "/settings/provider")
    try:
        return ProviderSettings.model_validate(data)
    except ValidationError as exc:
        raise NewsSummaryUnavailable("Invalid AI Server provider response") from exc


async def summarize_stock_news(base_url: str, payload: dict) -> StockNewsSummary:
    data = await _request(base_url, "/summarize/stock-news", payload)
    try:
        return StockNewsSummary.model_validate(data)
    except ValidationError as exc:
        raise NewsSummaryUnavailable("Invalid AI Server summary response") from exc
