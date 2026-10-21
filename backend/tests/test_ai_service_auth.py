"""Service header/config checks, with no DB or external transport."""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import SecretStr

from app.config import settings
from app.routers import ai
from app.services.ai_service_auth import AIServiceNotConfigured, service_headers
from app.services import news_summary_service as news
from app.services import portfolio_brief_client as portfolio
from uuid import uuid4

DUMMY = "unit-test-service-token"


class ServiceAuthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        patcher = patch.object(settings, "AI_SERVICE_TOKEN", SecretStr(DUMMY))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_token_config_does_not_expose_value(self):
        self.assertNotIn(DUMMY, repr(settings))
        self.assertNotIn(DUMMY, str(settings))
        self.assertNotIn(DUMMY, settings.model_dump_json())
        self.assertEqual(service_headers(), {"Authorization": "Bearer " + DUMMY})

    async def test_missing_or_invalid_token_never_constructs_http_client(self):
        for token in ["", "  ", "invalid\nheader", "invalid test token", "비ASCII"]:
            with self.subTest(token_config_valid=False), patch.object(settings, "AI_SERVICE_TOKEN", SecretStr(token)), patch.object(httpx, "AsyncClient") as client:
                with self.assertRaises(AIServiceNotConfigured):
                    service_headers()
                with self.assertRaises(news.NewsSummaryUnavailable) as caught:
                    await news.get_summary_provider("http://ai.test")
                self.assertEqual(str(caught.exception), "Stock-news summary unavailable")
                with self.assertRaises(news.NewsSummaryUnavailable):
                    await news.summarize_stock_news("http://ai.test", {})
                with self.assertRaises(portfolio.PortfolioBriefUnavailable) as caught:
                    await portfolio.request_portfolio_brief("http://ai.test", None, trace_id=uuid4())
                self.assertEqual(caught.exception.status_code, 503)
                with self.assertRaises(ai.HTTPException) as caught:
                    await ai._call_ai("/pattern", {"user_id": 1})
                self.assertEqual(caught.exception.status_code, 503)
                self.assertNotIn(DUMMY, str(caught.exception))
                client.assert_not_called()

    async def test_common_proxy_get_and_post_use_service_header(self):
        response = httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", "http://ai.test"))
        with patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=response)) as get, patch.object(httpx.AsyncClient, "post", AsyncMock(return_value=response)) as post:
            self.assertEqual(await ai._call_ai("/pattern"), {"ok": True})
            self.assertEqual(await ai._call_ai("/recommend", {"user_id": 1}), {"ok": True})
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer " + DUMMY})
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer " + DUMMY})

    async def test_upstream_service_auth_errors_do_not_become_user_login_errors(self):
        for status in [401, 403, 503]:
            response = httpx.Response(status, json={"detail": DUMMY}, request=httpx.Request("GET", "http://ai.test"))
            with self.subTest(status=status), patch.object(httpx.AsyncClient, "get", AsyncMock(return_value=response)):
                with self.assertRaises(ai.HTTPException) as caught:
                    await ai._call_ai("/pattern")
                self.assertEqual(caught.exception.status_code, 503 if status == 503 else 502)
                self.assertNotIn(DUMMY, str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
