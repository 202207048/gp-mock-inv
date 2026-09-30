"""
models/portfolio.py - 포트폴리오(보유 잔고) 테이블 모델

현재 보유 중인 종목과 수량을 저장합니다.
주문이 체결될 때마다 이 테이블이 업데이트됩니다.

예시:
    삼성전자 10주를 70,000원에 샀다면:
        avg_price = 70000
        hold_quantity = 10
    
    이후 삼성전자 5주를 72,000원에 더 샀다면:
        avg_price = (70000*10 + 72000*5) / 15 = 70667 (평균단가 재계산)
        hold_quantity = 15

수익률 계산:
    (현재가 - avg_price) / avg_price * 100 = 수익률(%)
"""

from sqlalchemy import Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class Portfolio(Base):
    """
    보유 잔고 테이블 모델.
    
    전량 매도하면 이 행(row)이 삭제됩니다.
    """

    __tablename__ = "portfolio"

    # 포트폴리오 항목 고유 번호
    portfolio_id = Column(Integer, primary_key=True, index=True)

    # 어떤 계좌의 포트폴리오인지
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=False)

    # 어떤 종목을 보유 중인지
    symbol_code = Column(String(20), ForeignKey("item_master.symbol_code"), nullable=False)

    # 평균 매수 단가 (수익률 계산에 사용)
    # 여러 번 나눠서 샀을 때 평균값으로 계산됨
    avg_price = Column(Numeric(20, 2), nullable=False)

    # 현재 보유 수량
    # 매수하면 증가, 매도하면 감소
    hold_quantity = Column(Integer, nullable=False, default=0)

    # 관계 설정
    account = relationship("Account", back_populates="portfolios")
    item_master = relationship("ItemMaster", back_populates="portfolios")
