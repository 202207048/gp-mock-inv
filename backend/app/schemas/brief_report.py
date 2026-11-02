from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# 사실과 다름 / 다른 종목 내용 / 이해하기 어려움 / 기타
ReportReason = Literal["wrong_fact", "wrong_stock", "hard_to_read", "other"]


class BriefReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["portfolio", "stock"]
    symbol_code: str = Field(min_length=1, max_length=12)
    generated_at: AwareDatetime
    account_id: int | None = Field(default=None, gt=0)
    reason: ReportReason
    note: str | None = Field(default=None, max_length=200)


class BriefReportResponse(BaseModel):
    id: int
    created_at: AwareDatetime
