from typing import Any

from ..schemas import StockNewsBrief, StockNewsSummaryRequest
from ..settings import OPENAI_MODEL, OPENAI_REASONING_EFFORT, get_openai_api_key
from .base import (
    SummarizerError, SummarizerUnavailableError,
    build_stock_news_prompt,
)


class OpenAIStockNewsSummarizer:
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, model: str = OPENAI_MODEL,
                 reasoning_effort: str = OPENAI_REASONING_EFFORT,
                 client: Any | None = None) -> None:
        key = api_key if api_key is not None else get_openai_api_key()
        if key is None or not key.strip():
            raise SummarizerUnavailableError("OpenAI summarizer is not configured")
        self.model = model
        self._reasoning_effort = reasoning_effort
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=key, max_retries=0, timeout=25.0)
        self._client = client

    def summarize(self, request: StockNewsSummaryRequest) -> StockNewsBrief:
        try:
            response = self._client.responses.parse(
                model=self.model, input=build_stock_news_prompt(request),
                reasoning={"effort": self._reasoning_effort},
                text_format=StockNewsBrief,
            )
            brief = getattr(response, "output_parsed", None)
            if not isinstance(brief, StockNewsBrief):
                raise ValueError("Missing structured summary")
            return StockNewsBrief.model_validate(brief.model_dump())
        except Exception as exc:
            raise SummarizerError("OpenAI stock-news summary failed") from exc
