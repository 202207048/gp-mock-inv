"""
routers/portfolio.py - 포트폴리오(보유 종목) API 엔드포인트

제공하는 API:
    GET /portfolio → 보유 종목 목록 + 계좌 잔고 조회

보유 종목마다 평균단가가 저장되어 있어서
현재가를 알면 수익률을 계산할 수 있습니다.

수익률 계산 (프론트엔드에서 처리 가능):
    수익률 = (현재가 - 평균단가) / 평균단가 × 100
    수익금 = (현재가 - 평균단가) × 보유수량
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.account import Account
from app.models.portfolio import Portfolio
from app.models.user import User
from app.utils.deps import get_current_user

# prefix는 main.py에서 /api/trading 으로 지정
# 최종 경로 예시: /api/trading/portfolio?account_id=1
router = APIRouter(tags=["거래"])


@router.get("", summary="보유 종목(포트폴리오) 조회")
def get_portfolio(
    account_id: int = Query(..., description="계좌 번호"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    특정 계좌의 보유 종목 목록과 계좌 잔고를 반환합니다.
    
    사용 예시:
        GET /portfolio?account_id=1
    
    응답 예시:
        {
            "account_id": 1,
            "balance": 10500000.0,           # 총 자산 (현금 + 주식 평가금액)
            "withdrawable_cash": 2500000.0,  # 매수 가능 현금
            "holdings": [
                {
                    "portfolio_id": 1,
                    "symbol_code": "005930",
                    "security_name": "삼성전자",
                    "avg_price": 70000.0,    # 평균 매수 단가
                    "hold_quantity": 10,     # 보유 수량
                    "total_value": 700000.0  # 평가금액 (평균단가 × 수량)
                }
            ]
        }
    
    현재가 기반 실시간 수익률은 프론트엔드에서
    /securities/{code}/price API를 호출해서 계산하도록 합니다.
    """
    # 내 계좌인지 확인
    account = db.query(Account).filter(
        Account.account_id == account_id,
        Account.user_id == current_user.user_id,
    ).first()

    if not account:
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")

    # 보유 종목 목록 조회
    items = db.query(Portfolio).filter(Portfolio.account_id == account_id).all()

    # 응답 데이터 구성
    result = []
    for item in items:
        result.append({
            "portfolio_id": item.portfolio_id,
            "symbol_code": item.symbol_code,
            # item.item_master: Portfolio 모델의 relationship으로 ItemMaster 객체 바로 접근 가능
            "security_name": item.item_master.name if item.item_master else "",
            "avg_price": float(item.avg_price),
            "hold_quantity": item.hold_quantity,
            # 평가금액 = 평균단가 × 보유수량 (실제 현재가 기준은 프론트에서 계산)
            "total_value": float(item.avg_price) * item.hold_quantity,
        })

    return {
        "account_id": account_id,
        "balance": float(account.balance),                   # 총 자산
        "withdrawable_cash": float(account.withdrawable_cash),  # 매수 가능 현금
        "holdings": result,                                  # 보유 종목 목록
    }
