from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

class Schema(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
class TermDetectRequest(Schema):
    text: str = Field(min_length=1,max_length=3000)
    known: list[Annotated[str,Field(max_length=40)]] = Field(default_factory=list,max_length=80)
    # 글에 나온 상장사 이름. 빠뜨리지 말고 company로 설명하게 한다.
    companies: list[Annotated[str,Field(max_length=40)]] = Field(default_factory=list,max_length=20)
    # 사용자가 검색창에 직접 넣은 낱말. 글에서 찾지 않고 이 말 하나만 풀이한다.
    ask: bool = False
class DetectedTerm(Schema):
    term: str
    tier: Literal["invest","company","general"]
    meaning: str
    basic: bool
    # 기업만 채운다. knowledge는 AI가 아는 회사, context는 글에 적힌 내용만으로 설명한 회사.
    basis: Literal["knowledge","context"]
    listing: str
    index: str
    sector: str
    size: Literal["","대기업","중견","중소","스타트업"]
    # 소속 그룹(예: 현대백화점그룹)과 모회사. 확실할 때만 채운다.
    group: str
    parent: str
class TermDetectResponse(Schema):
    terms: list[DetectedTerm]
