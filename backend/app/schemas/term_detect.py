"""화면 글에서 어려운 말을 찾는 요청과 결과 계약."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TermDetectRequest(Schema):
    text: str = Field(min_length=1, max_length=3000)
    # 화면의 용어사전이 이미 찾은 말. AI는 이것을 빼고 찾는다.
    known: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=80)
    # 검색창에서 낱말 하나를 직접 물어볼 때. 글에서 찾지 않고 그 말만 풀이한다.
    ask: bool = False


class Result(BaseModel):
    # AI 서버가 새 필드를 먼저 보내도 깨지지 않게, 모르는 필드는 버린다.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class DetectedTerm(Result):
    term: str
    tier: Literal["invest", "company", "general"]
    meaning: str
    # 대부분 아는 쉬운 말. 화면에서 흐리게 보여 준다.
    basic: bool = False
    # 기업 정보. knowledge는 AI가 아는 회사, context는 글 내용만으로 설명한 회사,
    # data는 AI가 빠뜨린 상장사를 종목 데이터로 채운 것.
    basis: Literal["knowledge", "context", "data"] = "knowledge"
    listing: str = ""
    index: str = ""
    sector: str = ""
    size: str = ""
    # 소속 기업집단과 모회사. AI가 확실할 때만 채운다.
    group: str = ""
    parent: str = ""
    # 상장 시장이 우리 종목 데이터로 확인됐는지.
    listing_checked: bool = False
    # 기업 이름이 국내 상장 종목과 정확히 같을 때만 채운다.
    symbol_code: str | None = None


class TermDetectResponse(Result):
    terms: list[DetectedTerm]
