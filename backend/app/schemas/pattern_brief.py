"""매매 패턴 회고의 사실 입력과 생성 결과 계약."""
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import Field, model_validator
from pydantic import BaseModel, ConfigDict
from .portfolio_brief import BriefText as SummaryText, PortfolioProfile
from app.services.news_summary_service import AIExecutionMetadata

class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

ProfileId = str

# 단어의 언급 대신 직접 권유·지시와 평가 부여 형태를 검사한다.
PROHIBITED_LANGUAGE_PATTERNS = (
    r"매수하세요", r"매도하세요", r"사세요", r"파세요",
    r"추천합니다", r"추천드립니다", r"목표가는",
    r"[0-9]+(?:\.[0-9]+)?\s*점입니다", r"등급입니다",
)


COMPARISON_DIRECTIVES = ("하세요", "해야 해요", "해 보세요")


Number = Annotated[Decimal, Field(allow_inf_nan=False)]
Count = Annotated[Decimal, Field(ge=0, allow_inf_nan=False, decimal_places=0)]
Percent = Annotated[Decimal, Field(ge=0, le=100, allow_inf_nan=False)]


class PatternPeriod(Schema):
    first_filled_at: datetime | None
    last_filled_at: datetime | None
    days: Count

    @model_validator(mode="after")
    def ordered(self):
        if (self.first_filled_at is None) != (self.last_filled_at is None):
            raise ValueError("Incomplete period")
        if self.first_filled_at is not None:
            if (self.first_filled_at.utcoffset() is None) != (self.last_filled_at.utcoffset() is None):
                raise ValueError("Inconsistent period timezones")
            if self.first_filled_at > self.last_filled_at:
                raise ValueError("Invalid period")
        return self


class PatternCounts(Schema):
    buy_filled: Count
    sell_filled: Count
    symbols_traded: Count


class PatternSymbol(Schema):
    symbol_code: str = Field(min_length=1, max_length=32)
    name: str | None = Field(max_length=200)
    buy_share_pct: Percent


class PatternConcentration(Schema):
    top_symbol_buy_share_pct: Percent
    top_symbols: list[PatternSymbol] = Field(max_length=3)


class PatternRealized(Schema):
    sells_with_basis: Count
    wins: Count
    losses: Count
    breakeven: Count
    unknown_basis: Count
    avg_realized_return_pct: Number | None


class PatternOrderStyle(Schema):
    market_ratio_pct: Percent
    limit_ratio_pct: Percent
    misu_count: Count


class PatternTiming(Schema):
    opening_30min_ratio_pct: Percent


KNOWN_INVESTMENT_STYLES = {"안정형", "안정추구형", "위험중립형", "적극투자형", "공격투자형"}
INVESTMENT_STYLE_DESCRIPTIONS = {
    "안정형": "원금 손실을 원하지 않고 예금 수준의 안정성을 중시",
    "안정추구형": "원금 손실을 최소화하며 안정적인 수익을 중시",
    "위험중립형": "수익을 위해 어느 정도의 손실 가능성을 받아들임",
    "적극투자형": "원금 보전보다 높은 수익을 추구하며 상당한 손실 가능성을 받아들임",
    "공격투자형": "높은 수익을 위해 큰 손실 가능성도 적극적으로 받아들임",
}
DataConfidence = Literal["preliminary", "moderate", "sufficient"]

TAG_LABELS = {
    "day_trading": "당일 매매", "short_term": "단기 매매", "swing": "스윙 매매", "long_term": "중장기 보유",
    "concentrated": "한 종목 집중", "diversified": "분산 투자", "market_order_heavy": "시장가 위주",
    "limit_order_heavy": "지정가 위주", "opening_heavy": "장 초반 주문", "uses_misu": "미수 사용",
}
TagCode = Literal["day_trading", "short_term", "swing", "long_term", "concentrated", "diversified",
                  "market_order_heavy", "limit_order_heavy", "opening_heavy", "uses_misu"]


