"""Public Naver search ranking with a shared, short-lived cache."""
import asyncio
import math
import re
import time

import httpx
from fastapi import HTTPException

URL = 'https://stock.naver.com/api/domestic/market/stock/default'
_lock = asyncio.Lock()
_rows = []
_expires = 0.0


def parse_ranking(payload):
    if not isinstance(payload, list):
        raise ValueError('Invalid ranking payload')
    rows, seen = [], set()
    for position, item in enumerate(payload, 1):
        if not isinstance(item, dict):
            continue
        code, name = item.get('itemcode'), item.get('itemname')
        if not isinstance(code, str) or not re.fullmatch(r'[0-9A-Z]{6}', code):
            continue
        if code in seen or not isinstance(name, str) or not name.strip():
            continue
        seen.add(code)
        try:
            change_rate = float(str(item.get('prevChangeRate', '')).replace(',', ''))
            if not math.isfinite(change_rate):
                change_rate = None
        except (TypeError, ValueError):
            change_rate = None
        rows.append({'rank': position, 'symbol_code': code, 'name': name.strip(),
                     'change_rate': change_rate})
    if not rows:
        raise ValueError('Empty ranking')
    return rows


async def get_popular_stocks():
    global _rows, _expires
    async with _lock:
        if time.monotonic() >= _expires:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    response = await client.get(URL, params={
                        'tradeType': 'KRX', 'marketType': 'ALL',
                        'orderType': 'searchTop', 'startIdx': 0, 'pageSize': 10,
                    })
                    response.raise_for_status()
                    _rows = parse_ranking(response.json())
                _expires = time.monotonic() + 60
            except (httpx.HTTPError, ValueError):
                # Avoid retry storms and never present old ranks as current.
                _rows = []
                _expires = time.monotonic() + 15
        if not _rows:
            raise HTTPException(503, '인기 검색어를 불러올 수 없습니다. 잠시 후 다시 시도해 주세요.')
        return _rows
