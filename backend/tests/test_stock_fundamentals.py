import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from app.services import stock_fundamentals as service


def fixtures():
    return ({'itemCode': '005930', 'totalInfos': [
        {'code': 'marketValue', 'value': '123조 4,567억'},
        {'code': 'dividendYieldRatio', 'value': '0.00%', 'valueDesc': '2025.12.'},
        {'code': 'per', 'value': 'N/A'}, {'code': 'pbr', 'value': '1.23배'},
    ]}, {'itemCode': '005930', 'financeInfo': {
        'trTitleList': [{'key': '202612', 'title': '2026.12.', 'isConsensus': 'Y'},
                        {'key': '202512', 'title': '2025.12.', 'isConsensus': 'N'}],
        'rowList': [{'title': 'ROE', 'columns': {'202512': {'value': '-2.50'}, '202612': {'value': '99'}}},
                    {'title': '매출액', 'columns': {'202512': {'value': '100,000'}}}]
    }})


class FundamentalsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        service._cache.clear()

    def test_units_zero_missing_negative_and_consensus(self):
        data = service.parse_fundamentals('005930', *fixtures())
        metrics = data['metrics']
        self.assertEqual(metrics['market_cap']['value'], 123456700000000)
        self.assertEqual(metrics['dividend_yield']['value'], 0)
        self.assertIsNone(metrics['per']['value'])
        self.assertEqual(metrics['pbr']['value'], 1.23)
        self.assertEqual(metrics['roe'], {'value': -2.5, 'period': '2025.12.'})
        self.assertAlmostEqual(metrics['psr']['value'], 12.34567)
        for value in ('N/A', '', '-', 'NaN', 'Infinity', None):
            self.assertIsNone(service.number(value))
        self.assertEqual(service.number(0), 0)

    def test_partial_response_and_zero_sales(self):
        integration, annual = fixtures()
        data = service.parse_fundamentals('005930', integration, RuntimeError())
        self.assertTrue(data['annual_unavailable'])
        self.assertIsNone(data['metrics']['roe']['value'])
        self.assertIsNotNone(data['metrics']['market_cap']['value'])
        annual['financeInfo']['rowList'][1]['columns']['202512']['value'] = '0'
        self.assertIsNone(service.parse_fundamentals('005930', integration, annual)['metrics']['psr']['value'])
        with self.assertRaises(ValueError):
            service.parse_fundamentals('000660', integration, annual)

    async def test_fetch_cache_and_invalid_symbol(self):
        integration, annual = fixtures()
        async def get(url):
            return httpx.Response(200, request=httpx.Request('GET', url), json=annual if url.endswith('/annual') else integration)
        with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=get)) as fetch:
            first = await service.get_stock_fundamentals('005930')
            self.assertEqual(await service.get_stock_fundamentals('005930'), first)
            self.assertEqual(fetch.await_count, 2)
            with self.assertRaises(HTTPException) as error:
                await service.get_stock_fundamentals('../invalid')
            self.assertEqual(error.exception.status_code, 422)

    async def test_provider_failure_is_retryable(self):
        with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=httpx.ConnectError('offline'))):
            with self.assertRaises(HTTPException) as error:
                await service.get_stock_fundamentals('005930')
            self.assertEqual(error.exception.status_code, 503)
            self.assertNotIn('005930', service._cache)
