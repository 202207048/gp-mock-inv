from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .execution import normalize_trace_id
from .service_auth import authenticate_service

from .analyzers.base import AnalyzerError, AnalyzerUnavailableError, NewsAnalyzer
from .analyzers.gemini import GeminiNewsAnalyzer
from .analyzers.openai import OpenAINewsAnalyzer
from .model_profiles import (
    ModelProfileRegistry, ProfileUnavailableError, ProfileValidationError,
    ResolvedProfile, create_profile_adapter, profile_metadata,
)
from .schemas import ModelProfileCatalogEntry, ProfileTask
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
from .schemas import PortfolioBriefRequest, PortfolioBriefResponse
from .summarizers.portfolio import PortfolioBriefGenerator
from .summarizers.portfolio_openai import OpenAIPortfolioBriefGenerator
from .summarizers.portfolio_gemini import GeminiPortfolioBriefGenerator


app = FastAPI(title="News AI Analysis")
app.state.provider_manager = RuntimeProviderManager(get_ai_provider())
app.state.model_profiles = ModelProfileRegistry()


@app.middleware("http")
async def trace_request(request: Request, call_next):
    request.state.trace_id = normalize_trace_id(request.headers.get("X-Trace-ID"))
    response = authenticate_service(request)
    if response is None:
        response = await call_next(request)
    response.headers["X-Trace-ID"] = str(request.state.trace_id)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _execution_failure(
    detail: str, exc: AnalyzerError | SummarizerError, include_metadata: bool,
    context: ResolvedProfile | None = None,
) -> JSONResponse:
    body = {"detail": detail}
    if include_metadata and exc.metadata is not None:
        try:
            metadata = profile_metadata(exc.metadata, context)
            body["ai"] = metadata.model_dump(
                mode="json", exclude={"profile_id", "profile_revision"} if context is None else None,
            )
        except ValueError:
            pass  # Never attach a profile to inconsistent execution identity.
    return JSONResponse(status_code=502, content=body)


def _resolve_profile(profile_id: str | None, task: ProfileTask) -> ResolvedProfile | None:
    if profile_id is None:
        return None
    try:
        return app.state.model_profiles.resolve(profile_id, task)
    except ProfileValidationError as exc:
        raise HTTPException(422, detail="Invalid model profile or unsupported task") from exc
    except ProfileUnavailableError as exc:
        raise HTTPException(503, detail="Model profile unavailable") from exc


def resolve_analysis_profile(request: NewsAnalysisRequest) -> ResolvedProfile | None:
    return _resolve_profile(request.profile, "news_analysis")


def resolve_summary_profile(request: StockNewsSummaryRequest) -> ResolvedProfile | None:
    return _resolve_profile(request.profile, "stock_news_summary")


@app.get("/profiles", response_model=list[ModelProfileCatalogEntry])
def get_profiles() -> list[ModelProfileCatalogEntry]:
    return app.state.model_profiles.catalog()


def get_analyzer(
    context: Annotated[ResolvedProfile | None, Depends(resolve_analysis_profile)] = None,
) -> NewsAnalyzer:
    try:
        if context is not None:
            return create_profile_adapter(context)
        provider = app.state.provider_manager.get_provider()
        if provider == "openai":
            return OpenAINewsAnalyzer()
        return GeminiNewsAnalyzer()
    except (AnalyzerUnavailableError, ProfileUnavailableError) as exc:
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


@app.post("/analyze/news", response_model=NewsAnalysisResponse, response_model_exclude_unset=True)
def analyze_news(
    request: NewsAnalysisRequest,
    http_request: Request,
    analyzer: NewsAnalyzer = Depends(get_analyzer),
    context: ResolvedProfile | None = Depends(resolve_analysis_profile),
) -> NewsAnalysisResponse | JSONResponse:
    try:
        measured = getattr(analyzer, "analyze_with_metadata", None)
        if measured is not None:
            outcome = measured(request.articles, trace_id=http_request.state.trace_id)
            return NewsAnalysisResponse(
                articles=outcome.result,
                **({"ai": profile_metadata(outcome.metadata, context)} if request.include_ai_metadata else {}),
            )
        return NewsAnalysisResponse(articles=analyzer.analyze(request.articles))
    except AnalyzerError as exc:
        return _execution_failure("News analysis failed", exc, request.include_ai_metadata, context)
    except ValueError as exc:
        raise HTTPException(502, detail="News analysis failed") from exc


