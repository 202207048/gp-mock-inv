from fastapi import APIRouter, Depends, HTTPException, Request
from .asset_coach_schemas import AssetCoachRequest, AssetCoachResponse
from .summarizers.asset_coach_openai import AssetCoachGenerator
from .summarizers.base import SummarizerError, SummarizerUnavailableError

router = APIRouter()


def get_generator():
    try:
        return AssetCoachGenerator()
    except SummarizerUnavailableError:
        raise HTTPException(503, "Asset coach generator unavailable") from None


@router.post("/brief/asset-coach", response_model=AssetCoachResponse)
def asset_coach(body: AssetCoachRequest, request: Request, generator=Depends(get_generator)):
    try:
        return generator.generate(body, request.state.trace_id)
    except SummarizerError:
        raise HTTPException(502, "Asset coach generation failed") from None
