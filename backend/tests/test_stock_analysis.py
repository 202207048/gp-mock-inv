import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from app.services import stock_analysis as service
from app.market_data_preview import app


def finance(code='005930'):
    return {'itemCode': code, 'financeInfo': {
        'trTitleList': [{'key': '202612', 'isConsensus': 'Y'}, {'key': '202512', 'isConsensus': 'N'}],
        'rowList': [{'title': '매출액', 'columns': {'202512': {'value': '1,234'}, '202612': {'value': '2,000'}}},
                    {'title': '영업이익', 'columns': {'202512': {'value': '-23'}, '202612': {'value': '-'}}},
                    {'title': '부채비율', 'columns': {'202512': {'value': '0'}}}]},
        'corporationSummary': {'comment1': '기업의 실제 사업 개요'}}


def integration():
    return {'itemCode': '005930', 'dealTrendInfos': [
        {'itemCode': '005930', 'bizdate': '20261109', 'individualPureBuyQuant': '+1,000',
         'foreignerPureBuyQuant': '-999', 'organPureBuyQuant': '0', 'foreignerHoldRatio': '45.5%'},
        {'itemCode': '000660', 'bizdate': '20261109', 'individualPureBuyQuant': '999'},
        {'itemCode': '005930', 'bizdate': '20260231'}],
        'consensusInfo': {'itemCode': '005930', 'recommMean': '4.00', 'priceTargetMean': '100,000', 'createDate': '2026-11-09'},
        'researches': [{'id': 12, 'cd': '005930', 'tit': '리포트 제목', 'bnm': '증권사', 'wdt': '20261109'},
                       {'id': '../bad', 'cd': '005930', 'tit': 'bad'}]}


class AnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        service._cache.clear()

    def test_finance_preserves_estimates_missing_zero_and_negative(self):
        result = service.parse_finance(finance(), '005930')
        self.assertEqual([p['key'] for p in result['periods']], ['202512', '202612'])
        self.assertFalse(result['periods'][0]['estimate'])
        self.assertTrue(result['periods'][1]['estimate'])
        self.assertEqual(result['rows'][0]['unit'], '억원')
        self.assertEqual(result['rows'][0]['values']['202512'], 1234)
        self.assertEqual(result['rows'][1]['values']['202512'], -23)
        self.assertIsNone(result['rows'][1]['values']['202612'])
        self.assertEqual(result['rows'][2]['values']['202512'], 0)

    def test_investors_reject_other_stock_invalid_dates_and_keep_signs(self):
        rows = service.parse_investors(integration(), '005930')
        self.assertEqual(len(rows), 1)
        self.assertEqual([rows[0][key] for key in ['individual', 'foreign', 'institution']], [1000, -999, 0])
        self.assertEqual(rows[0]['foreign_ratio'], 45.5)
        self.assertIsNone(rows[0]['close'])

    def test_opinions_have_valid_report_links_and_no_fabricated_values(self):
        data = service.parse_opinions(integration(), '005930')
        self.assertEqual(data['score'], 4)
        self.assertEqual(data['target_price'], 100000)
        self.assertEqual(len(data['reports']), 1)
        self.assertTrue(data['reports'][0]['url'].endswith('nid=12'))
        self.assertIsNone(service.parse_opinions({'itemCode': '005930'}, '005930')['score'])

    def test_partial_failure_and_mismatched_symbol(self):
        result = service.parse_analysis('005930', integration(), RuntimeError(), finance())
        self.assertIsNone(result['annual'])
        self.assertIsNotNone(result['quarter'])
        self.assertEqual(result['overview'], ['기업의 실제 사업 개요'])
        with self.assertRaises(ValueError):
            service.parse_analysis('000660', integration(), finance(), RuntimeError())
        self.assertEqual(service.parse_finance({'itemCode': '005930', 'financeInfo': None}, '005930'), {'periods': [], 'rows': []})

    async def test_public_endpoint_cache_and_validation(self):
        async def get(url):
            return httpx.Response(200, request=httpx.Request('GET', url), json=integration() if url.endswith('integration') else finance())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch.object(service.httpx.AsyncClient, 'get', new=AsyncMock(side_effect=get)) as fetch:
                # Invoke service directly because patching client.get also patches the ASGI client.
                first = await service.get_stock_analysis('005930')
                self.assertEqual(await service.get_stock_analysis('005930'), first)
                self.assertEqual(fetch.await_count, 3)
            response = await client.get('/api/stocks/005930/analysis')
            self.assertEqual(response.status_code, 200)
            self.assertEqual((await client.get('/api/stocks/invalid/analysis')).status_code, 422)

    async def test_total_outage_not_cached(self):
        with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=httpx.ConnectError('offline'))):
            with self.assertRaises(HTTPException) as error:
                await service.get_stock_analysis('005930')
            self.assertEqual(error.exception.status_code, 503)
            self.assertFalse(service._cache)
