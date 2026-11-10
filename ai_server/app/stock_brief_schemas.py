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
    banner_summary: str | None
    banner_ids: list[str]
    banner_tone: Literal["호재","악재","엇갈림"] | None
    relation: str | None
class StockBriefResponse(Schema):
    title: str | None
    title_ids: list[str]
    banner_summary: str | None
    banner_ids: list[str]
    banner_detail: str | None
    banner_tone: Literal["호재","악재","엇갈림"] | None
    recent_issues: list[Issue]
    positive_factors: list[Issue]
    risk_factors: list[Issue]
    related: list[RelatedBrief]
