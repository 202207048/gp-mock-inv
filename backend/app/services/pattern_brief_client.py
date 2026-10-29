"""Pattern Brief 전용 AI Server 중계. Provider SDK/DB/캐시는 사용하지 않는다."""
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.services.ai_service_auth import AIServiceNotConfigured, service_headers

from app.schemas.pattern_brief import PatternBrief as PatternBriefBody, PatternFacts as PatternBriefFacts, validate_comparison_basis
from app.schemas.portfolio_brief import PortfolioProfile as PatternProfile
from app.services.news_summary_service import (
    AIExecutionMetadata, ProviderSettings, _ExecutionIdentity,
)


class PatternBriefUnavailable(Exception):
    """브라우저에 노출 가능한 상태와 고정 메시지만 보유한다."""

    def __init__(self, status_code: int = 502):
        self.status_code = status_code
        super().__init__("매매 패턴 회고를 잠시 사용할 수 없습니다.")


class PatternBriefResult(PatternBriefBody, ProviderSettings):
    ai: AIExecutionMetadata | None = None

    def brief(self) -> dict:
        return self.model_dump(exclude={"provider", "model", "ai"})


def project_pattern_brief(facts: PatternBriefFacts, profile: PatternProfile = "luna") -> dict:
    """AI 계약 필드만 명시적으로 선택하며 Decimal은 JSON 십진수 문자열로 보존한다."""
    if profile not in ("luna", "sol"):
        raise ValueError("Unsupported pattern profile")
    return {"facts": facts.model_dump(mode="json"), "profile": profile, "include_ai_metadata": True}


def _parse_result(data: object, facts: PatternBriefFacts, profile: PatternProfile,
                  trace_id: UUID) -> PatternBriefResult:
    try:
        if not isinstance(data, dict):
            raise ValueError("Invalid result structure")
        core = dict(data)
        metadata = core.pop("ai", None)
        result = PatternBriefResult.model_validate(core)
        # Always requesting explicit profile/metadata makes identity mandatory here.
        # Numeric diagnostics may be discarded, but identity must never be discarded.
        if not isinstance(metadata, dict) or set(metadata) - AIExecutionMetadata.model_fields.keys():
            raise ValueError("Invalid execution metadata structure")
        identity = _ExecutionIdentity.model_validate({
            name: metadata[name] for name in _ExecutionIdentity.model_fields if name in metadata
        })
        if ((identity.provider, identity.model) != (result.provider, result.model)
                or identity.trace_id != trace_id or identity.profile_id != profile):
            raise ValueError("Execution identity mismatch")
        validate_comparison_basis(result, facts)
    except ValueError:
        raise PatternBriefUnavailable() from None

    try:
        observation = AIExecutionMetadata.model_validate(metadata)
    except ValidationError:
        return result  # Valid body/identity survive unusable optional usage/latency.
    return result.model_copy(update={"ai": observation})


async def request_pattern_brief(base_url: str, facts: PatternBriefFacts, *,
                                  profile: PatternProfile = "luna",
                                  trace_id: UUID) -> PatternBriefResult:
    try:
        headers = service_headers()
    except AIServiceNotConfigured:
        raise PatternBriefUnavailable(503) from None
    headers["X-Trace-ID"] = str(trace_id)
    payload = project_pattern_brief(facts, profile)
    if not isinstance(base_url, str) or not base_url.strip():
        raise PatternBriefUnavailable(503)
    try:
        base = httpx.URL(base_url.strip())
        if base.scheme not in ("http", "https") or not base.host:
            raise PatternBriefUnavailable(503)
    except httpx.InvalidURL:
        raise PatternBriefUnavailable(503) from None
    try:
        # Match the existing news-summary client lifecycle and timeout. No retries.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                base_url.strip().rstrip("/") + "/brief/pattern", json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        # Upstream authentication errors are never forwarded as browser login 401s.
        status = exc.response.status_code
        # Upstream 422 rejects our internal contract, not the validated user query.
        raise PatternBriefUnavailable(503 if status == 503 else 502) from None
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.ConnectError):
        raise PatternBriefUnavailable(503) from None
    except (httpx.HTTPError, ValueError):
        raise PatternBriefUnavailable() from None
    return _parse_result(data, facts, profile, trace_id)
