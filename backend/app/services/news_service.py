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


async def get_market_news(limit: int = 20) -> list[dict]:
    """
    전체 주식 시장 뉴스를 네이버 금융에서 크롤링합니다.
    
    특정 종목이 아닌 증권 전반의 뉴스를 가져옵니다.
    
    Args:
        limit: 가져올 뉴스 개수 (기본 20개)
    
    Returns:
        시장 뉴스 목록
    """
    url = "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=10)
            resp.encoding = "euc-kr"
            soup = BeautifulSoup(resp.text, "html.parser")

        news_list = []

        # 뉴스 목록 항목 추출
        items = soup.select("ul.newsList li")

        for item in items[:limit]:
            title_tag = item.select_one("a.articleSubject")
            date_tag = item.select_one("span.wdate")

            if title_tag:
                news_list.append({
                    "title": title_tag.get_text(strip=True),
                    "url": "https://finance.naver.com" + title_tag.get("href", ""),
                    "date": date_tag.get_text(strip=True) if date_tag else "",
                })

        return news_list

    except Exception:
        return []
