from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Number = Annotated[Decimal, Field(allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0, strict=True)]
class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
class LeaderInput(Schema):
    name: str = Field(min_length=1,max_length=200)
    change_rate: Number
class Headline(Schema):
    id: str = Field(min_length=1,max_length=100)
    title: str = Field(min_length=1,max_length=2000)
    source: str | None = Field(default=None,max_length=200)
    published_at: datetime
class ThemeInput(Schema):
    id: str
    name: str = Field(min_length=1,max_length=200)
    direction: Literal["rising","falling"]
    change_rate: Number
    rise_count: Count
    fall_count: Count
    total_count: Count
    leaders: list[LeaderInput] = Field(max_length=3)
    headlines: list[Headline] = Field(max_length=8)
    @model_validator(mode="after")
    def unique_headlines(self):
        if len({h.id for h in self.headlines}) != len(self.headlines):raise ValueError("Duplicate headline")
        return self
class MarketIndex(Schema):
    name: str
    change_rate: Number
class MarketContext(Schema):
    indices: list[MarketIndex] = Field(default_factory=list,max_length=2)
    headlines: list[Headline] = Field(default_factory=list,max_length=8)
    @model_validator(mode="after")
    def unique_ids(self):
        if len({h.id for h in self.headlines})!=len(self.headlines):raise ValueError("Duplicate market id")
        return self
class ThemeBriefRequest(Schema):
    themes: list[ThemeInput] = Field(min_length=1,max_length=6)
    profile: Literal["luna"] = "luna"
    market: MarketContext = Field(default_factory=MarketContext)
    @model_validator(mode="after")
    def unique_themes(self):
        if len({t.id for t in self.themes}) != len(self.themes):raise ValueError("Duplicate theme")
        market_ids={h.id for h in self.market.headlines}
        if any(market_ids & {h.id for h in t.headlines} for t in self.themes):raise ValueError("Ambiguous citation ids")
        return self
class ThemeReason(Schema):
    id: str
    reason: str | None
    headline_ids: list[str]
class ThemeBriefResponse(Schema):
    themes: list[ThemeReason]

# 화면용 응답은 JSON 숫자로 내려준다. (Decimal은 문자열로 직렬화된다)
class Leader(Schema):
    code: str
    name: str
    price: int
    change_rate: float
class Evidence(Schema):
    title: str
    source: str | None
    url: str | None
    published_at: datetime
class Theme(Schema):
    id: str
    name: str
    change_rate: float
    rise_count: Count
    fall_count: Count
    steady_count: Count
    total_count: Count
    leaders: list[Leader]
    reason: str | None = None
    evidence: list[Evidence] = Field(default_factory=list,max_length=3)
class ThemePublicResponse(Schema):
    source: Literal["네이버 증권"] = "네이버 증권"
    as_of: datetime
    generated_at: datetime
    rising: list[Theme]
    falling: list[Theme]
    disclaimer: Literal["기사 제목을 바탕으로 한 요약이며 전망이나 투자 조언이 아닙니다."] = "기사 제목을 바탕으로 한 요약이며 전망이나 투자 조언이 아닙니다."
