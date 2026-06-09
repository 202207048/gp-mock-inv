from pydantic import BaseModel


class SecurityResponse(BaseModel):
    symbol_code: str
    name: str
    market_type: str
    sector_code: str | None = None

    class Config:
        from_attributes = True


class PriceHistoryResponse(BaseModel):
    history_id: int
    symbol_code: str
    time_frame: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    timestamp: str

    class Config:
        from_attributes = True
