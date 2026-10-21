"""보유종목 브리핑에 사용할 객관적 사실 계약."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PositionReason = Literal[
    "legacy_execution_time_unavailable", "ambiguous_execution_order",
    "invalid_trade_history", "portfolio_quantity_mismatch",
]
FactLimitation = Literal[
    "legacy_execution_time_unavailable", "ambiguous_execution_order",
    "invalid_trade_history", "portfolio_quantity_mismatch",
    "opening_trade_ambiguous", "current_price_unavailable", "invalid_average_price",
    "news_unavailable", "historical_news_unavailable", "historical_price_unavailable",
    "security_name_unavailable", "entry_price_history_partial",
]
Money = Decimal


class FactSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HoldingFacts(FactSchema):
    quantity: int = Field(strict=True, gt=0)
    avg_price: Money = Field(allow_inf_nan=False)
    current_price: Money | None = Field(default=None, gt=0, allow_inf_nan=False)
    unrealized_pnl: Money | None = Field(default=None, allow_inf_nan=False)
    return_rate: Decimal | None = Field(default=None, allow_inf_nan=False)


class PositionFacts(FactSchema):
    available: bool
    reason: PositionReason | None = None
    started_at: datetime | None = None
    opening_order_id: int | None = Field(default=None, gt=0)
    opening_price: Money | None = Field(default=None, gt=0, allow_inf_nan=False)
    buy_count: int | None = Field(default=None, ge=1)
    sell_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_availability(self):
        if self.available:
            if self.reason is not None or any(value is None for value in
                                             (self.started_at, self.buy_count, self.sell_count)):
                raise ValueError("Available position requires a complete timeline")
        elif self.reason is None or any(value is not None for value in (
            self.started_at, self.opening_order_id, self.opening_price,
            self.buy_count, self.sell_count,
        )):
            raise ValueError("Unavailable position must not contain inferred facts")
        return self


class PositionTrade(FactSchema):
    order_id: int = Field(strict=True, gt=0)
    order_type: Literal["매수", "매도"]
    quantity: int = Field(strict=True, gt=0)
    price: Money = Field(gt=0, allow_inf_nan=False)
    filled_at: datetime


class RecentNews(FactSchema):
    title: str = Field(min_length=1)
    summary: str = ""
    source: str | None = None
    date: str | None = None
    url: str | None = None


class PriceContext(FactSchema):
    current_price: Money | None = Field(default=None, gt=0, allow_inf_nan=False)
    position_start_price: Money | None = Field(default=None, description="시작 시점 시장가격. 체결가격으로 대신하지 않음.")
    change_since_position_start: Decimal | None = Field(default=None, description="시작 시점 시장가격 대비 등락률(%). 과거 가격 미조회 시 null.")


class EntryPriceContext(FactSchema):
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


class PortfolioBriefFacts(FactSchema):
    account_id: int
    symbol_code: str
    security_name: str | None
    holding: HoldingFacts
    position: PositionFacts
    trades: list[PositionTrade]
    recent_news: list[RecentNews]
    price_context: PriceContext
    entry_price_context: EntryPriceContext = Field(default_factory=lambda: EntryPriceContext(
        available=False, reason="historical_price_unavailable"))
    limitations: list[FactLimitation]


# AI 생성 결과는 사실 데이터 계약과 분리한다. 계좌/실행 진단은 공개하지 않는다.
PortfolioProfile = Literal["luna", "sol"]
PORTFOLIO_DISCLAIMER = "모의투자 연습용 설명이며 투자 권유가 아닙니다."
BriefText = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]


class PortfolioBriefBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    headline: str = Field(strict=True, min_length=1, max_length=1000)
    current_status: list[BriefText] = Field(min_length=1, max_length=3)
    entry_context: list[BriefText] = Field(max_length=3)
    position_summary: list[BriefText] = Field(max_length=3)
    recent_issues: list[BriefText] = Field(max_length=3)
    positive_factors: list[BriefText] = Field(max_length=3)
    risk_factors: list[BriefText] = Field(max_length=3)
    watch_points: list[BriefText] = Field(max_length=3)
    limitations: list[BriefText] = Field(max_length=3)
    disclaimer: Literal["모의투자 연습용 설명이며 투자 권유가 아닙니다."]


class PortfolioBriefResponse(PortfolioBriefBody):
    symbol_code: str
    security_name: str | None
