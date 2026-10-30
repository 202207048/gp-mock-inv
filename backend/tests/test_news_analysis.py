import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine
from app.routers import ai as routes
from app.utils.deps import get_current_user
from app.services import news_analysis_service as service

RealClient = httpx.AsyncClient


class NewsAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        self.addCleanup(self.engine.dispose)
        service._cache.clear()
        self.addCleanup(service._cache.clear)
        self.calls = []
        self.failure = None
        self.clock = 100
        app = FastAPI()
        app.include_router(routes.router, prefix="/api/ai")
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(user_id=1)
        self.app = app
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        patches = [patch.object(service.settings, "AI_SERVER_URL", "http://ai.test"),
                   patch.object(service.settings, "AI_SERVICE_TOKEN", SecretStr("fixture-service")),
                   patch.object(service.time, "monotonic", lambda:self.clock),
                   patch.object(service.httpx, "AsyncClient", side_effect=lambda **kw:RealClient(transport=httpx.MockTransport(self.handler),**kw))]
        for p in patches:
            p.start(); self.addCleanup(p.stop)

    def handler(self, req):
        self.calls.append(req)
        self.assertEqual(req.headers["Authorization"], "Bearer fixture-service")
        self.assertEqual(req.url.path, "/analyze/news")
        if self.failure == "timeout": raise httpx.ReadTimeout("fixture",request=req)
        if self.failure == "connect": raise httpx.ConnectError("fixture",request=req)
        if isinstance(self.failure,int):return httpx.Response(self.failure,json={})
        if self.failure == "json":return httpx.Response(200,text="invalid")
        if self.failure == "schema":return httpx.Response(200,json={"articles":[]})
        data=json.loads(req.content)
        self.assertEqual(data["profile"],"luna")
        self.assertFalse(data["include_ai_metadata"])
        rows=[{"article_id":a["article_id"],"sentiment":{"label":"NEUTRAL","confidence":.8},
               "importance":{"score":.5,"label":"MEDIUM"},"provider":"private"} for a in data["articles"]]
        return httpx.Response(200,json={"articles":list(reversed(rows)),"ai":{"model":"private"},"usage":{}})

    def post(self, articles):return self.client.post("/api/ai/news-analysis",json={"articles":articles})
    def article(self, identity="a",title="뉴스"):return {"article_id":identity,"title":title}

    def test_authenticated_success_and_metadata_projection(self):
        r=self.post([self.article()]);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(set(r.json()),{"articles"})
        self.assertEqual(set(r.json()["articles"][0]),{"article_id","sentiment","importance"})
        self.assertNotIn("private",r.text);self.assertEqual(len(self.calls),1)
        def denied():raise HTTPException(401)
        self.app.dependency_overrides[get_current_user]=denied
        self.assertEqual(self.post([self.article()]).status_code,401)
        self.assertEqual(len(self.calls),1)

    def test_hits_partial_misses_ids_and_order(self):
        self.post([self.article("old","같은 뉴스")])
        r=self.post([self.article("new","새 뉴스"),self.article("changed","  같은   뉴스 ")])
        self.assertEqual([x["article_id"] for x in r.json()["articles"]],["new","changed"])
        self.assertEqual(len(json.loads(self.calls[-1].content)["articles"]),1)
        self.assertEqual(len(self.calls),2)
        self.post([self.article("index","같은 뉴스")]);self.assertEqual(len(self.calls),2)
        r=self.post([self.article("x","새 뉴스"),self.article("y","같은 뉴스")])
        self.assertEqual([x["article_id"] for x in r.json()["articles"]],["x","y"])
        self.assertEqual(len(self.calls),2)

    def test_batch_ids_and_expiration(self):
        r=self.post([self.article("a","A"),self.article("b","B")])
        self.assertEqual([x["article_id"] for x in r.json()["articles"]],["a","b"])
        self.clock+=1799;self.post([self.article("c","A")]);self.assertEqual(len(self.calls),1)
        self.clock+=1;self.post([self.article("d","A")]);self.assertEqual(len(self.calls),2)

    def test_failure_mapping_and_no_failure_cache(self):
        for failure,status in [(401,502),(403,502),(422,502),(503,503),(500,502),("timeout",502),("connect",503),("json",502),("schema",502)]:
            service._cache.clear();self.failure=failure
            self.assertEqual(self.post([self.article()]).status_code,status)
            self.assertFalse(service._cache)
            before=len(self.calls);self.failure=None
            self.assertEqual(self.post([self.article()]).status_code,200)
            self.assertEqual(len(self.calls),before+1)

    def test_invalid_input_and_configuration(self):
        for articles in ([],[self.article(str(i)) for i in range(11)],[{"title":"뉴스"}],[self.article(title=" ")],[self.article(title="a"*2001)],[self.article(),self.article()]):
            self.assertEqual(self.post(articles).status_code,422)
        self.assertFalse(self.calls)
        with patch.object(service.settings,"AI_SERVICE_TOKEN",SecretStr("")):
            self.assertEqual(self.post([self.article()]).status_code,503)
        with patch.object(service.settings,"AI_SERVER_URL","bad://invalid"):
            self.assertEqual(self.post([self.article()]).status_code,503)
        self.assertFalse(self.calls)
