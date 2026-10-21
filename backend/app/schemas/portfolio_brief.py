"""보유종목 브리핑에 사용할 객관적 사실 계약."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

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
    "security_name_unavailable",
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


class PortfolioBriefFacts(FactSchema):
    account_id: int
    symbol_code: str
    security_name: str | None
    holding: HoldingFacts
    position: PositionFacts
    trades: list[PositionTrade]
    recent_news: list[RecentNews]
    price_context: PriceContext
    limitations: list[FactLimitation]
