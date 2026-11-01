from fastapi import APIRouter, Depends, HTTPException, Request
from .stock_brief_schemas import StockBriefRequest, StockBriefResponse
from .summarizers.stock_brief_openai import StockBriefGenerator
from .summarizers.base import SummarizerError, SummarizerUnavailableError
router=APIRouter()
def get_generator():
    try:return StockBriefGenerator()
    except SummarizerUnavailableError:raise HTTPException(503,"Stock generator unavailable") from None
@router.post("/brief/stock",response_model=StockBriefResponse)
def stock_brief(body:StockBriefRequest,request:Request,generator=Depends(get_generator)):
    try:return generator.generate(body,request.state.trace_id)
    except SummarizerError:raise HTTPException(502,"Stock generation failed") from None
