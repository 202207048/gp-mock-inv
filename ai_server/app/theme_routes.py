from fastapi import APIRouter, Depends, HTTPException, Request
from .theme_schemas import ThemeBriefRequest, ThemeBriefResponse
from .summarizers.theme_openai import ThemeGenerator
from .summarizers.base import SummarizerError, SummarizerUnavailableError
router=APIRouter()
def get_generator():
    try:return ThemeGenerator()
    except SummarizerUnavailableError:raise HTTPException(503,"Theme generator unavailable") from None
@router.post("/brief/theme",response_model=ThemeBriefResponse)
def theme_brief(body:ThemeBriefRequest,request:Request,generator=Depends(get_generator)):
    try:return generator.generate(body,request.state.trace_id)
    except SummarizerError:raise HTTPException(502,"Theme generation failed") from None
