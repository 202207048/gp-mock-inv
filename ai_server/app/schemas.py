from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)



TokenCount = Annotated[int, Field(strict=True, ge=0)]


class UsageMetrics(Schema):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_tokens: TokenCount | None = None


ProfileId = Annotated[str, StringConstraints(
    strict=True, strip_whitespace=False, pattern=r"^[a-z][a-z0-9_-]{0,31}$",
)]
ProfileRevision = Annotated[int, Field(strict=True, ge=1)]
ProfileTask = Literal["news_analysis", "stock_news_summary", "portfolio_brief"]


class ModelProfileCatalogEntry(Schema):
    id: ProfileId
    revision: ProfileRevision
    display_name: str
    description: str
    category: str
    enabled: bool
    supported_tasks: tuple[ProfileTask, ...]


class AIExecutionMetadata(Schema):
    """One observed SDK generation attempt, including failed attempts."""
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    trace_id: UUID
    operation_id: UUID
    provider: Literal["openai", "gemini"]
    model: str = Field(strict=True, min_length=1, max_length=200)
    profile_id: ProfileId | None = None
    profile_revision: ProfileRevision | None = None
    usage: UsageMetrics
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


    @model_validator(mode="after")
    def profile_identity_is_complete(self):
        if (self.profile_id is None) != (self.profile_revision is None):
            raise ValueError("Profile ID and revision must be supplied together")
        return self


class ProviderSettingsResponse(Schema):
    provider: Literal["openai", "gemini"]
    model: str


class ProviderSwitchRequest(Schema):
    provider: str

class Article(Schema):
    article_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str | None = None
    url: str | None = None
    source: str | None = None
    published_at: datetime | None = None
    symbol_code: str | None = None


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class Sentiment(Schema):
    label: SentimentLabel
    confidence: float = Field(ge=0, le=1)


class ImportanceLabel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def importance_label_for_score(score: float) -> ImportanceLabel:
    # Keep importance band boundaries in one place for every provider.
    if score < 0.35:
        return ImportanceLabel.LOW
    if score < 0.70:
        return ImportanceLabel.MEDIUM
    return ImportanceLabel.HIGH


class Importance(Schema):
    score: float = Field(ge=0, le=1)

    @computed_field
    @property
    def label(self) -> ImportanceLabel:
        return importance_label_for_score(self.score)


class ArticleAnalysis(Schema):
    article_id: str = Field(min_length=1)
    sentiment: Sentiment
    importance: Importance


class ArticlePrediction(Schema):
    article_id: str = Field(min_length=1)
    sentiment: Sentiment
    importance_score: float = Field(ge=0, le=1)


class NewsSentimentBatch(Schema):
    articles: list[ArticlePrediction]


class NewsAnalysisRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    articles: list[Article] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def article_ids_are_unique(self):
        ids = [article.article_id for article in self.articles]
        if len(ids) != len(set(ids)):
            raise ValueError("article_id values must be unique within a request")
        return self


class NewsAnalysisResponse(Schema):
    ai: AIExecutionMetadata | None = None
    articles: list[ArticleAnalysis]


# Stock-level synthesis of supplied headlines/excerpts; not per-article summaries.
SummaryText = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
SummaryTag = Annotated[str, Field(strict=True, min_length=1, max_length=100)]


class StockNewsArticle(Schema):
    title: str = Field(min_length=1, max_length=2000)
    summary: str = Field(default="", max_length=20000)
    source: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=200)


class StockNewsSummaryRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    symbol_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    change_rate: float | None = Field(default=None, allow_inf_nan=False)
    articles: list[StockNewsArticle] = Field(min_length=1, max_length=5)


class StockNewsBrief(Schema):
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    summary: list[SummaryText] = Field(min_length=1, max_length=3)
    tags: list[SummaryTag] = Field(max_length=3)
    disclaimer: str = Field(strict=True, min_length=1, max_length=1000)


