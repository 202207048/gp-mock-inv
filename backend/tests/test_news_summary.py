"""Offline compatibility and transport checks for the existing stock-summary route."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

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
        self.selected.assert_awaited_once_with("http://unit-test-ai", trace_id=UUID(result.headers["X-Trace-ID"]))
        args = self.summarize.call_args.args
        self.assertEqual(args[0], "http://unit-test-ai")
        self.assertEqual(args[1], {"symbol_code": "005930", "name": "Example", "change_rate": 1.2,
                                  "articles": [{k: ARTICLES[0][k] for k in ["title", "summary", "source", "date"]}], "include_ai_metadata": True})
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


class MetadataTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_trace_header_and_internal_metadata_never_leak_into_brief(self):
        trace, operation = uuid4(), uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(operation),
                    "provider": "openai", "model": "test-model",
                    "usage": {"input_tokens": 0, "output_tokens": 20, "reasoning_tokens": None,
                              "total_tokens": None, "cached_tokens": 0}, "latency_ms": 12.5}
        settings_response = httpx.Response(200, json={"provider": "openai", "model": "test-model"}, request=httpx.Request("GET", "http://unit-test"))
        summary_response = httpx.Response(200, json={**WIRE, "ai": metadata}, request=httpx.Request("POST", "http://unit-test"))
        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=settings_response)) as get, patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=summary_response)) as post:
            await service.get_summary_provider("http://unit-test", trace_id=trace)
            result = await service.summarize_stock_news("http://unit-test", {"include_ai_metadata": True}, trace_id=trace)
        self.assertEqual(get.call_args.kwargs["headers"], {"X-Trace-ID": str(trace)})
        self.assertEqual(post.call_args.kwargs["headers"], {"X-Trace-ID": str(trace)})
        self.assertEqual(result.ai.trace_id, trace)
        self.assertEqual(result.ai.operation_id, operation)
        self.assertEqual(result.ai.usage.input_tokens, 0)
        self.assertIsNone(result.ai.usage.total_tokens)
        self.assertEqual(result.brief(), BRIEF)

    async def test_invalid_metadata_is_safe_unavailable(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                    "model": "test-model", "usage": {}, "latency_ms": 1}
        changes = [{"trace_id": str(uuid4())}, {"operation_id": "bad"},
                   {"provider": "gemini"}, {"model": "different-model"},
                   {"unexpected_secret": "unit-test-secret-marker"}]
        for change in changes:
            response = httpx.Response(200, json={**WIRE, "ai": {**metadata, **change}}, request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(fields=list(change)), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable) as caught:
                    await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)
                self.assertNotIn("unit-test-secret-marker", str(caught.exception))

    async def test_invalid_diagnostics_discard_only_observation(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                    "model": "test-model", "usage": {}, "latency_ms": 1}
        changes = [{"latency_ms": None}, {"latency_ms": -1}, {"latency_ms": "invalid"},
                   {"usage": None}, {"usage": []}, {"usage": {"input_tokens": -1}},
                   {"usage": {"input_tokens": True}}, {"usage": {"input_tokens": "10"}},
                   {"usage": {"output_tokens": 1.5}}, {"usage": {"unexpected": 1}}]
        for change in changes:
            response = httpx.Response(200, json={**WIRE, "ai": {**metadata, **change}}, request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(change=change), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                result = await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)
                self.assertEqual(result.brief(), BRIEF)
                self.assertIsNone(result.ai)

    async def test_bad_diagnostics_do_not_hide_identity_mismatch(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                    "model": "test-model", "usage": {"input_tokens": True}, "latency_ms": None}
        for change in [{"provider": "gemini"}, {"model": "different-model"},
                       {"trace_id": str(uuid4())}, {"operation_id": "bad"}]:
            response = httpx.Response(200, json={**WIRE, "ai": {**metadata, **change}}, request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(fields=list(change)), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable):
                    await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)

    async def test_unknown_structure_and_invalid_core_remain_strict(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                    "model": "test-model", "usage": {}, "latency_ms": None}
        cases = [{**WIRE, "ai": value} for value in [[], "invalid", False, {},
                 {k: v for k, v in metadata.items() if k != "trace_id"},
                 {**metadata, "unexpected_secret": "unit-test-secret-marker"}]]
        cases += [{**WIRE, "headline": " ", "ai": metadata},
                  {**WIRE, "summary": "wrong-type", "ai": metadata},
                  {**WIRE, "unexpected_secret": "unit-test-secret-marker", "ai": metadata}]
        for wire in cases:
            response = httpx.Response(200, json=wire, request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(metadata_type=type(wire["ai"]).__name__), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable) as caught:
                    await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)
                self.assertNotIn("unit-test-secret-marker", str(caught.exception))

    def test_wire_schema_and_usage_semantics_match_ai_contract(self):
        usage = service.UsageMetrics()
        self.assertTrue(all(v is None for v in usage.model_dump().values()))
        self.assertEqual(service.UsageMetrics(input_tokens=0).input_tokens, 0)
        with self.assertRaises(Exception):
            service.UsageMetrics(output_tokens="10")


class MetadataRouteTests(unittest.TestCase):
    setUp = RouteTests.setUp
    tearDown = RouteTests.tearDown

    # Reuse the existing offline dependency overrides and no-direct-provider guard.
    def test_cache_has_new_trace_but_no_replayed_generation(self):
        observations = []
        @self.app.middleware("http")
        async def observe(request, call_next):
            response = await call_next(request)
            observations.append((request.state.trace_id, request.state.ai_execution))
            return response
        operation = uuid4()
        def measured(base_url, payload, *, trace_id):
            return service.StockNewsSummary(**WIRE, ai=service.AIExecutionMetadata(
                trace_id=trace_id, operation_id=operation, provider="openai", model="test-model",
                usage=service.UsageMetrics(input_tokens=10, total_tokens=20), latency_ms=12.0))
        self.summarize.side_effect = measured
        first = self.client.get("/api/ai/news-summary/005930", headers={"X-Trace-ID": str(uuid4())})
        second = self.client.get("/api/ai/news-summary/005930")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(set(first.json()), {"symbol_code", "name", "change_rate", "headline", "summary", "tags", "disclaimer"})
        self.assertNotEqual(first.headers["X-Trace-ID"], second.headers["X-Trace-ID"])
        self.assertEqual(observations[0][1].operation_id, operation)
        self.assertIsNone(observations[1][1])
        self.assertEqual(observations[0][0], UUID(first.headers["X-Trace-ID"]))
        self.assertNotIn(str(operation), repr(ai._summary_cache))
        self.summarize.assert_awaited_once()
        self.assertEqual(self.summarize.call_args.kwargs["trace_id"], observations[0][0])

    def test_invalid_diagnostics_preserve_content_and_success_cache(self):
        self.patches[1].stop()  # Exercise actual transport/validation, not a parsed DTO fake.
        observations = []
        @self.app.middleware("http")
        async def observe(request, call_next):
            response = await call_next(request)
            observations.append(request.state.ai_execution)
            return response
        for diagnostics in [{"usage": {}, "latency_ms": None},
                            {"usage": {"input_tokens": True}, "latency_ms": 1}]:
            ai._summary_cache.clear()
            self.news.reset_mock()
            observations.clear()
            operation = str(uuid4())
            def summary_response(url, *, json, headers):
                metadata = {"trace_id": headers["X-Trace-ID"], "operation_id": operation,
                            "provider": "openai", "model": "test-model", **diagnostics}
                return httpx.Response(200, json={**WIRE, "ai": metadata}, request=httpx.Request("POST", url))
            with self.subTest(diagnostics=diagnostics), patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=summary_response)) as post:
                first = self.client.get("/api/ai/news-summary/005930")
                second = self.client.get("/api/ai/news-summary/005930")
                self.assertEqual(first.status_code, 200)
                self.assertEqual(first.json(), {"symbol_code": "005930", "name": "Example", "change_rate": 1.2, **BRIEF})
                self.assertEqual(first.json(), second.json())
                post.assert_awaited_once()
                self.news.assert_awaited_once()
            entry = ai._summary_cache[("005930", "openai", "test-model")]
            self.assertEqual(entry["data"], first.json())
            self.assertNotIn("ai", entry["data"])
            self.assertNotIn(operation, repr(entry))
            self.assertEqual(observations, [None, None])
            self.assertNotEqual(first.headers["X-Trace-ID"], second.headers["X-Trace-ID"])

    def test_identity_mismatch_with_bad_diagnostics_keeps_fallback_uncached(self):
        self.patches[1].stop()
        for mismatch in [{"provider": "gemini"}, {"model": "different-model"},
                         {"trace_id": str(uuid4())}]:
            ai._summary_cache.clear()
            def summary_response(url, *, json, headers):
                metadata = {"trace_id": headers["X-Trace-ID"], "operation_id": str(uuid4()),
                            "provider": "openai", "model": "test-model", "usage": {},
                            "latency_ms": None, **mismatch}
                return httpx.Response(200, json={**WIRE, "ai": metadata}, request=httpx.Request("POST", url))
            with self.subTest(mismatch=mismatch), patch.object(httpx.AsyncClient, "post", AsyncMock(side_effect=summary_response)) as post:
                result = self.client.get("/api/ai/news-summary/005930")
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()["headline"], "뉴스 요약을 잠시 사용할 수 없습니다.")
                self.assertEqual(result.json()["summary"], [ARTICLES[0]["title"]])
                self.assertFalse(ai._summary_cache)
                post.assert_awaited_once()

    def test_fallback_and_empty_news_never_have_success_metadata(self):
        observations = []
        @self.app.middleware("http")
        async def observe(request, call_next):
            response = await call_next(request)
            observations.append(request.state.ai_execution)
            return response
        self.summarize.side_effect = service.NewsSummaryUnavailable("unit-test-secret-marker")
        for articles in [ARTICLES, []]:
            self.news.return_value = articles
            response = self.client.get("/api/ai/news-summary/005930")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("ai", response.json())
            self.assertNotIn("unit-test-secret-marker", response.text)
            self.assertEqual(UUID(response.headers["X-Trace-ID"]).version, 4)
        self.assertEqual(observations, [None, None])
        self.assertFalse(ai._summary_cache)


class ProfileMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_profile_identity_without_changing_external_brief(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()),
                    "provider": "openai", "model": "test-model", "usage": {}, "latency_ms": 1,
                    "profile_id": "sol", "profile_revision": 1}
        response = httpx.Response(200, json={**WIRE, "ai": metadata},
                                  request=httpx.Request("POST", "http://unit-test"))
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            result = await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)
        self.assertEqual((result.ai.profile_id, result.ai.profile_revision), ("sol", 1))
        self.assertEqual(result.brief(), BRIEF)

    async def test_invalid_profile_identity_is_not_ignored_as_diagnostics(self):
        trace = uuid4()
        base = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                "model": "test-model", "usage": {}, "latency_ms": None}
        for fields in [{"profile_id": "sol"}, {"profile_revision": 1},
                       {"profile_id": "Sol", "profile_revision": 1},
                       {"profile_id": " sol ", "profile_revision": 1},
                       {"profile_id": "sol", "profile_revision": True},
                       {"profile_id": "sol", "profile_revision": 0}]:
            response = httpx.Response(200, json={**WIRE, "ai": {**base, **fields}},
                                      request=httpx.Request("POST", "http://unit-test"))
            with self.subTest(fields=fields), patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
                with self.assertRaises(service.NewsSummaryUnavailable):
                    await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)

    async def test_valid_profile_does_not_make_optional_numeric_diagnostics_required(self):
        trace = uuid4()
        metadata = {"trace_id": str(trace), "operation_id": str(uuid4()), "provider": "openai",
                    "model": "test-model", "profile_id": "luna", "profile_revision": 1,
                    "usage": {"input_tokens": True}, "latency_ms": None}
        response = httpx.Response(200, json={**WIRE, "ai": metadata},
                                  request=httpx.Request("POST", "http://unit-test"))
        with patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)):
            result = await service.summarize_stock_news("http://unit-test", {}, trace_id=trace)
        self.assertIsNone(result.ai)
        self.assertEqual(result.brief(), BRIEF)


if __name__ == "__main__":
    unittest.main()
