from typing import Any
from uuid import UUID

from ..execution import ProviderExecutionResult, execute_provider_call, openai_usage

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
        return self.summarize_with_metadata(request).result

    def summarize_with_metadata(
        self, request: StockNewsSummaryRequest, *, trace_id: UUID | None = None,
    ) -> ProviderExecutionResult[StockNewsBrief]:
        prompt = build_stock_news_prompt(request)
        call = lambda: self._client.responses.parse(
            model=self.model, input=prompt,
            reasoning={"effort": self._reasoning_effort}, text_format=StockNewsBrief,
        )
        return execute_provider_call(
            trace_id=trace_id, provider=self.provider, model=self.model,
            call=call, parse=self._parse_response, usage=openai_usage,
            error_type=SummarizerError, error_message="OpenAI stock-news summary failed",
        )

    @staticmethod
    def _parse_response(response: Any) -> StockNewsBrief:
        brief = getattr(response, "output_parsed", None)
        if not isinstance(brief, StockNewsBrief):
            raise ValueError("Missing structured summary")
        return StockNewsBrief.model_validate(brief.model_dump())
