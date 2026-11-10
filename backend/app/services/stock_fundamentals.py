"""Public investment metrics. Annual figures exclude analyst consensus.

Naver finance/annual sales are in KRW 100 million; integration marketValue
contains explicit Korean amount units. Never infer ROE from PBR/PER.
"""
import asyncio
import math
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException

_cache = {}
_TTL = 300
_FIELDS = {
    'market_cap': 'marketValue', 'dividend_yield': 'dividendYieldRatio',
    'pbr': 'pbr', 'per': 'per', 'eps': 'eps', 'bps': 'bps',
    'dividend_per_share': 'dividend', 'foreign_ownership': 'foreignRate',
}


def number(value):
    text = str(value if value is not None else '').replace(',', '').strip()
    text = re.sub(r'(배|원|%)$', '', text).strip()
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return None
    result = float(text)
    return result if math.isfinite(result) else None


def market_cap(value):
    text = str(value or '').replace(',', '').replace(' ', '')
    match = re.fullmatch(r'(?:(\d+(?:\.\d+)?)조)?(?:(\d+(?:\.\d+)?)억)?', text)
    if not match or not any(match.groups()):
        return None
    return round(float(match[1] or 0) * 1_000_000_000_000 + float(match[2] or 0) * 100_000_000)


def parse_fundamentals(code, integration, annual):
    if not isinstance(integration, dict) or integration.get('itemCode') != code or not isinstance(integration.get('totalInfos'), list):
        raise ValueError('Invalid stock metrics')
    infos = {row['code']: row for row in integration['totalInfos'] if isinstance(row, dict) and 'code' in row}
    metrics = {}
    for key, field in _FIELDS.items():
        row = infos.get(field, {})
        metrics[key] = {'value': (market_cap if key == 'market_cap' else number)(row.get('value')),
                        'period': row.get('valueDesc') or None}
    finance = annual.get('financeInfo') if isinstance(annual, dict) and annual.get('itemCode') == code else None
    finance = finance if isinstance(finance, dict) and isinstance(finance.get('trTitleList'), list) and isinstance(finance.get('rowList'), list) else {}
    periods = [p for p in finance.get('trTitleList', []) if isinstance(p, dict)
               and p.get('isConsensus') == 'N' and re.fullmatch(r'\d{6}', str(p.get('key', '')))]
    latest = max(periods, key=lambda p: p['key']) if periods else None
    rows = {row.get('title'): row for row in finance.get('rowList', []) if isinstance(row, dict)}

    def annual_value(title):
        columns = rows.get(title, {}).get('columns') or {}
        cell = columns.get(latest['key']) if latest else None
        return number(cell.get('value')) if isinstance(cell, dict) else None

    period = latest.get('title') if latest else None
    metrics['roe'] = {'value': annual_value('ROE'), 'period': period}
    sales = annual_value('매출액')
    cap = metrics['market_cap']['value']
    metrics['psr'] = {'value': cap / (sales * 100_000_000) if cap is not None and sales is not None and sales > 0 else None,
                      'period': period, 'calculated': True}
    return {'symbol_code': code, 'metrics': metrics, 'source': '네이버 증권',
            'source_url': f'https://m.stock.naver.com/domestic/stock/{code}/total',
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'annual_unavailable': finance == {}}


async def get_stock_fundamentals(code):
    if not re.fullmatch(r'[A-Z0-9]{6}', code):
        raise HTTPException(422, '종목코드는 6자리 영문 대문자 또는 숫자여야 합니다.')
    cached = _cache.get(code)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    async def fetch(client, path):
        response = await client.get(f'https://m.stock.naver.com/api/stock/{code}/{path}')
        response.raise_for_status()
        return response.json()
    try:
        async with httpx.AsyncClient(timeout=10, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            integration, annual = await asyncio.gather(fetch(client, 'integration'), fetch(client, 'finance/annual'), return_exceptions=True)
        if isinstance(integration, Exception):
            raise ValueError('Investment metrics unavailable') from integration
        result = parse_fundamentals(code, integration, annual)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise HTTPException(503, '투자 지표를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.') from error
    if len(_cache) >= 512:
        _cache.pop(next(iter(_cache)))
    _cache[code] = (time.monotonic() + (30 if result['annual_unavailable'] else _TTL), result)
    return result
