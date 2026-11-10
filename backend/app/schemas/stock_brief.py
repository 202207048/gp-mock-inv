from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, FiniteFloat, model_validator

class Schema(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
class Headline(Schema):
    id: str
    title: str = Field(min_length=1,max_length=2000)
    source: str | None
    published_at: AwareDatetime
    in_move_window: bool
    # 본 종목 기사의 앞부분. 상세 화면 제목을 제목보다 자세히 쓰는 데만 쓴다.
    summary: str | None = Field(default=None,max_length=300)
class StockInput(Schema):
    code: str = Field(pattern=r"^\d{6}$")
    name: str
    change_rate: FiniteFloat
    explain_move: bool
    headlines: list[Headline] = Field(max_length=10)
class StockBriefRequest(Schema):
    stock: StockInput
    related: list[StockInput] = Field(max_length=4)
    market_headlines: list[Headline] = Field(max_length=8)
    profile: Literal["luna"] = "luna"
    @model_validator(mode="after")
    def identities(self):
        codes=[self.stock.code]+[s.code for s in self.related]
        ids=[h.id for s in [self.stock]+self.related for h in s.headlines]+[h.id for h in self.market_headlines]
        if len(set(codes))!=len(codes) or len(set(ids))!=len(ids):raise ValueError("Duplicate identity")
        return self
class Issue(Schema):
    text: str
    ids: list[str]
class RelatedBrief(Schema):
    code: str
    title: str | None
    ids: list[str]
    banner_summary: str | None = None
    banner_ids: list[str] = Field(default_factory=list)
    banner_tone: str | None = None
    relation: str | None = None
class StockBriefResponse(Schema):
    # AI 서버가 응답 필드를 먼저 늘려도 중계가 깨지지 않게 모르는 필드는 무시한다.
    model_config=ConfigDict(extra="ignore",str_strip_whitespace=True)
    title: str | None
    title_ids: list[str]
    banner_summary: str | None = None
    banner_ids: list[str] = Field(default_factory=list)
    banner_detail: str | None = None
    banner_tone: str | None = None
    recent_issues: list[Issue]
    positive_factors: list[Issue]
    risk_factors: list[Issue]
    related: list[RelatedBrief]

class Evidence(Schema):
    title: str
    source: str | None
    url: str | None
    published_at: datetime
class RelatedStock(Schema):
    code: str
    name: str
    price: int
    change_rate: float
    title: str | None = None
    banner_summary: str | None = None
    banner_tone: Literal["호재","악재","엇갈림"] | None = None
    # 본 종목과의 관계(예: HBM 장비 고객사). 소식이 없을 때 카드에 보여 준다.
    relation: str | None = None
class StockPublicResponse(Schema):
    symbol_code: str
    name: str
    price: int
    change_rate: float
    as_of: AwareDatetime
    generated_at: AwareDatetime
    ai_available: bool = False
    title: str | None = None
    banner_summary: str | None = None
    # 상세 화면 제목. 배너와 같은 소식을 더 자세히 쓴 것이고, 배너가 있을 때만 있다.
    banner_detail: str | None = None
    # 배너 소식의 성격. 주가 방향과 반대일 수 있다(예: 상승 중 악재).
    banner_tone: Literal["호재","악재","엇갈림"] | None = None
    recent_issues: list[str] = Field(default_factory=list)
    positive_factors: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    citations: dict[str, list[Evidence]] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list,max_length=5)
    related: list[RelatedStock] = Field(default_factory=list,max_length=4)
    source: str = "네이버 증권"
    disclaimer: str = "기사 제목을 바탕으로 한 요약이며 전망이나 투자 조언이 아닙니다."

    @property
    def news_summary(self) -> list[str]:
        return list(dict.fromkeys(self.recent_issues + self.positive_factors + self.risk_factors))