class HoldingDuration(Schema):
    matched_sell_orders: Count
    matched_sell_quantity: Count
    unmatched_sell_quantity: Count
    avg_holding_days: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None
    same_day_ratio_pct: Percent | None

    @model_validator(mode="after")
    def availability(self):
        if self.matched_sell_quantity == 0:
            if self.avg_holding_days is not None or self.same_day_ratio_pct is not None:
                raise ValueError("Unknown holding duration must be null")
        elif self.avg_holding_days is None or self.same_day_ratio_pct is None:
            raise ValueError("Matched duration requires statistics")
        return self


class StyleTag(Schema):
    code: TagCode
    label: str

    @model_validator(mode="after")
    def canonical_label(self):
        if self.label != TAG_LABELS[self.code]:
            raise ValueError("Invalid tag label")
        return self


ComparisonCode = Literal["uses_misu", "concentrated", "day_trading", "market_heavy", "opening_heavy",
                         "no_misu", "diversified", "patient_hold", "limit_used", "spread_timing"]


class BehaviorStrength(Schema):
    code: ComparisonCode
    strength: Percent


class StyleComparisonBasis(Schema):
    matches: list[BehaviorStrength] = Field(max_length=3)
    differs: list[BehaviorStrength] = Field(max_length=3)

    @model_validator(mode="after")
    def unique_codes(self):
        codes = [item.code for item in self.matches + self.differs]
        if len(set(codes)) != len(codes):
            raise ValueError("Duplicate comparison code")
        return self


class ComparisonItem(Schema):
    code: ComparisonCode
    text: SummaryText

    @model_validator(mode="after")
    def safe_text(self):
        if any(phrase in self.text for phrase in COMPARISON_DIRECTIVES + ("잘", "좋은", "훌륭")):
            raise ValueError("Directive or praise in style comparison")
        return self


class StyleComparison(Schema):
    matches: list[ComparisonItem] = Field(max_length=3)
    differs: list[ComparisonItem] = Field(max_length=3)


class PatternFacts(Schema):
    period: PatternPeriod
    counts: PatternCounts
    concentration: PatternConcentration
    realized: PatternRealized
    order_style: PatternOrderStyle
    timing: PatternTiming
    holding: HoldingDuration
    style_tags: list[StyleTag] = Field(max_length=5)
    holdings_count: Count
    investment_style: str | None = Field(max_length=200)
    style_comparison_basis: StyleComparisonBasis | None
    pattern_highlights: list[BehaviorStrength] = Field(max_length=10)
    data_confidence: DataConfidence
    investment_style_description: str | None = Field(max_length=200)

    @model_validator(mode="after")
    def known_style_basis(self):
        count = self.counts.buy_filled + self.counts.sell_filled
        expected = "preliminary" if count < 10 else "moderate" if count < 30 else "sufficient"
        if self.data_confidence != expected:
            raise ValueError("Inconsistent data confidence")
        if self.data_confidence == "preliminary" and self.style_comparison_basis is not None:
            if len(self.style_comparison_basis.matches) > 1 or len(self.style_comparison_basis.differs) > 1:
                raise ValueError("Preliminary comparison exceeds sample limit")
        if self.investment_style not in KNOWN_INVESTMENT_STYLES and self.style_comparison_basis is not None:
            raise ValueError("Unknown investment style cannot have comparison basis")
        return self


class PatternBriefRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    facts: PatternFacts


class ObservedPattern(Schema):
    title: SummaryText
    evidence: SummaryText


class PatternBrief(Schema):
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    observed_patterns: list[ObservedPattern] = Field(min_length=1, max_length=4)
    style_comparison: StyleComparison | None
    limitations: list[SummaryText] = Field(max_length=10)
    disclaimer: Literal["모의투자 연습용 설명이며 투자 권유가 아닙니다."]

    @model_validator(mode="after")
    def safe_language(self):
        text = self.model_dump_json()
        if any(re.search(pattern, text) for pattern in PROHIBITED_LANGUAGE_PATTERNS):
            raise ValueError("Unsafe pattern commentary")
        return self


class PatternGenerateRequest(Schema):
    account_id: int = Field(strict=True, gt=0)
    profile: PortfolioProfile = "luna"


