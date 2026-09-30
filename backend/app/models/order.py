"""
models/order.py - 주문(Orders) 테이블 모델

매수/매도 주문 내역을 저장합니다.
모의투자에서는 주문 즉시 체결되는 방식을 사용합니다.

주문 흐름:
    1. 사용자가 "삼성전자 10주 매수" 요청
    2. Orders 테이블에 status="대기" 로 저장
    3. 잔고 확인 후 체결 처리
    4. status="체결" 로 업데이트
    5. Portfolio(보유잔고) 업데이트
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class Order(Base):
    """
    주문 테이블 모델.
    
    모든 매수/매도 기록이 여기에 남습니다.
    이것이 거래 내역 조회의 기반 데이터가 됩니다.
    """

    __tablename__ = "orders"

    # 주문 고유 번호 (자동 증가)
    order_id = Column(Integer, primary_key=True, index=True)

    # 어떤 계좌에서 주문했는지 (account 테이블 참조)
    account_id = Column(Integer, ForeignKey("account.account_id"), nullable=False)

    # 어떤 종목을 주문했는지 (item_master 테이블 참조)
    # 예: "005930" (삼성전자)
    symbol_code = Column(String(20), ForeignKey("item_master.symbol_code"), nullable=False)

    # 주문 종류: "매수" (사기), "매도" (팔기), "정정" (수정), "취소"
    order_type = Column(String(10), nullable=False)

    # 주문 단가 (1주당 가격)
    # 총 금액 = price * quantity
    price = Column(Numeric(20, 2), nullable=False)

    # 주문 수량 (몇 주 주문했는지)
    quantity = Column(Integer, nullable=False)

    # 주문 상태: "대기" → "체결" (또는 "취소", "거부")
    # 모의투자에서는 즉시 "체결"로 바뀜
    status = Column(String(10), nullable=False, default="대기")

    # 주문 시간 (자동으로 현재 시간 저장)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 관계 설정
    account = relationship("Account", back_populates="orders")
    item_master = relationship("ItemMaster", back_populates="orders")
