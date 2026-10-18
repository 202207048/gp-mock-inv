"""Offline compatibility and transport checks for the existing stock-summary route."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# Local in-memory DB configuration only; no database calls in these tests.
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SECRET_KEY"] = "unit-test-news-summary-only"

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import ai
from app.services import news_summary_service as service


ARTICLES = [{"title": "Revenue increased", "summary": "An excerpt", "url": "https://example.invalid/news",
             "source": "Source", "date": "today"}]
BRIEF = {"headline": "오늘은 +1.2% 올랐고, 실적 발표가 있었습니다.",
         "summary": ["실적이 증가했습니다."], "tags": ["실적"], "disclaimer": service.NEWS_DISCLAIMER}
WIRE = {**BRIEF, "provider": "openai", "model": "test-model"}


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_paths_url_and_validated_mapping(self):
        settings_response = httpx.Response(200, json={"provider": "openai", "model": "test-model"}, request=httpx.Request("GET", "http://unit-test/settings/provider"))
        summary_response = httpx.Response(200, json=WIRE, request=httpx.Request("POST", "http://unit-test/summarize/stock-news"))
        payload = {"symbol_code": "005930", "articles": ARTICLES}
        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=settings_response)) as get, patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=summary_response)) as post:
            selected = await service.get_summary_provider("http://unit-test/")
            summary = await service.summarize_stock_news("http://unit-test/", payload)
        get.assert_awaited_once_with("http://unit-test/settings/provider")
        post.assert_awaited_once_with("http://unit-test/summarize/stock-news", json=payload)
        self.assertEqual(selected.provider, "openai")
        self.assertEqual(summary.brief(), BRIEF)

    async def test_timeout_connection_status_json_and_schema_failures(self):
        failures = [httpx.ConnectError("unit-test-secret-marker"), httpx.ReadTimeout("unit-test-secret-marker")]
        for error in failures:
            with self.subTest(error=type(error).__name__), patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=error)):
                with self.assertRaises(service.NewsSummaryUnavailable) as caught:
                    await service.summarize_stock_news("http://unit-test", {})
                self.assertNotIn("unit-test-secret-marker", str(caught.exception))
        cases = [(status, {"detail": "unit-test-secret-marker"}) for status in [400, 401, 403, 404, 422, 429, 500, 502, 503]]
        cases += [(200, {**WIRE, "summary": "wrong-type"}), (200, {**WIRE, "unexpected_secret": "unit-test-secret-marker"}), (200, []), (200, {**WIRE, "tags": ["x"] * 4})]
        for status, data in cases:
            response = httpx.Response(status, json=data, request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(status=status, data_type=type(data).__name__), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable):
                    await service.summarize_stock_news("http://unit-test", {})
        response = httpx.Response(200, text="not-json unit-test-secret-marker", request=httpx.Request("POST", "http://unit-test"))
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            with self.assertRaises(service.NewsSummaryUnavailable):
                await service.summarize_stock_news("http://unit-test", {})

    async def test_invalid_provider_metadata_is_rejected(self):
        for data in [{"provider": "claude", "model": "test"}, {"provider": "openai"}, []]:
            response = httpx.Response(200, json=data, request=httpx.Request("GET", "http://unit-test"))
            with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable):
                    await service.get_summary_provider("http://unit-test")


class RouteTests(unittest.TestCase):
    def setUp(self):
        ai._summary_cache.clear()
        app = FastAPI()
        app.include_router(ai.router, prefix="/api/ai")
        app.dependency_overrides[ai.get_current_user] = lambda: SimpleNamespace(user_id=1)
        db = Mock()
        db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(name="Example")
        app.dependency_overrides[ai.get_db] = lambda: db
        self.app = app
        self.client = TestClient(app)
        self.patches = [
            patch.object(ai, "get_summary_provider", AsyncMock(return_value=service.ProviderSettings(provider="openai", model="test-model"))),
            patch.object(ai, "summarize_stock_news", AsyncMock(return_value=service.StockNewsSummary(**WIRE))),
            patch.object(ai, "get_news_by_symbol", AsyncMock(return_value=ARTICLES)),
            patch.object(ai, "get_current_price", AsyncMock(return_value={"change_rate": 1.2})),
            patch.object(ai.settings, "AI_SERVER_URL", "http://unit-test-ai"),
            patch("app.services.ai_service._call_gemini_json", side_effect=AssertionError("No direct Gemini call allowed")),
        ]
        self.selected, self.summarize, self.news, self.quote, _, self.direct = [p.start() for p in self.patches]

    def tearDown(self):
        self.client.close()
        for p in reversed(self.patches):
            p.stop()
        ai._summary_cache.clear()

    def test_existing_route_shape_payload_and_no_direct_gemini(self):
        result = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {"symbol_code": "005930", "name": "Example", "change_rate": 1.2, **BRIEF})
        self.selected.assert_awaited_once_with("http://unit-test-ai")
        args = self.summarize.call_args.args
        self.assertEqual(args[0], "http://unit-test-ai")
        self.assertEqual(args[1], {"symbol_code": "005930", "name": "Example", "change_rate": 1.2,
                                  "articles": [{k: ARTICLES[0][k] for k in ["title", "summary", "source", "date"]}]})
        self.news.assert_awaited_once_with("005930", 5)
        self.direct.assert_not_called()

    def test_authentication_remains_required(self):
        self.app.dependency_overrides.pop(ai.get_current_user)
        response = self.client.get("/api/ai/news-summary/005930")
        self.assertIn(response.status_code, [401, 403])
        self.summarize.assert_not_awaited()

    def test_blank_symbol_is_rejected(self):
        self.assertEqual(self.client.get("/api/ai/news-summary/%20").status_code, 400)
        self.news.assert_not_awaited()

    def test_cache_hit_and_expiry(self):
        self.client.get("/api/ai/news-summary/005930")
        self.client.get("/api/ai/news-summary/005930")
        self.summarize.assert_awaited_once()
        self.news.assert_awaited_once()
        self.assertEqual(self.selected.await_count, 2)
        entry = ai._summary_cache[("005930", "openai", "test-model")]
        entry["expires_at"] = ai.datetime.utcnow() - ai.timedelta(seconds=1)
        self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(self.summarize.await_count, 2)

    def test_provider_and_model_switch_bypass_old_cache(self):
        self.client.get("/api/ai/news-summary/005930")
        for provider, model in [("gemini", "gemini-test"), ("gemini", "gemini-test-v2")]:
            self.selected.return_value = service.ProviderSettings(provider=provider, model=model)
            self.summarize.return_value = service.StockNewsSummary(**{**WIRE, "provider": provider, "model": model})
            self.assertEqual(self.client.get("/api/ai/news-summary/005930").status_code, 200)
        self.assertEqual(self.summarize.await_count, 3)

    def test_switch_during_generation_caches_actual_metadata(self):
        self.summarize.return_value = service.StockNewsSummary(**{**WIRE, "provider": "gemini", "model": "gemini-test"})
        self.client.get("/api/ai/news-summary/005930")
        self.assertNotIn(("005930", "openai", "test-model"), ai._summary_cache)
        self.assertIn(("005930", "gemini", "gemini-test"), ai._summary_cache)

    def test_summary_failure_degrades_without_caching_or_secret(self):
        self.summarize.side_effect = service.NewsSummaryUnavailable("unit-test-secret-marker")
        for _ in range(2):
            response = self.client.get("/api/ai/news-summary/005930")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["headline"], "뉴스 요약을 잠시 사용할 수 없습니다.")
            self.assertEqual(response.json()["summary"], ["Revenue increased"])
            self.assertNotIn("unit-test-secret-marker", response.text)
        self.assertFalse(ai._summary_cache)
        self.assertEqual(self.summarize.await_count, 2)

    def test_provider_lookup_failure_does_not_return_old_cached_provider(self):
        self.client.get("/api/ai/news-summary/005930")
        self.selected.side_effect = service.NewsSummaryUnavailable("offline")
        self.summarize.side_effect = service.NewsSummaryUnavailable("offline")
        response = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["headline"], "뉴스 요약을 잠시 사용할 수 없습니다.")

    def test_upstream_failures_are_not_browser_auth_failures(self):
        # Exercise real transport + validation through the unchanged Backend route.
        self.patches[1].stop()
        for status in [401, 403, 422, 429, 500, 503]:
            response = httpx.Response(status, json={"detail": "unit-test-secret-marker"},
                                      request=httpx.Request("POST", "http://unit-test-ai/summarize/stock-news"))
            with self.subTest(status=status), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                result = self.client.get("/api/ai/news-summary/005930")
            self.assertEqual(result.status_code, 200)
            self.assertNotIn("unit-test-secret-marker", result.text)
            self.assertEqual(result.json()["summary"], ["Revenue increased"])

    def test_blank_ai_response_fields_degrade_without_caching(self):
        self.patches[1].stop()
        for field, value in [("model", " "), ("headline", " \t"),
                             ("summary", [" "]), ("tags", ["\t"]),
                             ("disclaimer", " ")]:
            response = httpx.Response(
                200, json={**WIRE, field: value},
                request=httpx.Request("POST", "http://unit-test-ai/summarize/stock-news"),
            )
            with self.subTest(field=field), patch.object(
                httpx.AsyncClient, "post", AsyncMock(return_value=response),
            ):
                result = self.client.get("/api/ai/news-summary/005930")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["headline"], "뉴스 요약을 잠시 사용할 수 없습니다.")
            self.assertEqual(result.json()["summary"], ["Revenue increased"])
            self.assertFalse(ai._summary_cache)

    def test_invalid_ai_server_url_degrades_without_caching(self):
        self.patches[0].stop()
        self.patches[1].stop()
        with patch.object(ai.settings, "AI_SERVER_URL", "http://localhost:invalid"):
            result = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["headline"], "뉴스 요약을 잠시 사용할 수 없습니다.")
        self.assertEqual(result.json()["summary"], ["Revenue increased"])
        self.assertNotIn("localhost:invalid", result.text)
        self.assertFalse(ai._summary_cache)

    def test_empty_news_is_not_cached_before_feed_recovers(self):
        self.news.return_value = []
        empty = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["summary"], [])
        self.assertFalse(ai._summary_cache)
        self.summarize.assert_not_awaited()
        self.news.return_value = ARTICLES
        recovered = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(recovered.json()["headline"], BRIEF["headline"])
        self.assertEqual(self.news.await_count, 2)
        self.summarize.assert_awaited_once()

    def test_no_news_and_missing_quote_keep_contract(self):
        self.news.return_value = []
        self.quote.side_effect = RuntimeError("unit-test-secret-marker")
        response = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"symbol_code": "005930", "name": "Example", "change_rate": None,
                                          "headline": "표시할 뉴스가 없습니다.", "summary": [], "tags": [],
                                          "disclaimer": service.NEWS_DISCLAIMER})
        self.summarize.assert_not_awaited()
        self.assertNotIn("unit-test-secret-marker", response.text)


if __name__ == "__main__":
    unittest.main()
