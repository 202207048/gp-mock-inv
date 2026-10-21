"""Portfolio Brief 전용 AI Server 중계. Provider SDK/DB/캐시는 사용하지 않는다."""
from uuid import UUID

import httpx
from pydantic import ValidationError

from app.services.ai_service_auth import AIServiceNotConfigured, service_headers

from app.schemas.portfolio_brief import (
    PortfolioBriefBody, PortfolioBriefFacts, PortfolioProfile,
)
from app.services.news_summary_service import (
    AIExecutionMetadata, ProviderSettings, _ExecutionIdentity,
)


class PortfolioBriefUnavailable(Exception):
    """브라우저에 노출 가능한 상태와 고정 메시지만 보유한다."""

    def __init__(self, status_code: int = 502):
        self.status_code = status_code
        super().__init__("보유종목 AI 브리핑을 잠시 사용할 수 없습니다.")


class PortfolioBriefResult(PortfolioBriefBody, ProviderSettings):
    ai: AIExecutionMetadata | None = None

    def brief(self) -> dict:
        return self.model_dump(exclude={"provider", "model", "ai"})


def project_portfolio_brief(facts: PortfolioBriefFacts, profile: PortfolioProfile = "luna") -> dict:
    """AI 계약 필드만 명시적으로 선택하며 Decimal은 JSON 십진수 문자열로 보존한다."""
    if profile not in ("luna", "sol"):
        raise ValueError("Unsupported portfolio profile")
    return {
        "symbol_code": facts.symbol_code,
        "security_name": facts.security_name,
        "holding": facts.holding.model_dump(mode="json", include={
            "quantity", "avg_price", "current_price", "unrealized_pnl", "return_rate",
        }),
        "position": facts.position.model_dump(mode="json", include={
            "available", "reason", "started_at", "opening_order_id", "opening_price",
            "buy_count", "sell_count",
        }),
        # These IDs remain only because the current AI contract requires them.
        "trades": [trade.model_dump(mode="json", include={
            "order_id", "order_type", "quantity", "price", "filled_at",
        }) for trade in facts.trades],
        "recent_news": [news.model_dump(mode="json", include={
            "title", "summary", "source", "date", "url",
        }) for news in facts.recent_news],
        "entry_price_context": facts.entry_price_context.model_dump(mode="json", include={
            "available", "reason", "anchor_at", "opening_price", "prior_close_date", "prior_close",
            "return_5_sessions", "return_20_sessions", "high_20_sessions", "low_20_sessions",
            "range_sessions", "opening_vs_prior_close_pct",
        }),
        "limitations": list(facts.limitations),
        "profile": profile,
        "include_ai_metadata": True,
    }


def _parse_result(data: object, facts: PortfolioBriefFacts, profile: PortfolioProfile,
                  trace_id: UUID) -> PortfolioBriefResult:
    try:
        if not isinstance(data, dict):
            raise ValueError("Invalid result structure")
        core = dict(data)
        metadata = core.pop("ai", None)
        result = PortfolioBriefResult.model_validate(core)
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
        if not facts.entry_price_context.available and result.entry_context:
            raise ValueError("Unavailable entry context reconstructed")
        if not facts.position.available and result.position_summary:
            raise ValueError("Unavailable position reconstructed")
        if not facts.recent_news and result.recent_issues:
            raise ValueError("Missing news reconstructed")
    except ValueError:
        raise PortfolioBriefUnavailable() from None

    try:
        observation = AIExecutionMetadata.model_validate(metadata)
    except ValidationError:
        return result  # Valid body/identity survive unusable optional usage/latency.
    return result.model_copy(update={"ai": observation})


async def request_portfolio_brief(base_url: str, facts: PortfolioBriefFacts, *,
                                  profile: PortfolioProfile = "luna",
                                  trace_id: UUID) -> PortfolioBriefResult:
    try:
        headers = service_headers()
    except AIServiceNotConfigured:
        raise PortfolioBriefUnavailable(503) from None
    headers["X-Trace-ID"] = str(trace_id)
    payload = project_portfolio_brief(facts, profile)
    if not isinstance(base_url, str) or not base_url.strip():
        raise PortfolioBriefUnavailable(503)
    try:
        base = httpx.URL(base_url.strip())
        if base.scheme not in ("http", "https") or not base.host:
            raise PortfolioBriefUnavailable(503)
    except httpx.InvalidURL:
        raise PortfolioBriefUnavailable(503) from None
    try:
        # Match the existing news-summary client lifecycle and timeout. No retries.
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                base_url.strip().rstrip("/") + "/brief/portfolio", json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        # Upstream authentication errors are never forwarded as browser login 401s.
        status = exc.response.status_code
        # Upstream 422 rejects our internal contract, not the validated user query.
        raise PortfolioBriefUnavailable(503 if status == 503 else 502) from None
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.ConnectError):
        raise PortfolioBriefUnavailable(503) from None
    except (httpx.HTTPError, ValueError):
        raise PortfolioBriefUnavailable() from None
    return _parse_result(data, facts, profile, trace_id)
