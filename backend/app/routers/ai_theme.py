from fastapi import APIRouter, Depends, HTTPException
from app.utils.deps import get_current_user
from app.schemas.theme_brief import ThemePublicResponse
from app.services.theme_brief_service import service, ThemeUnavailable
router=APIRouter()
@router.get('/theme-brief',response_model=ThemePublicResponse)
async def theme_brief(user=Depends(get_current_user)):
    try:return await service.get()
    except ThemeUnavailable:raise HTTPException(503,'테마 시세를 잠시 사용할 수 없습니다.') from None
