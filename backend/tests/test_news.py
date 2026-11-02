import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import httpx
from fastapi.testclient import TestClient
from app.news_preview import app
from app.services import news_service as news


def card(title, image=""):
    return f'''<div class="sa_item_flex"><div class="sa_thumb">{image}</div>
    <div class="sa_text"><a class="sa_text_title" href="/article/{title}">{title}</a>
    <div class="sa_text_lede">Summary</div><div class="sa_text_press">Source</div>
    <div class="sa_text_datetime">1 minute ago</div></div></div>'''


HTML = (card("First", '<img data-src="https://example.com/first.jpg" src="placeholder.gif">')
        + card("NoImage") + card("Third", '<img src="//example.com/third.jpg">')
        + card("Unsafe", '<img data-src="javascript:alert(1)">'))


class NewsTests(unittest.IsolatedAsyncioTestCase):
    def test_images_stay_with_articles_and_lazy_source_wins(self):
        result = news.parse_market_news(HTML)
        self.assertEqual([item['image_url'] for item in result], [
            'https://example.com/first.jpg', '', 'https://example.com/third.jpg', ''])
        self.assertEqual(result[0]['summary'], 'Summary')
        self.assertEqual(result[0]['source'], 'Source')
        self.assertEqual(result[0]['url'], 'https://news.naver.com/article/First')

    async def test_market_limit_and_api_image_response(self):
        response = httpx.Response(200, text=HTML, request=httpx.Request('GET', news.MARKET_NEWS_URL))
        with patch.object(httpx.AsyncClient, 'get', new_callable=AsyncMock, return_value=response):
            self.assertEqual(len(await news.get_market_news(2)), 2)
            with TestClient(app) as client:
                result = client.get('/api/news/market?limit=1')
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()[0]['image_url'], 'https://example.com/first.jpg')

    async def test_failure_does_not_masquerade_as_empty_success(self):
        with patch.object(httpx.AsyncClient, 'get', new_callable=AsyncMock, side_effect=httpx.ConnectError('offline')):
            with self.assertRaises(news.NewsUnavailable):
                await news.get_market_news()

    def test_api_validation_and_error(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/news/market?limit=0').status_code, 422)
            with patch('app.routers.news.get_market_news', new_callable=AsyncMock, side_effect=news.NewsUnavailable('unavailable')):
                self.assertEqual(client.get('/api/news/market').status_code, 502)


def article(title, stamp):
    return {
        "titleFull": title,
        "mobileNewsUrl": f"https://example.com/{title}",
        "body": "요약",
        "officeName": "신문",
        "datetime": stamp,
    }


def page(*rows):
    return httpx.Response(200, json=[{"items": list(rows)}])


class SymbolNewsWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_articles_inside_three_days(self):
        responses = [
            page(article("너무최근", "202611101200"), article("당일", "202611011000")),
            page(article("사흘전", "202610290900"), article("이전", "202610200900")),
        ]

        async def fake_get(self, url, params=None, **kwargs):
            return responses[params["page"] - 1]

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            rows = await news.get_news_by_symbol_around("005930", "2026-11-01", days=3, limit=5)

        self.assertEqual([row["title"] for row in rows], ["당일", "사흘전"])
        self.assertEqual(set(rows[0]), {"title", "url", "summary", "source", "date"})
        self.assertEqual(rows[0]["date"], "2026.11.01 10:00")

    async def test_stops_after_the_window_and_caps_pages(self):
        calls = []

        async def fake_get(self, url, params=None, **kwargs):
            calls.append(params["page"])
            if params["page"] == 1:
                return page(article("이전", "202610010900"), article("창안", "202611011000"))
            return page(article("더이전", "202609010900"))

        with patch.object(httpx.AsyncClient, "get", new=fake_get):
            rows = await news.get_news_by_symbol_around("005930", datetime(2026, 11, 1), limit=5)
        self.assertEqual([row["title"] for row in rows], ["창안"])
        self.assertEqual(calls, [1])

        calls.clear()

        async def newer(self, url, params=None, **kwargs):
            calls.append(params["page"])
            return page(article(f"최근{params['page']}", "202611101200"))

        with patch.object(httpx.AsyncClient, "get", new=newer):
            self.assertEqual(await news.get_news_by_symbol_around("005930", "2026-11-01"), [])
        self.assertEqual(calls, list(range(1, 11)))

    async def test_error_or_bad_date_returns_empty(self):
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=httpx.ConnectError("offline")):
            self.assertEqual(await news.get_news_by_symbol_around("005930", "2026-11-01"), [])
        response = httpx.Response(500, json={}, request=httpx.Request("GET", "https://example.com"))
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=response):
            self.assertEqual(await news.get_news_by_symbol_around("005930", "2026-11-01"), [])
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, side_effect=AssertionError("request")):
            self.assertEqual(await news.get_news_by_symbol_around("005930", "날짜아님"), [])
        aware = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)
        response = page(article("한국날짜", "202611011000"))
        with patch.object(httpx.AsyncClient, "get", new_callable=AsyncMock, return_value=response):
            rows = await news.get_news_by_symbol_around("005930", aware, days=0, limit=1)
        self.assertEqual([row["title"] for row in rows], ["한국날짜"])


if __name__ == '__main__':
    unittest.main()
