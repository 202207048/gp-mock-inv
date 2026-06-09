"""
services/order_service.py - 주문(매수/매도) 비즈니스 로직

모의투자의 핵심 기능인 매수/매도 처리를 담당합니다.

실제 증권사 vs 모의투자 차이:
    실제: 주문 넣으면 → 시장에서 매칭 → 체결 (시간이 걸림)
    모의: 주문 넣으면 → 즉시 체결 (시장가 방식)

매수 처리 흐름:
    1. 계좌 확인 (내 계좌인지)
    2. 종목 확인 (존재하는 종목인지)
    3. 잔고 확인 (돈이 충분한지)
    4. 잔고 차감 (현금 감소)
    5. 포트폴리오 업데이트 (보유 수량, 평균단가 재계산)
    6. 주문 기록 저장

매도 처리 흐름:
    1. 계좌 확인
    2. 보유 수량 확인 (팔 주식이 있는지)
    3. 포트폴리오 수량 감소
    4. 잔고 증가 (현금 증가)
    5. 주문 기록 저장
"""

from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.order import Order
from app.models.portfolio import Portfolio
from app.models.security import ItemMaster
from app.schemas.order import OrderRequest


def create_order(db: Session, req: OrderRequest, user_id: int) -> Order:
    """
    주문 생성 및 즉시 체결 처리.
    
    Args:
        db: DB 세션
        req: 주문 요청 데이터 (account_id, symbol_code, order_type, price, quantity)
        user_id: 현재 로그인한 유저의 ID (JWT 토큰에서 추출)
    
    Returns:
        생성된 Order 객체 (체결 완료 상태)
    """
    # 계좌 확인: 해당 계좌가 존재하고, 요청한 유저의 계좌인지 확인
    account = db.query(Account).filter(
        Account.account_id == req.account_id,
        Account.user_id == user_id,
    ).first()
    if not account:
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")

    # 종목 확인: 존재하는 종목인지 확인
    security = db.query(ItemMaster).filter(ItemMaster.symbol_code == req.symbol_code).first()
    if not security:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")

    # 총 거래 금액 계산 (단가 × 수량)
    total_amount = req.price * req.quantity

    if req.order_type == "매수":
        _process_buy(db, account, req, total_amount)
    elif req.order_type == "매도":
        _process_sell(db, account, req, total_amount)
    else:
        raise HTTPException(status_code=400, detail="order_type은 매수 또는 매도여야 합니다.")

    # 주문 기록 저장 (체결 상태로)
    order = Order(
        account_id=req.account_id,
        symbol_code=req.symbol_code,
        order_type=req.order_type,
        price=req.price,
        quantity=req.quantity,
        status="체결",  # 모의투자: 즉시 체결
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _process_buy(db: Session, account: Account, req: OrderRequest, total_amount: Decimal):
    """
    매수 처리 내부 함수.
    
    1. 잔고 부족 확인
    2. 현금 차감
    3. 포트폴리오 업데이트 (평균단가 재계산)
    
    함수명 앞에 _ 를 붙이면 "이 파일 내부에서만 사용하는 함수"라는 관례입니다.
    """
    # 잔고 확인 (매수 가능 현금이 충분한지)
    if account.withdrawable_cash < total_amount:
        raise HTTPException(status_code=400, detail="매수 가능 현금이 부족합니다.")

    # 현금 차감 (매수 금액만큼)
    account.withdrawable_cash -= total_amount
    account.balance -= total_amount

    # 포트폴리오(보유종목)에 이미 해당 종목이 있는지 확인
    portfolio = db.query(Portfolio).filter(
        Portfolio.account_id == req.account_id,
        Portfolio.symbol_code == req.symbol_code,
    ).first()

    if portfolio:
        # 이미 보유 중인 종목을 추가로 매수하는 경우
        # 평균단가 재계산: (기존 평균단가 × 기존 수량 + 새 가격 × 새 수량) / 전체 수량
        # 예: 10주를 7만원에 샀고, 5주를 8만원에 추가로 사면
        #     평균단가 = (70000×10 + 80000×5) / 15 = 73333원 (소수점 이하 버림)
        total_qty = portfolio.hold_quantity + req.quantity
        portfolio.avg_price = (
            (portfolio.avg_price * portfolio.hold_quantity + req.price * req.quantity) / total_qty
        )
        portfolio.hold_quantity = total_qty
    else:
        # 처음 매수하는 종목인 경우 → 새 포트폴리오 항목 생성
        portfolio = Portfolio(
            account_id=req.account_id,
            symbol_code=req.symbol_code,
            avg_price=req.price,        # 첫 매수가 = 평균단가
            hold_quantity=req.quantity,
        )
        db.add(portfolio)


def _process_sell(db: Session, account: Account, req: OrderRequest, total_amount: Decimal):
    """
    매도 처리 내부 함수.
    
    1. 보유 수량 확인
    2. 포트폴리오 수량 감소
    3. 현금 증가
    4. 전량 매도 시 포트폴리오 항목 삭제
    """
    # 보유 종목 조회
    portfolio = db.query(Portfolio).filter(
        Portfolio.account_id == req.account_id,
        Portfolio.symbol_code == req.symbol_code,
    ).first()

    # 보유하지 않은 종목이거나 보유 수량보다 많이 팔려는 경우
    if not portfolio or portfolio.hold_quantity < req.quantity:
        raise HTTPException(status_code=400, detail="보유 수량이 부족합니다.")

    # 보유 수량 감소
    portfolio.hold_quantity -= req.quantity

    # 현금 증가 (매도 금액만큼)
    account.withdrawable_cash += total_amount
    account.balance += total_amount

    # 전량 매도한 경우 포트폴리오 항목 삭제
    if portfolio.hold_quantity == 0:
        db.delete(portfolio)
