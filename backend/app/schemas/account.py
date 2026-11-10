from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class AccountResponse(BaseModel):
    account_id: int
    account_number: str
    account_name: str
    balance: Decimal
    withdrawable_cash: Decimal

    class Config:
        from_attributes = True


class AccountCreateRequest(BaseModel):
    account_name: str = Field(default="기본계좌", min_length=1, max_length=100)
    initial_balance: Decimal = Field(default=Decimal("10000000"), ge=100000, le=100000000, decimal_places=0)

    @field_validator('account_name', mode='before')
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value
