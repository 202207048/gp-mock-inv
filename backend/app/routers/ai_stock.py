import re
from fastapi import APIRouter, Depends, HTTPException
from app.utils.deps import get_current_user
from app.schemas.stock_brief import StockPublicResponse
from app.services.stock_brief_service import service, StockUnavailable
router=APIRouter()

@router.get('/stock-brief/{code}',response_model=StockPublicResponse)
async def stock_brief(code:str,user=Depends(get_current_user)):
    if not re.fullmatch(r'\d{6}',code):raise HTTPException(400,'Invalid stock code')
    try:return await service.get(code)
    except StockUnavailable:raise HTTPException(503,'Stock briefing unavailable') from None
