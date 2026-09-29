import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from app.news_preview import app
from app.services import news_service as news


FEED = b'''<rss><channel>
<item><title>Older &amp; headline</title><link>https://www.hankyung.com/article/1</link><pubDate>Wed, 23 Sep 2026 08:00:00 +0900</pubDate></item>
<item><title>Latest headline</title><link>https://www.hankyung.com/article/2</link><pubDate>Wed, 23 Sep 2026 09:00:00 +0900</pubDate></item>
<item><title>Duplicate</title><link>https://www.hankyung.com/article/2</link></item>
<item><title>Unsafe</title><link>javascript:alert(1)</link></item>
</channel></rss>'''


class NewsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        news._market_cache = []
        news._market_cached_at = 0
        news._market_lock = asyncio.Lock()

    def test_parser(self):
        result = news.parse_market_feed(FEED)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['title'], 'Latest headline')
        self.assertEqual(result[0]['date'], '2026.09.23 09:00')
        self.assertEqual(result[1]['title'], 'Older & headline')

    async def test_cache_keeps_full_feed_and_coalesces_requests(self):
        response = httpx.Response(200, content=FEED, request=httpx.Request('GET', news.MARKET_FEED_URL))
        with patch.object(httpx.AsyncClient, 'get', new_callable=AsyncMock, return_value=response) as get:
            first, second = await asyncio.gather(news.get_market_news(1), news.get_market_news(8))
            self.assertEqual(len(first), 1)
            self.assertEqual(len(second), 2)
            get.assert_awaited_once()

    async def test_failure_does_not_masquerade_as_empty_success(self):
        with patch.object(httpx.AsyncClient, 'get', new_callable=AsyncMock, side_effect=httpx.ConnectError('offline')):
            with self.assertRaises(news.NewsUnavailable):
                await news.get_market_news()

    def test_api_validation_and_error(self):
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/news/market?limit=0').status_code, 422)
            with patch('app.routers.news.get_market_news', new_callable=AsyncMock, side_effect=news.NewsUnavailable('unavailable')):
                self.assertEqual(client.get('/api/news/market').status_code, 502)


if __name__ == '__main__':
    unittest.main()
