"""내 자산 AI 코치: AI 서버로 보내는 계좌 사실과 화면에 돌려주는 조언."""
import re
from datetime import datetime
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DISCLAIMER = "모의투자 연습용 설명이며 투자 권유가 아닙니다."
# AI 서버에서 이미 검사하지만, 화면에 나가기 전에 직접 권유 표현을 한 번 더 막는다. 규칙은 AI 서버와 같게 둔다.
# '목표가 무엇이었는지'(목표 + 가)는 막지 않고, 목표 주가를 말할 때만 막는다.
TARGET_PRICE = r"목표\s*주가|목표가(?:는|를|도|로|격|\s*(?:약\s*)?\d)"
# 종목을 찍지 않아도 더 사라고 권하는 말.
BUY_PUSH = (r"매수해\s*보|매수를\s*해\s*보|매수를?\s*(?:고려|검토)해|물타기를?\s*(?:해\s*보|해도|하는\s*것도|고려해|검토해)"
            r"|더\s*사\s*보|사\s*보(?:세요|십시오|시기)|담아\s*보")
# 시키는 말의 끝. 해요체('~세요')와 합쇼체('~십시오', '~시기 바랍니다')를 함께 본다.
COMMAND = r"(?:세요|십시오|시기\s*바랍니다)"
# 계좌 전체를 두고도 줄여라·정리해라·현금으로 바꿔라 하고 시키는 말. '기준을 정리하세요'는 매매가 아니다.
ORDERS = (rf"(?:줄이|늘리|현금화하){COMMAND}|팔아[^.!?]*확보하{COMMAND}"
          rf"|(?<!기준을\s)(?<!생각을\s)(?<!기록을\s)(?<!이유를\s)정리하{COMMAND}")
FORBIDDEN = re.compile("|".join((
    r"매수하세요|매도하세요|사세요|파세요|추천합니다|추천드립니다|손절하세요|익절하세요|보장", TARGET_PRICE, BUY_PUSH, ORDERS,
    r"(?:매수|매도|손절|익절)하(?:십시오|시기\s*바랍니다)|사십시오|파십시오",
    # 오르는 쪽 가정은 기대를 부풀린다.
    r"\d\s*%[^.!?]*(?:오르|오른다|상승하|상승한다|회복하|반등하)면",
)))
# 매매를 시키는 말이 아닌 표현은 종목 지시 검사 전에 지운다.
NEUTRAL = re.compile(r"매수\s*·\s*보유\s*·\s*매도|매수\s*·\s*보유|보유\s*·\s*매도|매수한\s*(?:이유|까닭)|매[수도]\s*(?:이유|기준|당시)")
# '가장 큰 종목', '손실 종목'처럼 가리켜 팔거나 줄이라고 하는 문장.
GROUP_DIRECTIVE = re.compile(r"종목[^.!?]*(?:매도|팔아|팔지|손절|익절|비중을?\s*줄)"
                             r"|(?:종목|주식)(?:들)?(?:을|를|은|는|도|부터|만)?\s*(?:일부|전부|모두|먼저|우선)?\s*정리")


def unsafe(text):
    """한 칸의 글에 직접 권유나 종목 지시가 있는지 본다. 종목 지시는 문장 단위로 본다."""
    if FORBIDDEN.search(text):
        return True
    return any(GROUP_DIRECTIVE.search(NEUTRAL.sub(" ", sentence)) for sentence in re.split(r"(?<=[.!?])\s+", text))


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetCoachBody(Schema):
    account_id: int = Field(gt=0)


class CoachHolding(Schema):
    name: str
    weight_pct: Decimal
    pnl: Decimal
    return_pct: Decimal | None


class AssetCoachFacts(Schema):
    as_of: datetime
    tone: Literal["loss", "profit", "flat"]
    severity: Literal["small", "moderate", "large"]
    cost_total: Decimal
    market_total: Decimal
    cash: Decimal
    unrealized_pnl: Decimal
    return_pct: Decimal | None
    cash_weight_pct: Decimal | None
    holdings_count: int
    losing_count: int
    gaining_count: int
    concentration: Literal["concentrated", "moderate", "diversified"]
    top_holdings: list[CoachHolding]
    top3_weight_pct: Decimal | None
    worst_holding: CoachHolding | None
    best_holding: CoachHolding | None
    # 위험을 가늠해 보는 예시: 평가금액이 이 비율만큼 내리면 줄어드는 금액.
    stress_drop_pct: Decimal
    stress_drop_amount: Decimal


class CoachCheckpoint(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=200)
    detail: str | None = Field(None, min_length=1, max_length=200)  # 덧붙이는 둘째 문단


class AssetCoachAdvice(BaseModel):
    """AI 서버 응답. 새 칸이 생겨도 무시하고, 화면에 쓰는 칸만 다시 검사한다."""
    model_config = ConfigDict(extra="ignore")
    headline: str = Field(min_length=1, max_length=60)
    intro: str = Field(min_length=1, max_length=200)
    checkpoints: list[CoachCheckpoint] = Field(min_length=3, max_length=3)
    # 한 줄 정리와 굵게 보여 줄 구절은 없어도 된다(이전 AI 서버 응답).
    takeaway: str | None = Field(None, min_length=1, max_length=160)
    emphasis: list[str] = Field(default_factory=list, max_length=8)
    disclaimer: Literal[DISCLAIMER]

    @field_validator("headline", "intro", "takeaway")
    @classmethod
    def safe_text(cls, value):
        if value is not None and unsafe(value):
            raise ValueError("forbidden language")
        return value

    @field_validator("checkpoints")
    @classmethod
    def safe_checkpoints(cls, value):
        if any(unsafe(text) for item in value for text in (item.title, item.body, item.detail) if text):
            raise ValueError("forbidden language")
        return value

    @model_validator(mode="after")
    def emphasis_from_body(self):
        # 굵게 표시는 꾸밈이므로, 본문에 글자 그대로 없는 구절은 조용히 버린다.
        shown = [text for item in self.checkpoints for text in (item.body, item.detail) if text]
        if self.takeaway:
            shown.append(self.takeaway)
        kept = []
        for phrase in self.emphasis:
            if 2 <= len(phrase) <= 60 and phrase not in kept and any(phrase in text for text in shown):
                kept.append(phrase)
        self.emphasis = kept[:4]
        return self


class AssetCoachResponse(AssetCoachAdvice):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    tone: Literal["loss", "profit", "flat"]
    as_of: datetime
    cached: bool


class AssetCoachEmpty(Schema):
    status: Literal["no_holdings"] = "no_holdings"
