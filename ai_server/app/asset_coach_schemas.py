"""내 자산 AI 코치의 계좌 사실 입력과 조언 결과 계약."""
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from .schemas import PORTFOLIO_DISCLAIMER


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Money = Annotated[Decimal, Field(allow_inf_nan=False)]
NonNegativeMoney = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Rate = Annotated[Decimal, Field(ge=-100000, le=100000, allow_inf_nan=False)]
Percent = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=60)]


class CoachHolding(Schema):
    name: Name
    weight_pct: Percent
    pnl: Money
    return_pct: Rate | None


class AssetCoachFacts(Schema):
    as_of: AwareDatetime
    tone: Literal["loss", "profit", "flat"]
    severity: Literal["small", "moderate", "large"]
    cost_total: NonNegativeMoney
    market_total: NonNegativeMoney
    cash: NonNegativeMoney
    unrealized_pnl: Money
    return_pct: Rate | None
    cash_weight_pct: Percent | None
    holdings_count: Count = Field(ge=1)
    losing_count: Count
    gaining_count: Count
    concentration: Literal["concentrated", "moderate", "diversified"]
    top_holdings: list[CoachHolding] = Field(min_length=1, max_length=3)
    top3_weight_pct: Percent | None
    worst_holding: CoachHolding | None
    best_holding: CoachHolding | None
    # 위험을 가늠해 보는 예시 값(평가금액이 이 비율만큼 내릴 때 줄어드는 금액). 보내지 않아도 된다.
    stress_drop_pct: Percent | None = None
    stress_drop_amount: NonNegativeMoney | None = None

    @model_validator(mode="after")
    def consistent(self):
        if (self.stress_drop_pct is None) != (self.stress_drop_amount is None):
            raise ValueError("incomplete stress example")
        if self.stress_drop_pct is not None and abs(self.market_total * self.stress_drop_pct / 100 - self.stress_drop_amount) > 1:
            raise ValueError("stress example does not match totals")
        if abs(self.market_total - self.cost_total - self.unrealized_pnl) > 1:
            raise ValueError("pnl does not match totals")
        sign = "loss" if self.unrealized_pnl < 0 else "profit" if self.unrealized_pnl > 0 else "flat"
        if sign != self.tone:
            raise ValueError("tone does not match pnl")
        if self.losing_count + self.gaining_count > self.holdings_count:
            raise ValueError("too many counted holdings")
        if self.worst_holding and self.worst_holding.pnl >= 0:
            raise ValueError("worst holding must be a loss")
        if self.best_holding and self.best_holding.pnl <= 0:
            raise ValueError("best holding must be a gain")
        return self


class AssetCoachRequest(Schema):
    facts: AssetCoachFacts


class CoachCheckpoint(Schema):
    title: str
    body: str
    detail: str | None  # 덧붙이는 둘째 문단


class AssetCoachDraft(Schema):
    """모델이 채우는 부분. 길이와 개수는 생성 뒤 따로 검사한다."""
    headline: str
    intro: str
    checkpoints: list[CoachCheckpoint]
    takeaway: str
    emphasis: list[str]  # 본문에서 굵게 보여 줄 구절. 본문에 그대로 있는 것만 남긴다.


class AssetCoachResponse(AssetCoachDraft):
    disclaimer: Literal[PORTFOLIO_DISCLAIMER] = PORTFOLIO_DISCLAIMER
