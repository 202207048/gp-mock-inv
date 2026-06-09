"""
models/price_history.py - 시세 히스토리(PriceHistory) 테이블 모델

주식 차트 데이터(캔들스틱)를 저장합니다.
한국투자증권 API에서 받아온 데이터를 저장하거나
API에서 바로 가져다 프론트에 전달합니다.

OHLCV 데이터란?
    Open  (시가): 해당 기간 시작 가격
    High  (고가): 해당 기간 최고 가격
    Low   (저가): 해당 기간 최저 가격
    Close (종가): 해당 기간 종료 가격
    Volume(거래량): 해당 기간 거래된 주식 수

time_frame 예시:
    "1분"  → 1분봉 (1분마다 하나의 캔들)
    "일봉" → 하루치 데이터
    "주봉" → 일주일치 데이터
    "월봉" → 한 달치 데이터
"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class PriceHistory(Base):
    """시세 히스토리 테이블 모델."""

    __tablename__ = "price_history"

    # 기록 고유 번호
    history_id = Column(Integer, primary_key=True, index=True)

    # 어떤 종목의 데이터인지
    symbol_code = Column(String(20), ForeignKey("item_master.symbol_code"), nullable=False)

    # 데이터 주기 (1분, 일봉, 주봉, 월봉 등)
    time_frame = Column(String(10), nullable=False)

    # 시가 (해당 기간 첫 거래 가격)
    open = Column(Numeric(20, 2), nullable=False)

    # 고가 (해당 기간 가장 높은 가격)
    high = Column(Numeric(20, 2), nullable=False)

    # 저가 (해당 기간 가장 낮은 가격)
    low = Column(Numeric(20, 2), nullable=False)

    # 종가 (해당 기간 마지막 거래 가격, 현재가)
    close = Column(Numeric(20, 2), nullable=False)

    # 거래량 (해당 기간 동안 거래된 총 주식 수)
    volume = Column(BigInteger, nullable=False, default=0)

    # 이 데이터가 어느 시점의 데이터인지
    # 예: 2024-01-15 09:30:00 → 1월 15일 9시 30분 봉 데이터
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    # 관계 설정
    item_master = relationship("ItemMaster", back_populates="price_histories")
