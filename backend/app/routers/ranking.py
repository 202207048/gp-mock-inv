"""
routers/ranking.py - 랭킹 API 엔드포인트

전체 유저의 모의투자 수익률 랭킹을 제공합니다.

제공하는 API:
    GET /ranking → 수익률 랭킹 조회
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.services.ranking_service import get_profit_ranking
from app.utils.deps import get_current_user

# prefix는 main.py에서 /api/ranking 으로 지정
# 최종 경로 예시: /api/ranking
router = APIRouter(tags=["랭킹"])


@router.get("", summary="수익률 랭킹 조회")
def get_ranking(
    # ge=1: 1 이상, le=100: 100 이하 (유효성 자동 검사)
    limit: int = Query(20, ge=1, le=100, description="상위 몇 명까지 조회"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """
    전체 유저 수익률 랭킹을 반환합니다.
    
    수익률 계산: (현재 총자산 - 시작자산 1천만원) / 1천만원 × 100
    
    사용 예시:
        GET /ranking        → 상위 20명
        GET /ranking?limit=10 → 상위 10명
    
    응답 예시:
        [
            {
                "rank": 1,
                "user_id": 5,
                "user_name": "투자고수",
                "total_balance": 12500000.0,
                "profit_rate": 25.0
            },
            {
                "rank": 2,
                "user_name": "주식왕",
                "total_balance": 11000000.0,
                "profit_rate": 10.0
            },
            ...
        ]
    """
    return get_profit_ranking(db, limit)