def get_stock_news_summarizer(
    context: Annotated[ResolvedProfile | None, Depends(resolve_summary_profile)] = None,
) -> StockNewsSummarizer:
    try:
        if context is not None:
            return create_profile_adapter(context)
        # Capture the legacy provider/model once without mutating the manager.
        provider, model = app.state.provider_manager.get_settings()
        if provider == "openai":
            return OpenAIStockNewsSummarizer(model=model)
        return GeminiStockNewsSummarizer(model=model)
    except (SummarizerUnavailableError, ProfileUnavailableError) as exc:
        raise HTTPException(503, detail="Stock-news summarizer unavailable") from exc
    except Exception as exc:
        raise HTTPException(502, detail="Stock-news summarizer initialization failed") from exc


@app.post("/summarize/stock-news", response_model=StockNewsSummaryResponse, response_model_exclude_unset=True)
def summarize_stock_news(
    request: StockNewsSummaryRequest,
    http_request: Request,
    summarizer: StockNewsSummarizer = Depends(get_stock_news_summarizer),
    context: ResolvedProfile | None = Depends(resolve_summary_profile),
) -> StockNewsSummaryResponse | JSONResponse:
    try:
        measured = getattr(summarizer, "summarize_with_metadata", None)
        if measured is not None:
            outcome = measured(request, trace_id=http_request.state.trace_id)
            return StockNewsSummaryResponse(
                **outcome.result.model_dump(), provider=summarizer.provider,
                model=summarizer.model, **({"ai": profile_metadata(outcome.metadata, context)} if request.include_ai_metadata else {}),
            )
        brief = summarizer.summarize(request)
        return StockNewsSummaryResponse(
            **brief.model_dump(), provider=summarizer.provider, model=summarizer.model,
        )
    except SummarizerError as exc:
        return _execution_failure("Stock-news summary failed", exc, request.include_ai_metadata, context)
    except ValueError as exc:
        raise HTTPException(502, detail="Stock-news summary failed") from exc


def resolve_portfolio_profile(request: PortfolioBriefRequest) -> ResolvedProfile | None:
    return _resolve_profile(request.profile, "portfolio_brief")


def get_portfolio_brief_generator(
    context: Annotated[ResolvedProfile | None, Depends(resolve_portfolio_profile)] = None,
) -> PortfolioBriefGenerator:
    try:
        if context is not None:
            return create_profile_adapter(context)
        provider, model = app.state.provider_manager.get_settings()
        if provider == "openai":
            return OpenAIPortfolioBriefGenerator(model=model)
        return GeminiPortfolioBriefGenerator(model=model)
    except (SummarizerUnavailableError, ProfileUnavailableError) as exc:
        raise HTTPException(503, detail="Portfolio brief generator unavailable") from exc
    except Exception as exc:
        raise HTTPException(502, detail="Portfolio brief generator initialization failed") from exc


@app.post("/brief/portfolio", response_model=PortfolioBriefResponse, response_model_exclude_unset=True)
def generate_portfolio_brief(
    request: PortfolioBriefRequest,
    http_request: Request,
    generator: PortfolioBriefGenerator = Depends(get_portfolio_brief_generator),
    context: ResolvedProfile | None = Depends(resolve_portfolio_profile),
) -> PortfolioBriefResponse | JSONResponse:
    try:
        outcome = generator.generate_with_metadata(request, trace_id=http_request.state.trace_id)
        metadata = profile_metadata(outcome.metadata, context)
        return PortfolioBriefResponse(
            **outcome.result.model_dump(), provider=metadata.provider, model=metadata.model,
            **({"ai": metadata} if request.include_ai_metadata else {}),
        )
    except SummarizerError as exc:
        return _execution_failure("Portfolio brief failed", exc, request.include_ai_metadata, context)
    except ValueError as exc:
        raise HTTPException(502, detail="Portfolio brief failed") from exc