class PatternInsufficient(Schema):
    status: Literal["insufficient"] = "insufficient"
    filled_count: int


COMPARISON_NOTES: dict[ComparisonCode, dict[str, str]] = {
    "uses_misu": {"reason":"미수는 가진 현금보다 큰 금액을 매수하는 방식이라 손익 폭이 커지고, 정해진 기한 안에 갚지 못하면 반대매매가 될 수 있어요.", "option":"보유 현금 안에서만 주문하면 손실을 투자한 금액 안으로 묶을 수 있어요."},
    "concentrated": {"reason":"한 종목 비중이 크면 그 종목의 등락이 전체 수익률을 대부분 좌우해요.", "option":"매수 금액을 여러 종목에 나누면 한 종목의 변동이 전체에 주는 영향이 줄어요."},
    "day_trading": {"reason":"같은 날 사고파는 거래는 짧은 시간의 가격 변동에 결과가 크게 좌우돼요.", "option":"매수 전에 보유 기간을 정해 두면 짧은 변동에 덜 흔들릴 수 있어요."},
    "market_heavy": {"reason":"시장가 주문은 바로 체결되지만 체결 가격을 미리 정할 수 없어요.", "option":"지정가 주문을 쓰면 원하는 가격을 정해 두고 체결을 기다릴 수 있어요."},
    "opening_heavy": {"reason":"장 시작 직후에는 밤사이 쌓인 주문이 한꺼번에 몰려 하루 중 가격 변동이 큰 편이에요.", "option":"시초가가 자리 잡은 뒤 주문하면 급한 가격 변동을 피할 수 있어요."},
    "no_misu": {"reason":"현금 안에서만 거래하면 손실이 투자한 금액을 넘지 않아요."},
    "diversified": {"reason":"여러 종목에 나눠 사면 한 종목의 급등락이 전체 수익률에 주는 영향이 줄어요."},
    "patient_hold": {"reason":"보유 기간이 길면 짧은 가격 변동에 따라 사고파는 일이 줄어요."},
    "limit_used": {"reason":"지정가 주문은 체결 가격을 미리 정해 두기 때문에 예상보다 비싸게 사는 일을 줄일 수 있어요."},
    "spread_timing": {"reason":"주문 시간이 하루 중 고르게 나뉘면 장 초반의 큰 가격 변동에 덜 영향을 받아요."},
}


class ComparisonNote(Schema):
    reason: SummaryText
    option: SummaryText | None


class PublicComparisonItem(ComparisonItem):
    note: ComparisonNote | None


class PublicStyleComparison(Schema):
    matches: list[PublicComparisonItem] = Field(max_length=3)
    differs: list[PublicComparisonItem] = Field(max_length=3)


def attach_comparison_notes(comparison: dict | None) -> dict | None:
    if comparison is None:
        return None
    result = {}
    for field in ("matches", "differs"):
        result[field] = []
        for item in comparison[field]:
            fixed = COMPARISON_NOTES.get(item["code"])
            note = None if fixed is None else {
                "reason": fixed["reason"], "option": fixed.get("option") if field == "differs" else None,
            }
            result[field].append({**item, "note": note})
    return result


class PatternPublicResponse(PatternBrief):
    style_comparison: PublicStyleComparison | None
    data_confidence: DataConfidence
    filled_count: int = Field(strict=True, ge=0)
    investment_style_description: str | None = Field(max_length=200)
    style_tags: list[StyleTag] = Field(max_length=5)


def validate_comparison_basis(brief: PatternBrief, facts: PatternFacts) -> None:
    basis = facts.style_comparison_basis
    comparison = brief.style_comparison
    if facts.data_confidence != "preliminary" and len(brief.observed_patterns) < 2:
        raise ValueError("At least two observations required")
    if basis is None:
        if comparison is not None:
            raise ValueError("Unexpected style comparison")
        return
    if comparison is None:
        raise ValueError("Missing style comparison")
    for field in ("matches", "differs"):
        if [item.code for item in getattr(comparison, field)] != [item.code for item in getattr(basis, field)]:
            raise ValueError("Comparison codes do not match basis")
