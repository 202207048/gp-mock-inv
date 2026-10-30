"""뉴스 분석 공개 요청과 결과 계약."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AnalysisArticle(Schema):
    article_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=2000)
    content: str | None = Field(default=None, max_length=20000)
    url: str | None = Field(default=None, max_length=4000)
    source: str | None = Field(default=None, max_length=200)


class NewsAnalysisRequest(Schema):
    articles: list[AnalysisArticle] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [a.article_id for a in self.articles]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate article IDs")
        return self


class Sentiment(Schema):
    label: Literal["POSITIVE", "NEUTRAL", "NEGATIVE"]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


class Importance(Schema):
    label: Literal["LOW", "MEDIUM", "HIGH"]
    score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def consistent_label(self):
        expected = "LOW" if self.score < .35 else "MEDIUM" if self.score < .70 else "HIGH"
        if self.label != expected:
            raise ValueError("Inconsistent importance label")
        return self


class ArticleAnalysis(Schema):
    article_id: str
    sentiment: Sentiment
    importance: Importance


class NewsAnalysisResponse(Schema):
    articles: list[ArticleAnalysis]
