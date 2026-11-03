import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from app.routers import ai as routes
from app.utils.deps import get_current_user
from app.database import get_db
from app.services import term_detect_service as service

RealClient = httpx.AsyncClient
class FakeDb:
    def __init__(self, stored):
        self.stored = stored

    def query(self, column, *more):
        stored = self.stored
        if column.key == "name":
            return SimpleNamespace(all=lambda: [(name,) for name in stored])
        class Query:
            def filter(self, condition):
                code = stored.get(condition.right.value)
                return SimpleNamespace(first=lambda: (code, "KOSPI") if code else None)
        return Query()


BODY = {"text": "호재가 겹치며 행사는 성료됐다.", "known": ["거래량"]}


class TermDetectTests(unittest.TestCase):
    def setUp(self):
        service._cache.clear(); service._calls.clear()
        self.addCleanup(service._cache.clear); self.addCleanup(service._calls.clear)
        self.calls = []
        self.failure = None
        self.clock = 100
        self.user = 1
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/ai")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=self.user)
        self.stored = {"삼성전자": "005930"}
        app.dependency_overrides[get_db] = lambda: FakeDb(self.stored)
        self.terms = [{"term": "호재", "tier": "invest", "meaning": "좋은 소식이에요."},
                      {"term": "없는말", "tier": "general", "meaning": "글에 없어요."}]
        self.searched = []
        self.sent = []
        service._names.update(at=None, names=[])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        patches = [patch.object(service.settings, "AI_SERVER_URL", "http://ai.test"),
                   patch.object(service.settings, "AI_SERVICE_TOKEN", SecretStr("fixture-service")),
                   patch.object(service.time, "monotonic", lambda: self.clock),
                   patch.object(service, "search_listed_stocks", self.search),
                   patch.object(service.httpx, "AsyncClient", side_effect=lambda **kw: RealClient(transport=httpx.MockTransport(self.handler), **kw))]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def handler(self, req):
        self.calls.append(req)
        self.assertEqual(req.headers["Authorization"], "Bearer fixture-service")
        self.assertEqual(req.url.path, "/terms/detect")
        if isinstance(self.failure, int):
            return httpx.Response(self.failure, json={})
        sent = json.loads(req.content)
        self.assertEqual(set(sent), {"text", "known", "companies"})
        self.sent.append(sent["companies"])
        return httpx.Response(200, json={"terms": self.terms})

    async def search(self, keyword, limit):
        self.searched.append(keyword)
        return [{"symbol_code": "000660", "name": "SK하이닉스"}, {"symbol_code": "035420", "name": "NAVER"}]

    def post(self, body=BODY):
        return self.client.post("/api/ai/term-detect", json=body)

    def test_relays_text_and_drops_words_not_in_text(self):
        res = self.post()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json(), {"terms": [{"term": "호재", "tier": "invest", "meaning": "좋은 소식이에요.", "basic": False, "basis": "knowledge",
                                                   "listing": "", "index": "", "sector": "", "size": "", "listing_checked": False, "symbol_code": None}]})

    def test_company_names_link_only_exact_listed_stocks(self):
        body = {"text": "삼성전자와 SK 하이닉스, 하이닉스와 엔비디아", "known": []}
        self.terms = [{"term": name, "tier": "company", "meaning": "반도체 회사예요."} for name in ("삼성전자", "SK 하이닉스", "하이닉스", "엔비디아")]
        res = self.post(body)
        self.assertEqual([(t["term"], t["symbol_code"]) for t in res.json()["terms"]],
                         [("삼성전자", "005930"), ("SK 하이닉스", "000660"), ("하이닉스", "000660"), ("엔비디아", None)])
        self.assertNotIn("삼성전자", self.searched)
        self.assertEqual(res.json()["terms"][0]["listing"], "코스피")

    def test_listed_names_and_aliases_are_sent_as_hints_and_linked(self):
        self.stored.update({"삼성생명": "032830", "삼성": "000000"})
        body = {"text": "삼성생명이 네이버 지분을, 삼성생명은 다시", "known": []}
        self.terms = [{"term": "네이버", "tier": "company", "meaning": "검색 포털 회사예요."}]
        res = self.post(body)
        self.assertEqual(self.sent[-1], ["삼성생명", "네이버"])
        self.assertIn("NAVER", self.searched)
        self.assertEqual(res.json()["terms"][0]["symbol_code"], "035420")
        self.assertEqual((res.json()["terms"][0]["listing"], res.json()["terms"][0]["listing_checked"]), ("국내 상장", True))
        filled = res.json()["terms"][1]
        self.assertEqual((filled["term"], filled["basis"], filled["symbol_code"], filled["listing"]), ("삼성생명", "data", "032830", "코스피"))
        self.assertIn("코스피에 상장된 국내 기업", filled["meaning"])

    def test_same_text_is_served_from_cache(self):
        self.post(); self.post()
        self.assertEqual(len(self.calls), 1)

    def test_rate_limit_counts_only_new_texts_per_user(self):
        for i in range(service.RATE_LIMIT):
            self.assertEqual(self.post({**BODY, "text": BODY["text"] + str(i)}).status_code, 200)
        self.assertEqual(self.post({**BODY, "text": "새 글"}).status_code, 429)
        self.assertEqual(self.post({**BODY, "text": BODY["text"] + "0"}).status_code, 200)
        self.user = 2
        self.assertEqual(self.post({**BODY, "text": "새 글"}).status_code, 200)
        self.user = 1; self.clock += service.RATE_WINDOW
        self.assertEqual(self.post({**BODY, "text": "새 글"}).status_code, 200)

    def test_errors_and_validation(self):
        self.failure = 503
        self.assertEqual(self.post().status_code, 503)
        self.failure = 500
        self.assertEqual(self.post().status_code, 502)
        self.assertEqual(self.post({"text": "가" * 3001}).status_code, 422)
        with patch.object(service.settings, "AI_SERVICE_TOKEN", SecretStr("")):
            service._cache.clear()
            self.assertEqual(self.post().status_code, 503)

    def test_cached_requests_do_not_consume_rate_budget(self):
        self.assertEqual(self.post().status_code, 200)
        for _ in range(4):
            self.assertEqual(self.post().status_code, 200)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(service._calls[self.user]), 1)
        for i in range(14):
            self.assertEqual(self.post({"text": f"새 글 {i}", "known": []}).status_code, 200)
        self.assertEqual(self.post({"text": "열여섯 번째", "known": []}).status_code, 429)
        self.assertEqual(len(self.calls), 15)

    def test_blank_meanings_removed_and_stock_lookup_failure_is_safe(self):
        self.terms = [{"term": "호재", "tier": "invest", "meaning": ""},
                      {"term": "하이닉스", "tier": "company", "meaning": "반도체 기업"}]
        with patch.object(service, "listed_names_in", side_effect=RuntimeError("offline")), \
             patch.object(service, "search_listed_stocks", side_effect=RuntimeError("offline")):
            response = self.post({"text": "호재 하이닉스", "known": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([t["term"] for t in response.json()["terms"]], ["하이닉스"])
        self.assertIsNone(response.json()["terms"][0]["symbol_code"])
