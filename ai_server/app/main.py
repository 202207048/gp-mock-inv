from fastapi import Depends, FastAPI, HTTPException

from .analyzers.base import AnalyzerError, AnalyzerUnavailableError, NewsAnalyzer
from .analyzers.gemini import GeminiNewsAnalyzer
from .analyzers.openai import OpenAINewsAnalyzer
from .provider_manager import ProviderKeyUnavailableError, RuntimeProviderManager
from .schemas import (
    NewsAnalysisRequest,
    NewsAnalysisResponse,
    ProviderSettingsResponse,
    ProviderSwitchRequest,
)
from .settings import get_ai_provider


app = FastAPI(title="News AI Analysis")
app.state.provider_manager = RuntimeProviderManager(get_ai_provider())


def get_analyzer() -> NewsAnalyzer:
    try:
        provider = app.state.provider_manager.get_provider()
        if provider == "openai":
            return OpenAINewsAnalyzer()
        return GeminiNewsAnalyzer()
    except AnalyzerUnavailableError as exc:
        raise HTTPException(status_code=503, detail="News analyzer unavailable") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="News analyzer initialization failed") from exc


@app.get("/settings/provider", response_model=ProviderSettingsResponse)
def get_provider_settings() -> ProviderSettingsResponse:
    provider, model = app.state.provider_manager.get_settings()
    return ProviderSettingsResponse(provider=provider, model=model)


@app.put("/settings/provider", response_model=ProviderSettingsResponse)
def update_provider_settings(request: ProviderSwitchRequest) -> ProviderSettingsResponse:
    manager = app.state.provider_manager
    try:
        provider = manager.normalize_provider(request.provider)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Unsupported provider") from exc

    try:
        model = manager.set_provider(provider)
    except ProviderKeyUnavailableError as exc:
        raise HTTPException(status_code=503, detail="Selected provider is not configured") from exc
    return ProviderSettingsResponse(provider=provider, model=model)


@app.post("/analyze/news", response_model=NewsAnalysisResponse)
def analyze_news(
    request: NewsAnalysisRequest,
    analyzer: NewsAnalyzer = Depends(get_analyzer),
) -> NewsAnalysisResponse:
    try:
        return NewsAnalysisResponse(articles=analyzer.analyze(request.articles))
    except AnalyzerError as exc:
        raise HTTPException(status_code=502, detail="News analysis failed") from exc
