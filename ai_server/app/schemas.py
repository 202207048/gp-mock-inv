from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)



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
    articles: list[Article] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def article_ids_are_unique(self):
        ids = [article.article_id for article in self.articles]
        if len(ids) != len(set(ids)):
            raise ValueError("article_id values must be unique within a request")
        return self


class NewsAnalysisResponse(Schema):
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
    # Internal metadata lets Backend cache by the provider actually used.
    provider: Literal["openai", "gemini"]
    model: str = Field(min_length=1, max_length=200)
