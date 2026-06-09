"""
routers/accounts.py - 계좌 관련 API 엔드포인트

제공하는 API:
    GET  /accounts          → 내 계좌 목록 조회
    GET  /accounts/{id}     → 특정 계좌 상세 조회
    POST /accounts          → 새 계좌 개설

모든 API는 로그인이 필요합니다 (Depends(get_current_user)).
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.account import Account
from app.models.user import User
from app.schemas.account import AccountCreateRequest, AccountResponse
from app.utils.deps import get_current_user

# prefix는 main.py에서 /api/trading 으로 지정
# 최종 경로 예시: /api/trading/accounts, /api/trading/accounts/1
router = APIRouter(tags=["거래"])


@router.get("", response_model=list[AccountResponse], summary="내 계좌 목록 조회")
def get_my_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    로그인한 유저의 모든 계좌를 반환합니다.
    
    회원가입 시 기본 계좌가 1개 자동 생성되므로 최소 1개 이상 반환됩니다.
    
    응답 예시:
        [
            {
                "account_id": 1,
                "account_name": "모의투자 기본계좌",
                "balance": 10000000.00,
                "withdrawable_cash": 9500000.00
            }
        ]
    """
    # current_user.user_id로 이 유저의 계좌만 필터링
    return db.query(Account).filter(Account.user_id == current_user.user_id).all()


@router.get("/{account_id}", response_model=AccountResponse, summary="계좌 상세 조회")
def get_account(
    account_id: int,        # URL 경로의 {account_id} 값이 자동으로 들어옴
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    특정 계좌의 상세 정보를 반환합니다.
    
    본인 계좌만 조회 가능합니다 (다른 사람 계좌 조회 불가).
    """
    account = db.query(Account).filter(
        Account.account_id == account_id,
        Account.user_id == current_user.user_id,  # 반드시 내 계좌여야 함
    ).first()

    if not account:
        # 없는 계좌이거나 다른 사람 계좌인 경우
        raise HTTPException(status_code=404, detail="계좌를 찾을 수 없습니다.")

    return account


@router.post("", response_model=AccountResponse, summary="추가 계좌 개설")
def create_account(
    req: AccountCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    새로운 모의투자 계좌를 개설합니다.
    
    요청 예시:
        POST /accounts
        {
            "account_name": "장기투자용",
            "initial_balance": 5000000
        }
    """
    account = Account(
        user_id=current_user.user_id,
        account_name=req.account_name,
        balance=req.initial_balance,
        withdrawable_cash=req.initial_balance,  # 처음엔 전액 현금
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return account
