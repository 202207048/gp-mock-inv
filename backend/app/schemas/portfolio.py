from decimal import Decimal

from pydantic import BaseModel, Field


class PortfolioItemResponse(BaseModel):
    """
    보유 종목(포트폴리오) 응답 스키마.

    응답 예시:
        {
            "portfolio_id": 1,
            "symbol_code": "005930",
            "security_name": "삼성전자",
            "avg_price": 74000.00,
            "hold_quantity": 10,
            "current_price": 75000.00,
            "profit_loss": 10000.00,
            "profit_loss_rate": 1.35
        }
    """
    portfolio_id: int
    symbol_code: str
    security_name: str
    avg_price: Decimal = Field(..., description="평균 매수 단가")
    hold_quantity: int = Field(..., description="현재 보유 수량")
    current_price: Decimal | None = Field(None, description="현재가 (실시간 조회 시 포함)")
    profit_loss: Decimal | None = Field(None, description="평가손익 = (현재가 - 평균단가) × 수량")
    profit_loss_rate: float | None = Field(None, description="수익률 (%)")

    class Config:
        from_attributes = True
