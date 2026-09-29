"""
services/news_service.py - 뉴스 크롤링 서비스

네이버 금융에서 주식 관련 뉴스를 크롤링합니다.

크롤링(Crawling)이란?
    웹사이트의 HTML을 가져와서 원하는 정보를 추출하는 기술입니다.
    BeautifulSoup 라이브러리로 HTML을 파싱(분석)합니다.

주의사항:
    - 너무 자주 크롤링하면 해당 사이트에서 접근을 차단할 수 있습니다
    - 실제 서비스에서는 일정 간격으로 크롤링하고 DB에 저장하는 방식 권장
    - 네이버 금융 HTML 구조가 변경되면 크롤링 코드도 수정 필요

async/await 사용 이유:
    네트워크 요청(웹사이트 접속)은 시간이 걸리는 작업입니다.
    async/await로 처리하면 요청을 기다리는 동안 다른 요청도 처리 가능합니다.
"""

import httpx
from bs4 import BeautifulSoup


async def get_news_by_symbol(symbol_code: str, limit: int = 10) -> list[dict]:
    """
    특정 종목의 뉴스를 네이버 금융에서 크롤링합니다.
    
    Args:
        symbol_code: 종목 코드 (예: "005930" = 삼성전자)
        limit: 가져올 뉴스 개수 (기본 10개)
    
    Returns:
        뉴스 목록:
        [
            {
                "title": "삼성전자, 3분기 실적 발표...",
                "url": "https://finance.naver.com/...",
                "date": "2024.01.15 09:30",
                "source": "연합뉴스"
            },
            ...
        ]
        
        크롤링 실패 시 빈 리스트 반환 (오류가 나도 서버가 죽지 않도록)
    """
    # 네이버 금융 종목 뉴스 URL
    url = f"https://finance.naver.com/item/news_news.naver?code={symbol_code}"

    # User-Agent: 브라우저인 척 헤더를 보냄 (없으면 봇으로 인식해 차단 가능)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10)

            # 네이버 금융은 euc-kr 인코딩을 사용함 (한글 깨짐 방지)
            resp.encoding = "euc-kr"

            # BeautifulSoup으로 HTML 파싱
            soup = BeautifulSoup(resp.text, "html.parser")

        news_list = []

        # CSS 선택자로 뉴스 행(row) 추출
        # "table.type5 tbody tr" → class가 type5인 table의 tbody 안의 tr들
        rows = soup.select("table.type5 tbody tr")

        for row in rows[:limit]:
            # 제목과 링크 추출
            title_tag = row.select_one("td.title a")
            date_tag = row.select_one("td.date")
            source_tag = row.select_one("td.info")  # 언론사

            if title_tag:
                news_list.append({
                    "title": title_tag.get_text(strip=True),    # 뉴스 제목
                    "url": "https://finance.naver.com" + title_tag.get("href", ""),
                    "date": date_tag.get_text(strip=True) if date_tag else "",
                    "source": source_tag.get_text(strip=True) if source_tag else "",
                })

        return news_list

    except Exception:
        # 크롤링 실패(네트워크 오류, HTML 구조 변경 등) 시 빈 리스트 반환
        # 뉴스 크롤링 실패가 전체 서버를 멈추면 안 됨
        return []


# The former Naver market HTML now redirects to a client-rendered site.
# Use the publisher's public securities RSS feed instead.
import asyncio
import logging
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
from time import monotonic
from urllib.parse import urlsplit
from xml.etree import ElementTree

MARKET_FEED_URL = "https://www.hankyung.com/feed/finance"
_market_cache: list[dict] = []
_market_cached_at = 0.0
_market_lock = asyncio.Lock()
logger = logging.getLogger(__name__)


class NewsUnavailable(RuntimeError):
    pass


def parse_market_feed(content: bytes) -> list[dict]:
    root = ElementTree.fromstring(content)
    articles = []
    seen = set()
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        parts = urlsplit(url)
        if not title or parts.scheme != "https" or parts.hostname not in {"www.hankyung.com", "hankyung.com"} or url in seen:
            continue
        raw_date = (item.findtext("pubDate") or "").strip()
        try:
            published = parsedate_to_datetime(raw_date)
            if published.tzinfo is None:
                raise ValueError("Missing timezone")
            date = published.astimezone(timezone(timedelta(hours=9))).strftime("%Y.%m.%d %H:%M")
        except (ValueError, TypeError, OverflowError):
            date = ""
        seen.add(url)
        articles.append({"title": title, "url": url, "date": date, "source": "한국경제"})
    return sorted(articles, key=lambda article: article["date"], reverse=True)


async def get_market_news(limit: int = 20) -> list[dict]:
    """Fetch public headline metadata; cache successful results for five minutes."""
    global _market_cache, _market_cached_at
    async with _market_lock:
        if _market_cache and monotonic() - _market_cached_at < 300:
            return _market_cache[:limit]
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
                response = await client.get(MARKET_FEED_URL, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Accept": "application/rss+xml, application/xml, text/xml",
                })
                response.raise_for_status()
            articles = parse_market_feed(response.content)
            if not articles:
                raise NewsUnavailable("Empty news feed")
        except (httpx.HTTPError, ElementTree.ParseError, NewsUnavailable) as exc:
            logger.warning("Market news fetch failed: %s", exc)
            raise NewsUnavailable("뉴스를 불러오지 못했어요. 잠시 후 다시 시도해 주세요.") from exc
        _market_cache = articles
        _market_cached_at = monotonic()
        return articles[:limit]
