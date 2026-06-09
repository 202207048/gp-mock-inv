"""
services/ranking_service.py - 랭킹 비즈니스 로직

전체 유저의 수익률을 계산해서 랭킹을 만듭니다.

수익률 계산 방법:
    시작 자산: 1,000만원 (모든 유저 동일)
    현재 총자산: 계좌의 balance 합계
    수익률(%) = (현재 총자산 - 시작 자산) / 시작 자산 × 100
    
    예: 현재 총자산 11,000,000원이면
        수익률 = (11,000,000 - 10,000,000) / 10,000,000 × 100 = 10%

SQLAlchemy 집계 함수 사용:
    func.sum() → SQL의 SUM() 함수와 동일
    GROUP BY → 유저별로 계좌 잔고를 합산
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.user import User

# 모든 유저의 초기 시작 자산 (회원가입 시 자동 지급)
START_BALANCE = 10_000_000  # 1천만원


def get_profit_ranking(db: Session, limit: int = 20) -> list[dict]:
    """
    전체 유저 수익률 랭킹을 계산해서 반환합니다.
    
    유저별로 모든 계좌의 잔고를 합산해서 수익률을 계산합니다.
    (계좌가 여러 개인 유저는 전체 합산)
    
    Args:
        db: DB 세션
        limit: 상위 몇 명까지 조회할지 (기본 20명)
    
    Returns:
        랭킹 목록:
        [
            {
                "rank": 1,
                "user_id": 5,
                "user_name": "투자고수",
                "total_balance": 12500000,   # 현재 총자산
                "profit_rate": 25.0          # 수익률 (%)
            },
            ...
        ]
    """
    # SQLAlchemy로 아래 SQL과 동일한 쿼리를 실행:
    # SELECT u.user_id, u.user_name, SUM(a.balance) as total_balance
    # FROM users u
    # JOIN account a ON a.user_id = u.user_id
    # GROUP BY u.user_id, u.user_name
    # ORDER BY total_balance DESC
    # LIMIT 20
    results = (
        db.query(
            User.user_id,
            User.user_name,
            func.sum(Account.balance).label("total_balance"),  # 계좌 잔고 합산
        )
        .join(Account, Account.user_id == User.user_id)  # users와 account 테이블 조인
        .group_by(User.user_id, User.user_name)          # 유저별로 그룹화
        .order_by(func.sum(Account.balance).desc())      # 잔고 높은 순 정렬
        .limit(limit)
        .all()
    )

    # 결과를 보기 좋은 형태로 변환
    ranking = []
    for rank, row in enumerate(results, start=1):  # rank는 1부터 시작
        total = float(row.total_balance)

        # 수익률 계산 (소수점 2자리 반올림)
        profit_rate = (total - START_BALANCE) / START_BALANCE * 100

        ranking.append({
            "rank": rank,                       # 순위
            "user_id": row.user_id,
            "user_name": row.user_name,
            "total_balance": total,             # 현재 총자산 (원)
            "profit_rate": round(profit_rate, 2),  # 수익률 (%)
        })

    return ranking
