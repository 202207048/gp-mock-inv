from datetime import datetime
from enum import Enum
from uuid import UUID
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)



TokenCount = Annotated[int, Field(strict=True, ge=0)]


class UsageMetrics(Schema):
    model_config = ConfigDict(extra="forbid", frozen=True)
    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_tokens: TokenCount | None = None


ProfileId = Annotated[str, StringConstraints(
    strict=True, strip_whitespace=False, pattern=r"^[a-z][a-z0-9_-]{0,31}$",
)]
ProfileRevision = Annotated[int, Field(strict=True, ge=1)]
ProfileTask = Literal["news_analysis", "stock_news_summary"]


class ModelProfileCatalogEntry(Schema):
    id: ProfileId
    revision: ProfileRevision
    display_name: str
    description: str
    category: str
    enabled: bool
    supported_tasks: tuple[ProfileTask, ...]


class AIExecutionMetadata(Schema):
    """One observed SDK generation attempt, including failed attempts."""
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
    trace_id: UUID
    operation_id: UUID
    provider: Literal["openai", "gemini"]
    model: str = Field(strict=True, min_length=1, max_length=200)
    profile_id: ProfileId | None = None
    profile_revision: ProfileRevision | None = None
    usage: UsageMetrics
    latency_ms: float = Field(ge=0, allow_inf_nan=False)


    @model_validator(mode="after")
    def profile_identity_is_complete(self):
        if (self.profile_id is None) != (self.profile_revision is None):
            raise ValueError("Profile ID and revision must be supplied together")
        return self


class ProviderSettingsResponse(Schema):
    provider: Literal["openai", "gemini"]
    model: str


class ProviderSwitchRequest(Schema):
    provider: str

class Article(Schema):
    article_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str | None = None
    url: str | None = None
    source: str | None = None
    published_at: datetime | None = None
    symbol_code: str | None = None


class SentimentLabel(str, Enum):
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class Sentiment(Schema):
    label: SentimentLabel
    confidence: float = Field(ge=0, le=1)


class ImportanceLabel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


def importance_label_for_score(score: float) -> ImportanceLabel:
    # Keep importance band boundaries in one place for every provider.
    if score < 0.35:
        return ImportanceLabel.LOW
    if score < 0.70:
        return ImportanceLabel.MEDIUM
    return ImportanceLabel.HIGH


class Importance(Schema):
    score: float = Field(ge=0, le=1)

    @computed_field
    @property
    def label(self) -> ImportanceLabel:
        return importance_label_for_score(self.score)


class ArticleAnalysis(Schema):
    article_id: str = Field(min_length=1)
    sentiment: Sentiment
    importance: Importance


class ArticlePrediction(Schema):
    article_id: str = Field(min_length=1)
    sentiment: Sentiment
    importance_score: float = Field(ge=0, le=1)


class NewsSentimentBatch(Schema):
    articles: list[ArticlePrediction]


class NewsAnalysisRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    articles: list[Article] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def article_ids_are_unique(self):
        ids = [article.article_id for article in self.articles]
        if len(ids) != len(set(ids)):
            raise ValueError("article_id values must be unique within a request")
        return self


class NewsAnalysisResponse(Schema):
    ai: AIExecutionMetadata | None = None
    articles: list[ArticleAnalysis]


# Stock-level synthesis of supplied headlines/excerpts; not per-article summaries.
SummaryText = Annotated[str, Field(strict=True, min_length=1, max_length=2000)]
SummaryTag = Annotated[str, Field(strict=True, min_length=1, max_length=100)]


class StockNewsArticle(Schema):
    title: str = Field(min_length=1, max_length=2000)
    summary: str = Field(default="", max_length=20000)
    source: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=200)


class StockNewsSummaryRequest(Schema):
    profile: ProfileId | None = None
    include_ai_metadata: bool = False
    symbol_code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    change_rate: float | None = Field(default=None, allow_inf_nan=False)
    articles: list[StockNewsArticle] = Field(min_length=1, max_length=5)


class StockNewsBrief(Schema):
    headline: str = Field(strict=True, min_length=1, max_length=1000)
    summary: list[SummaryText] = Field(min_length=1, max_length=3)
    tags: list[SummaryTag] = Field(max_length=3)
    disclaimer: str = Field(strict=True, min_length=1, max_length=1000)


class StockNewsSummaryResponse(StockNewsBrief):
    ai: AIExecutionMetadata | None = None
    # Internal metadata lets Backend cache by the provider actually used.
    provider: Literal["openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)
