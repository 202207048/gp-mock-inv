from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.utils.deps import get_current_user
from app.schemas.asset_coach import AssetCoachBody, AssetCoachEmpty, AssetCoachResponse
from app.services.asset_coach_service import service

router = APIRouter()


@router.post("/asset-coach", response_model=AssetCoachResponse | AssetCoachEmpty, summary="내 자산 AI 코치")
async def asset_coach(body: AssetCoachBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """본인 계좌의 매입·평가금액, 손익, 종목 비중을 계산해 점검 조언을 돌려준다. 매매 지시는 하지 않는다."""
    result = await service.get(db, body.account_id, user.user_id)
    return result if result is not None else AssetCoachEmpty()
