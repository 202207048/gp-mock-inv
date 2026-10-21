from typing import Any

from ..schemas import StockNewsBrief, StockNewsSummaryRequest
from ..settings import GEMINI_MODEL, get_gemini_api_key
from .base import SummarizerError, SummarizerUnavailableError, build_stock_news_prompt


class GeminiStockNewsSummarizer:
    provider = "gemini"

    def __init__(self, *, api_key: str | None = None, model: str = GEMINI_MODEL,
                 client: Any | None = None) -> None:
        key = api_key if api_key is not None else get_gemini_api_key()
        if key is None or not key.strip():
            raise SummarizerUnavailableError("Gemini summarizer is not configured")
        self.model = model
        if client is None:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=25000, retry_options=types.HttpRetryOptions(attempts=1),
            ))
        self._client = client

    def summarize(self, request: StockNewsSummaryRequest) -> StockNewsBrief:
        from google.genai import types
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=build_stock_news_prompt(request),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=StockNewsBrief.model_json_schema(),
                    temperature=0,
                ),
            )
            raw = getattr(response, "text", None)
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("Missing structured summary")
            return StockNewsBrief.model_validate_json(raw)
        except Exception as exc:
            raise SummarizerError("Gemini stock-news summary failed") from exc
