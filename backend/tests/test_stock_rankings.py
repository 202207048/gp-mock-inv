import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from app.services import kis_service as service
from app.routers.securities import router


def stock(**fields):
    return {"mksc_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
            "stck_prpr": "70000", "prdy_ctrt": "-2.15", "acml_vol": "123456",
            "acml_tr_pbmn": "8641920000", **fields}


class StockRankingsTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, category, output, limit=30):
        response = httpx.Response(200, request=httpx.Request("GET", "https://example.test"),
                                  json={"rt_cd": "0", "output": output})
        with patch.object(service.settings, "KIS_REAL_APP_KEY", "fixture-key"), \
             patch.object(service, "get_real_kis_token", new=AsyncMock(return_value="fixture-token")), \
             patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=response)) as fetch:
            rows = await service.get_stock_ranking(category, limit)
            return rows, fetch.call_args

    async def test_decline_requests_downward_ranking_and_retains_provider_order(self):
        rows, call = await self.request("decline", [stock(prdy_ctrt="-29.99"), stock(mksc_shrn_iscd="000660", prdy_ctrt="-25.1")])
        self.assertTrue(call.args[0].endswith("/ranking/fluctuation"))
        self.assertEqual(call.kwargs["params"]["FID_RANK_SORT_CLS_CODE"], "1")
        self.assertEqual([row["change_rate"] for row in rows], [-29.99, -25.1])
        self.assertEqual([row["rank"] for row in rows], [1, 2])

    async def test_market_cap_is_in_won_and_absent_trade_amount_stays_missing(self):
        row = stock(lstn_stcn="5969782550", stck_avls="4178848")
        del row["acml_tr_pbmn"]
        rows, call = await self.request("market_cap", [row])
        self.assertTrue(call.args[0].endswith("/ranking/market-cap"))
        self.assertEqual(call.kwargs["headers"]["tr_id"], "FHPST01740000")
        self.assertEqual(call.kwargs["params"]["FID_COND_SCR_DIV_CODE"], "20174")
        self.assertEqual(rows[0]["market_cap"], 417884778500000)
        self.assertIsNone(rows[0]["amount"])

    async def test_volume_surge_uses_increase_ranking_and_preserves_zero(self):
        rows, call = await self.request("volume_surge", [stock(vol_inrt="1234.56"), stock(vol_inrt="0"), stock(vol_inrt="NaN")])
        self.assertTrue(call.args[0].endswith("/quotations/volume-rank"))
        self.assertEqual(call.kwargs["params"]["FID_BLNG_CLS_CODE"], "1")
        self.assertEqual([row["volume_increase_rate"] for row in rows], [1234.56, 0, None])

    async def test_existing_categories_keep_their_sort_parameters(self):
        for category, field, value in [("amount", "FID_BLNG_CLS_CODE", "3"), ("volume", "FID_BLNG_CLS_CODE", "0"), ("change", "FID_RANK_SORT_CLS_CODE", "0")]:
            rows, call = await self.request(category, [stock()] * 35)
            self.assertEqual(call.kwargs["params"][field], value)
            self.assertEqual(len(rows), 30)
            service._assert_read_only(call.kwargs["headers"]["tr_id"])

    async def test_invalid_and_missing_values_do_not_become_fabricated_numbers(self):
        rows, _ = await self.request("market_cap", [None, {}, stock(prdy_ctrt="Infinity", lstn_stcn="", acml_vol="0")])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["market_cap"])
        self.assertIsNone(rows[0]["change_rate"])
        self.assertEqual(rows[0]["volume"], 0)
        with self.assertRaises(ValueError):
            await service.get_stock_ranking("not-supported")

    async def test_public_routes_accept_new_types_and_validate_limit(self):
        app = FastAPI()
        app.include_router(router, prefix="/api/stocks")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            with patch("app.routers.securities.get_stock_ranking", new=AsyncMock(return_value=[{"rank": 1}])) as fetch:
                for category in ("decline", "market_cap", "volume_surge"):
                    response = await client.get(f"/api/stocks/ranking/{category}?limit=30")
                    self.assertEqual(response.status_code, 200)
                    fetch.assert_awaited_with(category, 30)
                self.assertEqual((await client.get("/api/stocks/ranking/unknown")).status_code, 400)
                self.assertEqual((await client.get("/api/stocks/ranking/market_cap?limit=31")).status_code, 422)
