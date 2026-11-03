from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field

class Schema(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
class TermDetectRequest(Schema):
    text: str = Field(min_length=1,max_length=3000)
    known: list[Annotated[str,Field(max_length=40)]] = Field(default_factory=list,max_length=80)
    # 글에 나온 상장사 이름. 빠뜨리지 말고 company로 설명하게 한다.
    companies: list[Annotated[str,Field(max_length=40)]] = Field(default_factory=list,max_length=20)
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
    size: Literal["","초대형","대형","중견","중소","스타트업"]
class TermDetectResponse(Schema):
    terms: list[DetectedTerm]
