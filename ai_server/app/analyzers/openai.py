from typing import Any
from uuid import UUID

from ..execution import ProviderExecutionResult, execute_provider_call, openai_usage

from ..schemas import Article, ArticleAnalysis, Importance, NewsSentimentBatch
from ..settings import (
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
    get_openai_api_key,
)
from .base import (
    AnalyzerError,
    AnalyzerResponseError,
    AnalyzerUnavailableError,
    build_news_analysis_prompt,
)


class OpenAINewsAnalyzer:
    """Analyze a news batch with one Responses API structured output call."""

    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = OPENAI_MODEL,
        reasoning_effort: str = OPENAI_REASONING_EFFORT,
        client: Any | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else get_openai_api_key()
        if not self._api_key:
            raise AnalyzerUnavailableError("OPENAI_API_KEY is not configured")
        self._model = model
        self.model = model
        self._reasoning_effort = reasoning_effort
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=self._api_key, max_retries=0)
        self._client = client
        self.last_http_status: int | None = None
        self.last_usage: dict[str, int | None] = {}

    def analyze(self, articles: list[Article]) -> list[ArticleAnalysis]:
        return self.analyze_with_metadata(articles).result

    def analyze_with_metadata(
        self, articles: list[Article], *, trace_id: UUID | None = None,
    ) -> ProviderExecutionResult[list[ArticleAnalysis]]:
        prompt = build_news_analysis_prompt(articles, minimal_input=True)
        call = lambda: self._client.responses.parse(
            model=self._model, input=prompt,
            reasoning={"effort": self._reasoning_effort}, text_format=NewsSentimentBatch,
        )
        return execute_provider_call(
            trace_id=trace_id, provider=self.provider, model=self._model,
            call=call, parse=lambda response: self._parse_response(response, articles),
            usage=openai_usage, error_type=AnalyzerError,
            error_message="OpenAI news analysis request failed",
        )

    def _parse_response(self, response: Any, articles: list[Article]) -> list[ArticleAnalysis]:
        self.last_http_status = 200
        self.last_usage = _usage_counts(getattr(response, "usage", None))
        batch = getattr(response, "output_parsed", None)
        if not isinstance(batch, NewsSentimentBatch):
            raise AnalyzerResponseError("OpenAI returned no valid structured response")

        requested_ids = [article.article_id for article in articles]
        returned_ids = [result.article_id for result in batch.articles]
        if len(returned_ids) != len(set(returned_ids)):
            raise AnalyzerResponseError("OpenAI returned duplicate article_id values")
        if set(returned_ids) != set(requested_ids) or len(returned_ids) != len(requested_ids):
            raise AnalyzerResponseError("OpenAI response article_id values do not match request")

        by_id = {result.article_id: result for result in batch.articles}
        return [
            ArticleAnalysis(
                article_id=article_id,
                sentiment=by_id[article_id].sentiment,
                importance=Importance(score=by_id[article_id].importance_score),
            )
            for article_id in requested_ids
        ]


def _usage_counts(usage: Any | None) -> dict[str, int | None]:
    details = getattr(usage, "output_tokens_details", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "reasoning_tokens": getattr(details, "reasoning_tokens", None),
    }
