from typing import Any
from uuid import UUID

from ..execution import ProviderExecutionResult, execute_provider_call, openai_usage
from ..schemas import PortfolioBrief, PortfolioBriefRequest
from ..settings import OPENAI_MODEL, OPENAI_REASONING_EFFORT, get_openai_api_key
from .base import SummarizerError, SummarizerUnavailableError
from .portfolio import build_portfolio_brief_prompt, validate_portfolio_brief


class OpenAIPortfolioBriefGenerator:
    provider = "openai"

    def __init__(self, *, api_key: str | None = None, model: str = OPENAI_MODEL,
                 reasoning_effort: str = OPENAI_REASONING_EFFORT,
                 client: Any | None = None) -> None:
        key = api_key if api_key is not None else get_openai_api_key()
        if key is None or not key.strip():
            raise SummarizerUnavailableError("OpenAI portfolio generator is not configured")
        self.model = model
        self._reasoning_effort = reasoning_effort
        if client is None:
            from openai import OpenAI
            client = OpenAI(api_key=key, max_retries=0, timeout=25.0)
        self._client = client

    def generate(self, request: PortfolioBriefRequest) -> PortfolioBrief:
        return self.generate_with_metadata(request).result

    def generate_with_metadata(
        self, request: PortfolioBriefRequest, *, trace_id: UUID | None = None,
    ) -> ProviderExecutionResult[PortfolioBrief]:
        prompt = build_portfolio_brief_prompt(request)
        return execute_provider_call(
            trace_id=trace_id, provider=self.provider, model=self.model,
            call=lambda: self._client.responses.parse(
                model=self.model, input=prompt, reasoning={"effort": self._reasoning_effort},
                text_format=PortfolioBrief,
            ),
            parse=lambda response: validate_portfolio_brief(self._parse_response(response), request),
            usage=openai_usage, error_type=SummarizerError,
            error_message="OpenAI portfolio brief failed",
        )

    @staticmethod
    def _parse_response(response: Any) -> PortfolioBrief:
        brief = getattr(response, "output_parsed", None)
        if not isinstance(brief, PortfolioBrief):
            raise ValueError("Missing structured portfolio brief")
        return PortfolioBrief.model_validate(brief.model_dump())
