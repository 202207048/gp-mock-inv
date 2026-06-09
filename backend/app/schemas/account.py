from decimal import Decimal

from pydantic import BaseModel


class AccountResponse(BaseModel):
    account_id: int
    account_name: str
    balance: Decimal
    withdrawable_cash: Decimal

    class Config:
        from_attributes = True


class AccountCreateRequest(BaseModel):
    account_name: str = "기본계좌"
    initial_balance: Decimal = Decimal("10000000")  # 기본 시작 자산 1천만원
