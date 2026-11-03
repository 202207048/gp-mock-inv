from fastapi import APIRouter, Depends, HTTPException, Request
from .term_schemas import TermDetectRequest, TermDetectResponse
from .summarizers.terms_openai import TermDetector
from .summarizers.base import SummarizerError, SummarizerUnavailableError
router=APIRouter()
def get_detector():
    try:return TermDetector()
    except SummarizerUnavailableError:raise HTTPException(503,"Term detector unavailable") from None
@router.post("/terms/detect",response_model=TermDetectResponse)
def detect_terms(body:TermDetectRequest,request:Request,detector=Depends(get_detector)):
    try:return detector.detect(body,request.state.trace_id)
    except SummarizerError:raise HTTPException(502,"Term detection failed") from None
