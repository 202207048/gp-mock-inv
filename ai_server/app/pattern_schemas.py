"""매매 패턴 회고의 사실 입력과 생성 결과 계약."""
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .schemas import Schema, ProfileId, SummaryText, AIExecutionMetadata

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
TAG_LABELS = {
    "day_trading": "당일 매매", "short_term": "단기 매매", "swing": "스윙 매매", "long_term": "중장기 보유",
    "concentrated": "한 종목 집중", "diversified": "분산 투자", "market_order_heavy": "시장가 위주",
    "limit_order_heavy": "지정가 위주", "opening_heavy": "장 초반 주문", "uses_misu": "미수 사용",
}
TagCode = Literal["day_trading", "short_term", "swing", "long_term", "concentrated", "diversified",
                  "market_order_heavy", "limit_order_heavy", "opening_heavy", "uses_misu"]


class HoldingDuration(Schema):
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


ComparisonCode = Literal["uses_misu", "concentrated", "day_trading", "diversified", "no_misu"]


class StyleComparisonBasis(Schema):
    matches: list[ComparisonCode] = Field(max_length=3)
    differs: list[ComparisonCode] = Field(max_length=3)

    @model_validator(mode="after")
    def unique_codes(self):
        codes = self.matches + self.differs
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

    @model_validator(mode="after")
    def known_style_basis(self):
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
    observed_patterns: list[ObservedPattern] = Field(min_length=2, max_length=4)
    style_comparison: StyleComparison | None
    limitations: list[SummaryText] = Field(max_length=10)
    disclaimer: Literal["모의투자 연습용 설명이며 투자 권유가 아닙니다."]

    @model_validator(mode="after")
    def safe_language(self):
        text = self.model_dump_json()
        if any(re.search(pattern, text) for pattern in PROHIBITED_LANGUAGE_PATTERNS):
            raise ValueError("Unsafe pattern commentary")
        return self


class PatternBriefResponse(PatternBrief):
    ai: AIExecutionMetadata | None = None
    provider: Literal["openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)


def validate_comparison_basis(brief: PatternBrief, facts: PatternFacts) -> None:
    basis = facts.style_comparison_basis
    comparison = brief.style_comparison
    if basis is None:
        if comparison is not None:
            raise ValueError("Unexpected style comparison")
        return
    if comparison is None:
        raise ValueError("Missing style comparison")
    for field in ("matches", "differs"):
        if [item.code for item in getattr(comparison, field)] != getattr(basis, field):
            raise ValueError("Comparison codes do not match basis")
