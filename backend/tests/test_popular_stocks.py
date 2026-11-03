import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from app.services import popular_stocks as service


class PopularStocksTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        service._rows = []
        service._expires = 0
        service._lock = asyncio.Lock()

    def test_preserves_provider_order_and_filters_invalid_rows(self):
        rows = service.parse_ranking([
            {'itemcode': '005930', 'itemname': '삼성전자'},
            {'itemcode': 'bad', 'itemname': 'invalid'},
            {'itemcode': '000660', 'itemname': 'SK하이닉스'},
            {'itemcode': '005930', 'itemname': 'duplicate'},
        ])
        self.assertEqual([row['rank'] for row in rows], [1, 3])
        self.assertEqual(rows[0]['symbol_code'], '005930')
        for payload in ({}, [], [None]):
            with self.assertRaises(ValueError):
                service.parse_ranking(payload)

    async def test_concurrent_reads_share_upstream_request(self):
        response = httpx.Response(200, request=httpx.Request('GET', service.URL),
            json=[{'itemcode': '005930', 'itemname': '삼성전자'}])
        with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(return_value=response)) as fetch:
            first, second = await asyncio.gather(service.get_popular_stocks(), service.get_popular_stocks())
            self.assertEqual(first, second)
            fetch.assert_awaited_once()
            self.assertEqual(fetch.call_args.kwargs['params']['orderType'], 'searchTop')

    async def test_failure_clears_old_ranking_and_backs_off(self):
        service._rows = [{'rank': 1, 'symbol_code': '005930', 'name': 'old'}]
        with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=httpx.ConnectError('offline'))) as fetch:
            for _ in range(2):
                with self.assertRaises(HTTPException) as error:
                    await service.get_popular_stocks()
                self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(service._rows, [])
            fetch.assert_awaited_once()
