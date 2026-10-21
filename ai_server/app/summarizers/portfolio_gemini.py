from typing import Any
from uuid import UUID

from ..execution import ProviderExecutionResult, execute_provider_call, gemini_usage
from ..schemas import PortfolioBrief, PortfolioBriefRequest
from ..settings import GEMINI_MODEL, get_gemini_api_key
from .base import SummarizerError, SummarizerUnavailableError
from .portfolio import build_portfolio_brief_prompt, validate_portfolio_brief


class GeminiPortfolioBriefGenerator:
    provider = "gemini"

    def __init__(self, *, api_key: str | None = None, model: str = GEMINI_MODEL,
                 client: Any | None = None) -> None:
        key = api_key if api_key is not None else get_gemini_api_key()
        if key is None or not key.strip():
            raise SummarizerUnavailableError("Gemini portfolio generator is not configured")
        self.model = model
        if client is None:
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=key, http_options=types.HttpOptions(
                timeout=25000, retry_options=types.HttpRetryOptions(attempts=1),
            ))
        self._client = client

    def generate(self, request: PortfolioBriefRequest) -> PortfolioBrief:
        return self.generate_with_metadata(request).result

    def generate_with_metadata(
        self, request: PortfolioBriefRequest, *, trace_id: UUID | None = None,
    ) -> ProviderExecutionResult[PortfolioBrief]:
        from google.genai import types
        prompt = build_portfolio_brief_prompt(request)
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=PortfolioBrief.model_json_schema(), temperature=0,
        )
        return execute_provider_call(
            trace_id=trace_id, provider=self.provider, model=self.model,
            call=lambda: self._client.models.generate_content(
                model=self.model, contents=prompt, config=config,
            ),
            parse=lambda response: validate_portfolio_brief(self._parse_response(response), request),
            usage=gemini_usage, error_type=SummarizerError,
            error_message="Gemini portfolio brief failed",
        )

    @staticmethod
    def _parse_response(response: Any) -> PortfolioBrief:
        raw = getattr(response, "text", None)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Missing structured portfolio brief")
        return PortfolioBrief.model_validate_json(raw)
