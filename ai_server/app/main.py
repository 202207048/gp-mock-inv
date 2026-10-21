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
from .schemas import StockNewsSummaryRequest, StockNewsSummaryResponse
from .summarizers.base import (
    StockNewsSummarizer, SummarizerError, SummarizerUnavailableError,
)
from .summarizers.openai import OpenAIStockNewsSummarizer
from .summarizers.gemini import GeminiStockNewsSummarizer


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


def get_stock_news_summarizer() -> StockNewsSummarizer:
    # Capture provider/model together once for this request, including cache metadata.
    provider, model = app.state.provider_manager.get_settings()
    try:
        if provider == "openai":
            return OpenAIStockNewsSummarizer(model=model)
        return GeminiStockNewsSummarizer(model=model)
    except SummarizerUnavailableError as exc:
        raise HTTPException(503, detail="Stock-news summarizer unavailable") from exc
    except Exception as exc:
        raise HTTPException(502, detail="Stock-news summarizer initialization failed") from exc


@app.post("/summarize/stock-news", response_model=StockNewsSummaryResponse)
def summarize_stock_news(
    request: StockNewsSummaryRequest,
    summarizer: StockNewsSummarizer = Depends(get_stock_news_summarizer),
) -> StockNewsSummaryResponse:
    try:
        brief = summarizer.summarize(request)
        return StockNewsSummaryResponse(
            **brief.model_dump(), provider=summarizer.provider, model=summarizer.model,
        )
    except (SummarizerError, ValueError) as exc:
        raise HTTPException(502, detail="Stock-news summary failed") from exc
