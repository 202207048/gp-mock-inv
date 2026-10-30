"""인증된 AI 중계와 기사 내용 기준 프로세스 캐시."""
from collections import OrderedDict
import hashlib
import json
import time
import unicodedata
import httpx
from app.config import settings
from app.schemas.news_analysis import ArticleAnalysis, NewsAnalysisRequest, NewsAnalysisResponse
from app.services.ai_service_auth import AIServiceNotConfigured, service_headers

CACHE_TTL = 1800
CACHE_MAX_SIZE = 1024
_cache = OrderedDict()


class NewsAnalysisUnavailable(Exception):
    def __init__(self, status_code=502):
        self.status_code = status_code
        super().__init__("뉴스 AI 분석을 잠시 사용할 수 없습니다.")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def article_key(article):
    data = [normalized(article.title), normalized(article.content), normalized(article.url)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode("utf-8")).hexdigest()


async def analyze_news(request: NewsAnalysisRequest) -> NewsAnalysisResponse:
    found, missing = {}, {}
    now = time.monotonic()
    for article in request.articles:
        key = article_key(article)
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL:
            found[key] = cached[1]
            _cache.move_to_end(key)
        else:
            _cache.pop(key, None)
            missing.setdefault(key, article)
    if missing:
        try:
            headers = service_headers()
        except AIServiceNotConfigured:
            raise NewsAnalysisUnavailable(503) from None
        try:
            base = httpx.URL(settings.AI_SERVER_URL.strip())
            if base.scheme not in ("http", "https") or not base.host:
                raise NewsAnalysisUnavailable(503)
        except (httpx.InvalidURL, AttributeError):
            raise NewsAnalysisUnavailable(503) from None
        ids = {str(i): key for i, key in enumerate(missing)}
        articles = []
        for identity, key in ids.items():
            article = missing[key]
            articles.append({**article.model_dump(), "article_id":identity,
                             "title":normalized(article.title), "content":normalized(article.content),
                             "url":normalized(article.url)})
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(str(base).rstrip("/") + "/analyze/news", headers=headers,
                    json={"articles":articles, "profile":"luna", "include_ai_metadata":False})
                response.raise_for_status()
                data = response.json()
            results = data["articles"]
            if not isinstance(results, list) or len(results) != len(ids):
                raise ValueError("Incomplete analysis")
            validated = {}
            for item in results:
                result = ArticleAnalysis.model_validate({name:item[name] for name in ("article_id", "sentiment", "importance")})
                if result.article_id not in ids or result.article_id in validated:
                    raise ValueError("Analysis identity mismatch")
                validated[result.article_id] = result.model_dump(exclude={"article_id"})
        except httpx.HTTPStatusError as exc:
            raise NewsAnalysisUnavailable(503 if exc.response.status_code == 503 else 502) from None
        except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.ConnectError):
            raise NewsAnalysisUnavailable(503) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise NewsAnalysisUnavailable() from None
        for identity, body in validated.items():
            key = ids[identity]
            found[key] = body
            _cache[key] = (time.monotonic(), body)
            _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_SIZE:
            _cache.popitem(last=False)
    return NewsAnalysisResponse(articles=[ArticleAnalysis(article_id=a.article_id, **found[article_key(a)])
                                          for a in request.articles])
