from typing import Any
from uuid import UUID

from ..execution import ProviderExecutionResult, execute_provider_call, gemini_usage
from ..pattern_schemas import PatternBrief, PatternBriefRequest
from ..settings import GEMINI_MODEL, get_gemini_api_key
from .base import SummarizerError, SummarizerUnavailableError
from .pattern import build_pattern_brief_prompt, validate_pattern_brief


class GeminiPatternBriefGenerator:
    provider = "gemini"

    def __init__(self, *, api_key: str | None = None, model: str = GEMINI_MODEL,
                 client: Any | None = None) -> None:
        key = api_key if api_key is not None else get_gemini_api_key()
        if key is None or not key.strip():
            raise SummarizerUnavailableError("Gemini pattern generator is not configured")
        self.model = model
        if client is None:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=25000, retry_options=types.HttpRetryOptions(attempts=1),
            ))
        self._client = client

    def generate(self, request: PatternBriefRequest) -> PatternBrief:
        return self.generate_with_metadata(request).result

    def generate_with_metadata(
        self, request: PatternBriefRequest, *, trace_id: UUID | None = None,
    ) -> ProviderExecutionResult[PatternBrief]:
        from google.genai import types
        prompt = build_pattern_brief_prompt(request)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=PatternBrief.model_json_schema(), temperature=0,
        )
        return execute_provider_call(
            trace_id=trace_id, provider=self.provider, model=self.model,
            call=lambda: self._client.models.generate_content(
                model=self.model, contents=prompt, config=config,
            ),
            parse=lambda response: validate_pattern_brief(self._parse_response(response), request),
            usage=gemini_usage, error_type=SummarizerError,
            error_message="Gemini pattern brief failed",
        )

    @staticmethod
    def _parse_response(response: Any) -> PatternBrief:
        raw = getattr(response, "text", None)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Missing structured pattern brief")
        return PatternBrief.model_validate_json(raw)
