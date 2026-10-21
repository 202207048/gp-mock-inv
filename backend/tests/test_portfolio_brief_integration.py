"""메모리 SQLite와 MockTransport만 사용하는 Backend Portfolio Brief 회귀 검증."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.models import Account, ItemMaster, Order, Portfolio, User
from app.routers import ai as routes
from app.schemas.portfolio_brief import (
    PORTFOLIO_DISCLAIMER, PortfolioBriefFacts, PortfolioBriefResponse,
)
from app.services import portfolio_brief_client as service
from app.services import portfolio_brief_service as facts_service
from app.utils.deps import get_current_user

START = datetime(2026, 10, 21, 1, tzinfo=timezone.utc)
NEWS = {"title": "가온기술, 신규 공급 계약", "summary": "공급 계약을 공시했다.",
        "source": "가상매체", "date": "2026-10-21", "url": "https://example.invalid/one"}
BODY = {"entry_context": [], "headline": "현재 보유 상태와 최근 공급 계약을 확인합니다.",
        "current_status": ["10주를 보유하고 있습니다."],
        "position_summary": ["현재 포지션에서 매수 1건이 확인됩니다."],
        "recent_issues": ["신규 공급 계약을 공시했습니다."],
        "positive_factors": ["신규 계약이 확인됩니다."], "risk_factors": [],
        "watch_points": ["계약 후속 공시를 확인합니다."],
        "limitations": ["과거 뉴스와 가격 시점 자료는 제공되지 않습니다."],
        "disclaimer": PORTFOLIO_DISCLAIMER}
RealAsyncClient = httpx.AsyncClient


def facts():
    return PortfolioBriefFacts.model_validate({
        "account_id": 997, "symbol_code": "SYN001", "security_name": "가온기술",
        "holding": {"quantity": 10, "avg_price": "100.250000000000000001",
                    "current_price": "120.50", "unrealized_pnl": "202.499999999999999990",
                    "return_rate": "20.19950124688279301600897756"},
        "position": {"available": True, "reason": None, "started_at": START,
                     "opening_order_id": 801, "opening_price": "100.25",
                     "buy_count": 1, "sell_count": 0},
        "trades": [{"order_id": 801, "order_type": "매수", "quantity": 10,
                    "price": "100.25", "filled_at": START}],
        "recent_news": [NEWS], "price_context": {"current_price": "120.50"},
        "limitations": ["historical_news_unavailable", "historical_price_unavailable"],
    })


def wire(profile, trace):
    model = "gpt-6-luna" if profile == "luna" else "gpt-6.1-sol"
    return {**deepcopy(BODY), "provider": "openai", "model": model,
            "ai": {"trace_id": str(trace), "operation_id": str(uuid4()),
                   "provider": "openai", "model": model, "profile_id": profile,
                   "profile_revision": 1, "usage": {"input_tokens": 11, "output_tokens": 22,
                   "reasoning_tokens": 0, "total_tokens": 33, "cached_tokens": 0},
                   "latency_ms": 12.5}}


def ai_schema():
    # Contract check imports only pure schema definitions, never the app/settings.
    name = "_portfolio_bridge_ai_schema"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[2] / "ai_server/app/schemas.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


class ProjectionTests(unittest.TestCase):
    def test_exact_projection_matches_actual_ai_contract_and_excludes_private_fields(self):
        bundle = facts()
        payload = service.project_portfolio_brief(bundle)
        self.assertEqual(set(payload), {"symbol_code", "security_name", "holding", "position",
                                       "trades", "recent_news", "limitations", "profile",
                                       "include_ai_metadata", "entry_price_context"})
        self.assertEqual(payload["holding"], bundle.holding.model_dump(mode="json"))
        self.assertEqual(payload["position"], bundle.position.model_dump(mode="json"))
        self.assertEqual(payload["trades"], [t.model_dump(mode="json") for t in bundle.trades])
        self.assertEqual(payload["recent_news"], [NEWS])
        self.assertEqual(payload["limitations"], bundle.limitations)
        self.assertEqual(payload["profile"], "luna")
        self.assertIs(payload["include_ai_metadata"], True)
        actual = ai_schema().PortfolioBriefRequest.model_validate(payload)
        self.assertEqual(actual.model_dump(mode="json"), payload)
        text = json.dumps(payload, allow_nan=False)
        for field in ("account_id", "user_id", "email", "user_name", "price_context",
                      "position_start_price", "change_since_position_start"):
            self.assertNotIn(json.dumps(field) + ":", text)

    def test_decimal_values_are_strings_and_never_lossy_floats(self):
        payload = service.project_portfolio_brief(facts(), "sol")
        self.assertEqual(payload["profile"], "sol")
        for key in ("avg_price", "current_price", "unrealized_pnl", "return_rate"):
            self.assertIsInstance(payload["holding"][key], str)
            self.assertEqual(Decimal(payload["holding"][key]), getattr(facts().holding, key))
        self.assertEqual(payload["holding"]["avg_price"], "100.250000000000000001")
        with self.assertRaises(ValueError):
            service.project_portfolio_brief(facts(), "gemini")

    def test_unavailable_position_price_and_empty_news_are_not_reconstructed(self):
        data = facts().model_dump()
        data.update(position={"available": False, "reason": "legacy_execution_time_unavailable"},
                    trades=[], recent_news=[])
        data["holding"].update(current_price=None, unrealized_pnl=None, return_rate=None)
        data["limitations"] += ["legacy_execution_time_unavailable", "current_price_unavailable",
                                 "news_unavailable"]
        payload = service.project_portfolio_brief(PortfolioBriefFacts.model_validate(data))
        self.assertFalse(payload["position"]["available"])
        self.assertIsNone(payload["position"]["started_at"])
        self.assertIsNone(payload["position"]["opening_order_id"])
        self.assertIsNone(payload["holding"]["current_price"])
        self.assertEqual((payload["trades"], payload["recent_news"]), ([], []))
        ai_schema().PortfolioBriefRequest.model_validate(payload)

    def test_backend_domain_output_matches_actual_ai_response_contract(self):
        data = wire("sol", uuid4())
        ai_schema().PortfolioBriefResponse.model_validate(data)
        result = service._parse_result(data, facts(), "sol", UUID(data["ai"]["trace_id"]))
        exposed = PortfolioBriefResponse(symbol_code="SYN001", security_name="가온기술",
                                          **result.brief()).model_dump()
        self.assertEqual(exposed, {"symbol_code": "SYN001", "security_name": "가온기술", **BODY})


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.trace = uuid4()
        self.requests = []
        self.clients = []

    async def call(self, *, data=None, status=200, error=None, raw=None, base_url="http://ai.test/",
                   profile="luna", bundle=None):
        if data is None:
            data = wire(profile, self.trace)
        def handle(request):
            self.requests.append(request)
            if error:
                raise error
            if raw is not None:
                return httpx.Response(status, text=raw)
            return httpx.Response(status, json=data)
        def factory(**options):
            client = RealAsyncClient(transport=httpx.MockTransport(handle), **options)
            self.clients.append(client)
            return client
        with patch.object(service.httpx, "AsyncClient", side_effect=factory):
            return await service.request_portfolio_brief(
                base_url, bundle or facts(), profile=profile, trace_id=self.trace,
            )

    async def test_one_post_trace_explicit_profile_timeout_and_client_closure(self):
        result = await self.call(profile="sol")
        self.assertEqual(len(self.requests), 1)
        request = self.requests[0]
        self.assertEqual((request.method, str(request.url)), ("POST", "http://ai.test/brief/portfolio"))
        self.assertEqual(request.headers["X-Trace-ID"], str(self.trace))
        self.assertEqual(json.loads(request.content), service.project_portfolio_brief(facts(), "sol"))
        self.assertEqual(request.extensions["timeout"], dict(connect=30.0, read=30.0, write=30.0, pool=30.0))
        self.assertTrue(self.clients[0].is_closed)
        self.assertEqual(result.brief(), BODY)
        self.assertEqual((result.ai.trace_id, result.ai.profile_id, result.ai.usage.total_tokens),
                         (self.trace, "sol", 33))
        for field in ("provider", "model", "ai", "usage", "trace_id", "operation_id"):
            self.assertNotIn(field, result.brief())

    async def test_timeout_connection_and_other_http_errors_are_safe_without_retry(self):
        for error, expected in [(httpx.ReadTimeout("private-marker"), 502),
                                (httpx.ConnectTimeout("private-marker"), 502),
                                (httpx.ConnectError("private-marker"), 503),
                                (httpx.RemoteProtocolError("private-marker"), 502)]:
            with self.subTest(error=type(error).__name__):
                count = len(self.requests)
                with self.assertRaises(service.PortfolioBriefUnavailable) as caught:
                    await self.call(error=error)
                self.assertEqual(caught.exception.status_code, expected)
                self.assertNotIn("private-marker", str(caught.exception))
                self.assertEqual(len(self.requests), count + 1)
                self.assertTrue(self.clients[-1].is_closed)

    async def test_upstream_statuses_are_safe_and_never_browser_login_errors(self):
        for status in [400, 401, 403, 404, 422, 429, 500, 502, 503]:
            with self.subTest(status=status):
                count = len(self.requests)
                with self.assertRaises(service.PortfolioBriefUnavailable) as caught:
                    await self.call(status=status, data={"detail": "private-marker"})
                self.assertEqual(caught.exception.status_code, 503 if status == 503 else 502)
                self.assertNotIn("private-marker", str(caught.exception))
                self.assertEqual(len(self.requests), count + 1)

    async def test_missing_invalid_url_is_unavailable_without_network(self):
        for base in ("", "   ", "http://localhost:invalid", "not-a-url", None):
            with self.subTest(base=base):
                with self.assertRaises(service.PortfolioBriefUnavailable) as caught:
                    await self.call(base_url=base)
                self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.requests, [])

    async def test_malformed_json_and_non_object_response_are_502(self):
        for options in ({"raw": "not-json private-marker"}, {"data": []}, {"data": "private-marker"}):
            with self.subTest(options=options):
                with self.assertRaises(service.PortfolioBriefUnavailable) as caught:
                    await self.call(**options)
                self.assertEqual(caught.exception.status_code, 502)
                self.assertNotIn("private-marker", str(caught.exception))

    async def test_strict_domain_validation_rejects_bad_fields_and_extra_data(self):
        cases = [{"headline": "  "}, {"headline": 123}, {"current_status": []},
                 {"current_status": "wrong"}, {"current_status": [" "]},
                 {"current_status": ["x"] * 4}, {"watch_points": ["x"] * 4},
                 {"risk_factors": [True]}, {"limitations": None},
                 {"disclaimer": "wrong"}, {"unexpected": "private-marker"}]
        for field in BODY:
            data = wire("luna", self.trace)
            data.pop(field)
            cases.append(data)
        for change in cases:
            data = wire("luna", self.trace)
            # The missing-field cases already contain a complete response minus one key.
            data = change if "ai" in change else {**data, **change}
            with self.subTest(fields=list(change)):
                with self.assertRaises(service.PortfolioBriefUnavailable):
                    await self.call(data=data)

    async def test_missing_bad_or_mismatched_identity_is_not_ignored_as_diagnostics(self):
        base = wire("luna", self.trace)
        changes = [{"provider": "gemini"}, {"model": "wrong"}, {"trace_id": str(uuid4())},
                   {"operation_id": "bad"}, {"profile_id": "sol"}, {"profile_id": None},
                   {"profile_revision": 0}, {"profile_revision": True},
                   {"profile_revision": None}, {"unexpected": "private-marker"}]
        for change in changes:
            data = deepcopy(base)
            data["ai"].update(change, usage={"input_tokens": True}, latency_ms=None)
            with self.subTest(change=change), self.assertRaises(service.PortfolioBriefUnavailable):
                await self.call(data=data)
        for meta in (None, [], "private-marker"):
            with self.subTest(meta_type=type(meta).__name__), self.assertRaises(service.PortfolioBriefUnavailable):
                await self.call(data={**base, "ai": meta})
        data = deepcopy(base)
        data.pop("ai")
        with self.assertRaises(service.PortfolioBriefUnavailable):
            await self.call(data=data)

    async def test_bad_numeric_diagnostics_preserve_domain_after_identity_validation(self):
        for change in ({"usage": None}, {"usage": {"input_tokens": True}},
                       {"usage": {"unexpected": 1}}, {"latency_ms": -1},
                       {"latency_ms": None}, {"latency_ms": "invalid"}):
            data = wire("luna", self.trace)
            data["ai"].update(change)
            with self.subTest(change=change):
                result = await self.call(data=data)
                self.assertEqual(result.brief(), BODY)
                self.assertIsNone(result.ai)

    async def test_unavailable_facts_must_not_be_reconstructed_in_upstream_response(self):
        data = facts().model_dump()
        data.update(position={"available": False, "reason": "invalid_trade_history"}, trades=[])
        with self.assertRaises(service.PortfolioBriefUnavailable):
            await self.call(bundle=PortfolioBriefFacts.model_validate(data))
        data = facts().model_dump()
        data["recent_news"] = []
        with self.assertRaises(service.PortfolioBriefUnavailable):
            await self.call(bundle=PortfolioBriefFacts.model_validate(data))

    async def test_overlapping_profiles_keep_request_owned_trace_usage_and_body(self):
        ready = asyncio.Event()
        seen, clients = [], []
        async def handle(request):
            payload = json.loads(request.content)
            profile = payload["profile"]
            seen.append(profile)
            if len(seen) == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 2)
            data = wire(profile, UUID(request.headers["X-Trace-ID"]))
            data["headline"] = profile
            data["ai"]["usage"]["input_tokens"] = 10 if profile == "luna" else 20
            return httpx.Response(200, json=data)
        def factory(**options):
            client = RealAsyncClient(transport=httpx.MockTransport(handle), **options)
            clients.append(client)
            return client
        traces = [uuid4(), uuid4()]
        with patch.object(service.httpx, "AsyncClient", side_effect=factory):
            results = await asyncio.gather(*(
                service.request_portfolio_brief("http://ai.test", facts(), profile=profile, trace_id=trace)
                for profile, trace in zip(("luna", "sol"), traces)
            ))
        self.assertEqual(sorted(seen), ["luna", "sol"])
        self.assertTrue(all(c.is_closed for c in clients))
        self.assertNotEqual(results[0].ai.operation_id, results[1].ai.operation_id)
        for index, profile in enumerate(("luna", "sol")):
            result = results[index]
            self.assertEqual((result.headline, result.ai.profile_id, result.ai.trace_id),
                             (profile, profile, traces[index]))
            self.assertEqual(result.ai.usage.input_tokens, 10 if profile == "luna" else 20)


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        self.addCleanup(self.engine.dispose)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session() as db:
            db.add_all([User(user_id=i, login_id=str(i), passwd="unused", user_name="test",
                             email=f"test{i}@example.invalid") for i in (1, 2)])
            db.add_all([Account(account_id=i, user_id=i, balance=10000, withdrawable_cash=10000)
                        for i in (1, 2)])
            db.add(ItemMaster(symbol_code="SYN001", name="가온기술", market_type="국내주식"))
            db.add_all([Portfolio(account_id=i, symbol_code="SYN001", hold_quantity=10,
                                  avg_price=Decimal("100.25")) for i in (1, 2)])
            db.add_all([Order(order_id=i, account_id=i, symbol_code="SYN001", status="체결",
                              order_type="매수", price_type="시장가", quantity=10,
                              price=Decimal("100.25"), created_at=START, filled_at=START)
                        for i in (1, 2)])
            db.commit()
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/ai")
        def session():
            with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)
        self.addCleanup(self.client.close)
        self.observations = []
        @app.middleware("http")
        async def observe(request, call_next):
            response = await call_next(request)
            self.observations.append(getattr(request.state, "ai_execution", None))
            return response
        self.quote = AsyncMock(return_value={"current_price": "120.50"})
        self.news = AsyncMock(return_value=[NEWS])
        async def generate(base_url, bundle, *, profile, trace_id):
            data = wire(profile, trace_id)
            if not bundle.position.available:
                data["position_summary"] = []
            if not bundle.recent_news:
                data["recent_issues"] = []
            return service._parse_result(data, bundle, profile, trace_id)
        self.generate = AsyncMock(side_effect=generate)
        self.generator_patch = patch.object(routes, "request_portfolio_brief", self.generate)
        for patcher in (self.generator_patch,
                        patch.object(facts_service, "get_current_price", self.quote),
                        patch.object(facts_service, "get_news_by_symbol", self.news),
                        patch.object(routes.settings, "AI_SERVER_URL", "http://ai.test")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def get(self, account=1, symbol="SYN001", query=""):
        return self.client.get(f"/api/ai/portfolio-brief/{symbol}?account_id={account}{query}")

    def test_authenticated_success_with_default_luna_and_private_metadata(self):
        result = self.get()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json(), {"symbol_code": "SYN001", "security_name": "가온기술", **BODY})
        self.assertEqual(self.generate.await_count, 1)
        kwargs = self.generate.call_args.kwargs
        self.assertEqual(kwargs["profile"], "luna")
        self.assertEqual(kwargs["trace_id"], UUID(result.headers["X-Trace-ID"]))
        self.assertEqual(self.observations[-1].trace_id, kwargs["trace_id"])
        self.assertEqual(self.observations[-1].profile_id, "luna")
        for field in ("provider", "model", "account_id", "user_id", "ai", "usage", "AI_SERVER_URL"):
            self.assertNotIn(field, result.json())

    def test_explicit_luna_sol_and_unknown_profile(self):
        traces = []
        for profile in ("luna", "sol"):
            response = self.get(query=f"&profile={profile}")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.generate.call_args.kwargs["profile"], profile)
            traces.append(response.headers["X-Trace-ID"])
        self.assertNotEqual(*traces)
        self.generate.reset_mock()
        for profile in ("gemini", "gpt-6-luna", "LUNA", "", "unknown"):
            self.assertEqual(self.get(query=f"&profile={profile}").status_code, 422)
        self.generate.assert_not_awaited()

    def test_other_user_missing_account_and_unheld_symbol_stop_before_ai(self):
        for account, symbol in ((2, "SYN001"), (999, "SYN001"), (1, "MISSING")):
            self.assertEqual(self.get(account, symbol).status_code, 404)
        self.generate.assert_not_awaited()
        self.quote.assert_not_awaited()
        self.news.assert_not_awaited()

    def test_authentication_is_required(self):
        self.app.dependency_overrides.pop(get_current_user)
        self.assertIn(self.get().status_code, (401, 403))
        self.generate.assert_not_awaited()
        self.quote.assert_not_awaited()

    def test_invalid_account_and_blank_symbol_do_not_call_ai(self):
        for account in (0, -1, "invalid"):
            self.assertEqual(self.get(account).status_code, 422)
        self.assertEqual(self.get(symbol="%20").status_code, 400)
        self.generate.assert_not_awaited()

    def test_legacy_position_price_unavailable_and_news_empty_still_allow_ai(self):
        with self.Session() as db:
            db.get(Order, 1).filled_at = None
            db.commit()
        self.quote.side_effect = RuntimeError("private-marker")
        self.news.return_value = []
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        bundle = self.generate.call_args.args[1]
        self.assertFalse(bundle.position.available)
        self.assertEqual(bundle.trades, [])
        self.assertIsNone(bundle.holding.current_price)
        self.assertEqual(bundle.recent_news, [])
        self.assertEqual(response.json()["position_summary"], [])
        self.assertEqual(response.json()["recent_issues"], [])
        self.assertNotIn("private-marker", response.text)

    def test_core_fact_failures_preserve_status_and_never_call_ai(self):
        from fastapi import HTTPException
        for error, expected in ((HTTPException(409, "보유 정보가 일치하지 않습니다."), 409),
                                (RuntimeError("private-db-marker"), 500)):
            with patch.object(routes, "load_portfolio_facts", side_effect=error):
                response = self.get()
            self.assertEqual(response.status_code, expected)
            self.assertNotIn("private-db-marker", response.text)
        self.generate.assert_not_awaited()

    def test_safe_upstream_errors_keep_trace_and_never_fallback(self):
        for status in (502, 503):
            self.generate.side_effect = service.PortfolioBriefUnavailable(status)
            before = self.generate.await_count
            response = self.get(query="&profile=sol")
            self.assertEqual(response.status_code, status)
            UUID(response.headers["X-Trace-ID"])
            self.assertEqual(self.generate.await_count, before + 1)
            self.assertEqual(self.generate.call_args.kwargs["profile"], "sol")
            self.assertIsNone(self.observations[-1])
            self.assertNotIn("http://ai.test", response.text)

    def test_real_client_route_uses_projection_validates_response_and_no_global_provider_lookup(self):
        self.generator_patch.stop()
        calls = []
        def handle(request):
            calls.append(request)
            payload = json.loads(request.content)
            data = wire(payload["profile"], UUID(request.headers["X-Trace-ID"]))
            return httpx.Response(200, json=data)
        def factory(**options):
            return RealAsyncClient(transport=httpx.MockTransport(handle), **options)
        with patch.object(service.httpx, "AsyncClient", side_effect=factory):
            response = self.get(query="&profile=sol")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].url.path, "/brief/portfolio")
        payload = json.loads(calls[0].content)
        self.assertEqual(payload["profile"], "sol")
        self.assertNotIn("account_id", payload)
        self.assertNotIn("price_context", payload)
        self.assertEqual(self.observations[-1].model, "gpt-6.1-sol")

    def test_facts_endpoint_remains_independent_and_no_generation_cache(self):
        response = self.client.get("/api/ai/portfolio-brief/facts/SYN001?account_id=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["account_id"], 1)
        self.generate.assert_not_awaited()
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.get().status_code, 200)
        self.assertEqual(self.generate.await_count, 2)


    def test_real_transport_failures_never_escape_as_login_or_raw_provider_errors(self):
        self.generator_patch.stop()
        modes = ["timeout", "connection", "invalid-json", "invalid-body", "identity"]
        modes += [401, 403, 422, 429, 502, 503]
        for mode in modes:
            calls = []
            def handle(request):
                calls.append(request)
                if mode == "timeout":
                    raise httpx.ReadTimeout("private-marker")
                if mode == "connection":
                    raise httpx.ConnectError("private-marker")
                if mode == "invalid-json":
                    return httpx.Response(200, text="private-marker")
                if isinstance(mode, int):
                    return httpx.Response(mode, json={"detail": "private-marker"})
                data = wire("sol", UUID(request.headers["X-Trace-ID"]))
                if mode == "invalid-body":
                    data["current_status"] = "private-marker"
                else:
                    data["ai"]["profile_id"] = "luna"
                return httpx.Response(200, json=data)
            def factory(**options):
                return RealAsyncClient(transport=httpx.MockTransport(handle), **options)
            with self.subTest(mode=mode), patch.object(service.httpx, "AsyncClient", side_effect=factory):
                if mode == 422:
                    # Local validation is a user 422; it must never reach upstream.
                    invalid = self.get(query="&profile=invalid-profile")
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(calls, [])
                response = self.get(query="&profile=sol")
            expected = 503 if mode in ("connection", 503) else 502
            self.assertEqual(response.status_code, expected)
            self.assertEqual(len(calls), 1)
            self.assertEqual(json.loads(calls[0].content)["profile"], "sol")
            self.assertIsNone(self.observations[-1])
            self.assertNotIn("private-marker", response.text)
            self.assertNotIn("http://ai.test", response.text)
            UUID(response.headers["X-Trace-ID"])

    def test_real_transport_bad_diagnostics_keep_domain_and_ignore_raw_model_queries(self):
        self.generator_patch.stop()
        payloads = []
        def handle(request):
            payloads.append(json.loads(request.content))
            data = wire("luna", UUID(request.headers["X-Trace-ID"]))
            data["ai"].update(usage={"input_tokens": True}, latency_ms=None)
            return httpx.Response(200, json=data)
        def factory(**options):
            return RealAsyncClient(transport=httpx.MockTransport(handle), **options)
        with patch.object(service.httpx, "AsyncClient", side_effect=factory):
            response = self.get(query="&model=untrusted&provider=gemini&reasoning=high")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"symbol_code": "SYN001", "security_name": "가온기술", **BODY})
        self.assertEqual(payloads[0]["profile"], "luna")
        for key in ("model", "provider", "reasoning"):
            self.assertNotIn(key, payloads[0])
        self.assertIsNone(self.observations[-1])


    def test_entry_price_context_flows_from_facts_through_client_to_public_response(self):
        from datetime import date
        from app.schemas.portfolio_brief import EntryPriceContext
        context=EntryPriceContext(
            available=True,anchor_at=START,opening_price="100.25",prior_close_date=date(2026,10,20),
            prior_close="100",high_20_sessions="105",low_20_sessions="95",range_sessions=1,
            opening_vs_prior_close_pct="0.25",
        )
        self.generator_patch.stop()
        payloads=[]
        def handle(request):
            payloads.append(json.loads(request.content))
            data=wire("sol",UUID(request.headers["X-Trace-ID"]))
            data["entry_context"]=["포지션 시작 직전 확인된 종가는 100원입니다."]
            return httpx.Response(200,json=data)
        def factory(**options):
            return RealAsyncClient(transport=httpx.MockTransport(handle),**options)
        with patch.object(service.httpx,"AsyncClient",side_effect=factory),              patch.object(facts_service,"load_entry_price_context",AsyncMock(return_value=context)):
            response=self.get(query="&profile=sol")
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()["entry_context"],["포지션 시작 직전 확인된 종가는 100원입니다."])
        self.assertEqual(payloads[0]["entry_price_context"],context.model_dump(mode="json"))
        self.assertNotIn("historical_price_unavailable",payloads[0]["limitations"])
        self.assertNotIn("account_id",payloads[0])
        self.assertNotIn("user_id",payloads[0])
