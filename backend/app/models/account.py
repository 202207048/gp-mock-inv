"""
models/account.py - 계좌(Account) 테이블 모델

모의투자 계좌 정보를 저장합니다.
한 유저가 여러 계좌를 가질 수 있습니다 (1:N 관계).

회원가입 시 기본 계좌가 자동으로 1개 생성됩니다 (시작 자산 1천만원).
"""

from sqlalchemy import Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base


class Account(Base):
    """
    계좌 테이블 모델.
    
    모의투자에서 "계좌"는 실제 증권사 계좌처럼
    잔고, 보유 종목, 거래 내역을 관리하는 단위입니다.
    """

    __tablename__ = "account"

    # 계좌 고유 번호 (자동 증가)
    account_id = Column(Integer, primary_key=True, index=True)

    # 계좌 소유자의 user_id (users 테이블과 연결)
    # ForeignKey: 반드시 users 테이블에 존재하는 user_id만 올 수 있음
    # 없는 유저의 계좌를 만들 수 없도록 DB가 자동으로 막아줌
    user_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)

    # 계좌 별칭 (사용자가 직접 이름을 붙일 수 있음)
    # 예: "주식 계좌", "장기투자용" 등
    account_name = Column(String(100), nullable=False, default="기본계좌")

    # 총 자산 (현금 + 보유 주식 평가금액)
    # Numeric(20, 2): 최대 20자리, 소수점 2자리 (돈 계산에 정확한 Numeric 사용)
    balance = Column(Numeric(20, 2), nullable=False, default=0)

    # 매수 가능 현금 (주식을 사는 데 쓸 수 있는 현금)
    # balance와 다른 점: 주식을 사면 withdrawable_cash만 줄고,
    # 주식 가격이 오르면 balance는 올라가도 withdrawable_cash는 그대로
    withdrawable_cash = Column(Numeric(20, 2), nullable=False, default=0)

    # 관계 설정
    # 이 계좌의 소유자 (User 객체를 바로 가져올 수 있음)
    user = relationship("User", back_populates="accounts")

    # 이 계좌의 주문 목록
    orders = relationship("Order", back_populates="account")

    # 이 계좌의 보유 종목 목록
    portfolios = relationship("Portfolio", back_populates="account")