class StockNewsSummaryResponse(StockNewsBrief):
    ai: AIExecutionMetadata | None = None
    # Internal metadata lets Backend cache by the provider actually used.
    provider: Literal["openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)


# Independent AI contract: Backend projects only these facts, never account/user IDs.
PortfolioPositionReason = Literal[
    "legacy_execution_time_unavailable", "ambiguous_execution_order",
    "invalid_trade_history", "portfolio_quantity_mismatch",
]
PortfolioLimitation = Literal[
    "legacy_execution_time_unavailable", "ambiguous_execution_order",
    "invalid_trade_history", "portfolio_quantity_mismatch", "opening_trade_ambiguous",
    "current_price_unavailable", "invalid_average_price", "news_unavailable", "entry_price_history_partial",
    "historical_news_unavailable", "historical_price_unavailable", "security_name_unavailable",
]
PortfolioMoney = Annotated[Decimal, Field(allow_inf_nan=False)]
PortfolioPositiveMoney = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
PORTFOLIO_DISCLAIMER = "모의투자 연습용 설명이며 투자 권유가 아닙니다."


class PortfolioHolding(Schema):
    quantity: int = Field(strict=True, gt=0)
    avg_price: PortfolioMoney
    current_price: PortfolioPositiveMoney | None = None
    unrealized_pnl: PortfolioMoney | None = None
    return_rate: PortfolioMoney | None = None

    @model_validator(mode="after")
    def derived_values_require_valid_prices(self):
        if (self.current_price is None or self.avg_price <= 0) and (
            self.unrealized_pnl is not None or self.return_rate is not None
        ):
            raise ValueError("Derived holding values require current price and positive average price")
        return self


class PortfolioPosition(Schema):
    available: bool
    reason: PortfolioPositionReason | None = None
    started_at: datetime | None = None
    opening_order_id: int | None = Field(default=None, strict=True, gt=0)
    opening_price: PortfolioPositiveMoney | None = None
    buy_count: int | None = Field(default=None, strict=True, ge=1)
    sell_count: int | None = Field(default=None, strict=True, ge=0)

    @model_validator(mode="after")
    def position_facts_match_availability(self):
        if self.available:
            if self.reason is not None or any(v is None for v in
                                             (self.started_at, self.buy_count, self.sell_count)):
                raise ValueError("Available position requires timeline facts")
            if (self.opening_order_id is None) != (self.opening_price is None):
                raise ValueError("Opening order and price must be supplied together")
        elif self.reason is None or any(v is not None for v in (
            self.started_at, self.opening_order_id, self.opening_price, self.buy_count, self.sell_count,
        )):
            raise ValueError("Unavailable position must not contain inferred facts")
        return self


class PortfolioTrade(Schema):
    order_id: int = Field(strict=True, gt=0)
    order_type: Literal["매수", "매도"]
    quantity: int = Field(strict=True, gt=0)
    price: PortfolioPositiveMoney
    filled_at: datetime


class PortfolioRecentNews(StockNewsArticle):
    url: str | None = Field(default=None, max_length=4000)


class PortfolioEntryPriceContext(Schema):
    available: bool
    reason: Literal["position_unavailable", "historical_price_unavailable"] | None = None
    anchor_at: datetime | None = None
    opening_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    prior_close_date: date | None = None
    prior_close: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    return_5_sessions: Decimal | None = Field(default=None, allow_inf_nan=False)
    return_20_sessions: Decimal | None = Field(default=None, allow_inf_nan=False)
    high_20_sessions: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    low_20_sessions: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    range_sessions: int = Field(default=0, strict=True, ge=0, le=20)
    opening_vs_prior_close_pct: Decimal | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_entry_context(self):
        prices = (self.prior_close, self.high_20_sessions, self.low_20_sessions)
        if self.available:
            if (self.reason is not None or self.anchor_at is None
                    or self.anchor_at.utcoffset() is None or self.prior_close_date is None
                    or any(v is None for v in prices) or self.range_sessions == 0):
                raise ValueError("Available entry context requires completed session facts")
            anchor_day = self.anchor_at.astimezone(timezone(timedelta(hours=9))).date()
            if self.prior_close_date >= anchor_day:
                raise ValueError("Entry context must exclude the start day and all future bars")
            if not self.low_20_sessions <= self.prior_close <= self.high_20_sessions:
                raise ValueError("Invalid historical range")
        elif (self.reason is None or any(v is not None for v in (
            *prices, self.prior_close_date, self.return_5_sessions, self.return_20_sessions,
            self.opening_vs_prior_close_pct,
        )) or self.range_sessions != 0):
            raise ValueError("Unavailable entry context must not contain historical facts")
        if self.reason == "position_unavailable" and (
            self.anchor_at is not None or self.opening_price is not None
        ):
            raise ValueError("Unknown position cannot supply an entry anchor")
        if ((self.return_5_sessions is not None and self.range_sessions < 6)
                or (self.return_20_sessions is not None and self.range_sessions < 20)):
            raise ValueError("Historical returns require enough completed sessions")
        if self.opening_price is None and self.opening_vs_prior_close_pct is not None:
            raise ValueError("Opening comparison requires a known fill price")
        return self


class PortfolioBriefRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    symbol_code: str = Field(min_length=1, max_length=32)
    security_name: str | None = Field(default=None, min_length=1, max_length=200)
    holding: PortfolioHolding
    position: PortfolioPosition
    entry_price_context: PortfolioEntryPriceContext | None = None
    trades: list[PortfolioTrade]
    recent_news: list[PortfolioRecentNews] = Field(max_length=5)
    limitations: list[PortfolioLimitation]

    @model_validator(mode="after")
    def unavailable_position_has_no_trades(self):
        if not self.position.available and self.trades:
            raise ValueError("Unavailable position must not contain reconstructed trades")
        context = self.entry_price_context
        if context is not None:
            if not self.position.available and (context.available or context.anchor_at is not None
                                                or context.opening_price is not None):
                raise ValueError("Unknown position cannot have entry context")
            if context.anchor_at is not None and context.anchor_at != self.position.started_at:
                raise ValueError("Entry anchor must match the current position")
            if context.available and context.opening_price != self.position.opening_price:
                raise ValueError("Entry opening price must match the known fill")
        return self


class PortfolioBrief(Schema):
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    current_status: list[SummaryText] = Field(min_length=1, max_length=3)
    entry_context: list[SummaryText] = Field(max_length=3)
    position_summary: list[SummaryText] = Field(max_length=3)
    recent_issues: list[SummaryText] = Field(max_length=3)
    positive_factors: list[SummaryText] = Field(max_length=3)
    risk_factors: list[SummaryText] = Field(max_length=3)
    watch_points: list[SummaryText] = Field(max_length=3)
    limitations: list[SummaryText] = Field(max_length=3)
    disclaimer: Literal["모의투자 연습용 설명이며 투자 권유가 아닙니다."]


class PortfolioBriefResponse(PortfolioBrief):
    ai: AIExecutionMetadata | None = None
    provider: Literal["openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)
