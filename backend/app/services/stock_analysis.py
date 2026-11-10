"""Read-only company analysis from Naver's published stock pages."""
import asyncio
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import HTTPException
from app.services.stock_fundamentals import number

_cache = {}
FINANCE_FIELDS = {
    '매출액': ('revenue', '억원'), '영업이익': ('operating_profit', '억원'),
    '당기순이익': ('net_income', '억원'), '지배주주순이익': ('owners_income', '억원'),
    '영업이익률': ('operating_margin', '%'), '순이익률': ('net_margin', '%'),
    'ROE': ('roe', '%'), '부채비율': ('debt_ratio', '%'),
    '당좌비율': ('quick_ratio', '%'), '유보율': ('reserve_ratio', '%'),
    'EPS': ('eps', '원'), 'BPS': ('bps', '원'), 'PER': ('per', '배'),
    'PBR': ('pbr', '배'), '주당배당금': ('dividend', '원'),
}


def date(value):
    raw = str(value or '').replace('-', '')
    try:
        return datetime.strptime(raw, '%Y%m%d').date().isoformat() if len(raw) == 8 else None
    except ValueError:
        return None


def valid_payload(payload, code):
    return isinstance(payload, dict) and payload.get('itemCode') == code


def parse_finance(payload, code):
    if not valid_payload(payload, code):
        return None
    info = payload.get('financeInfo')
    if info is None:
        return {'periods': [], 'rows': []}
    if not isinstance(info, dict) or not isinstance(info.get('trTitleList'), list) or not isinstance(info.get('rowList'), list):
        return None
    periods = []
    for p in info['trTitleList']:
        if not isinstance(p, dict) or p.get('isConsensus') not in ('Y', 'N'):
            continue
        key = str(p.get('key', ''))
        if not re.fullmatch(r'\d{4}(?:0[1-9]|1[0-2])', key):
            continue
        periods.append({'key': key, 'label': f'{key[:4]}.{key[4:]}', 'estimate': p['isConsensus'] == 'Y'})
    periods = sorted({p['key']: p for p in periods}.values(), key=lambda p: p['key'])[-8:]
    rows = []
    for row in info['rowList']:
        if not isinstance(row, dict) or row.get('title') not in FINANCE_FIELDS:
            continue
        key, unit = FINANCE_FIELDS[row['title']]
        columns = row.get('columns')
        if not isinstance(columns, dict):
            columns = {}
        values = {p['key']: number(columns[p['key']].get('value'))
                  if isinstance(columns.get(p['key']), dict) else None for p in periods}
        rows.append({'key': key, 'label': row['title'], 'unit': unit, 'values': values})
    return {'periods': periods, 'rows': rows}


def parse_investors(payload, code):
    if not valid_payload(payload, code) or not isinstance(payload.get('dealTrendInfos'), list):
        return None
    rows = {}
    for item in payload['dealTrendInfos']:
        if not isinstance(item, dict) or item.get('itemCode') != code or not date(item.get('bizdate')):
            continue
        day = date(item['bizdate'])
        rows[day] = {'date': day, **{key: number(item.get(field)) for key, field in {
            'individual': 'individualPureBuyQuant', 'foreign': 'foreignerPureBuyQuant',
            'institution': 'organPureBuyQuant', 'foreign_ratio': 'foreignerHoldRatio',
            'close': 'closePrice', 'volume': 'accumulatedTradingVolume',
        }.items()}}
    return sorted(rows.values(), key=lambda row: row['date'], reverse=True)


def parse_opinions(payload, code):
    if not valid_payload(payload, code):
        return None
    info = payload.get('consensusInfo')
    if not isinstance(info, dict) or info.get('itemCode') != code:
        info = {}
    score, target = number(info.get('recommMean')), number(info.get('priceTargetMean'))
    reports = []
    for item in payload.get('researches') or []:
        if not isinstance(item, dict) or item.get('cd') != code or not re.fullmatch(r'\d+', str(item.get('id', ''))):
            continue
        if not isinstance(item.get('tit'), str) or not item['tit'].strip():
            continue
        reports.append({'id': str(item['id']), 'title': item['tit'], 'broker': item.get('bnm') or '—',
                        'date': date(item.get('wdt')),
                        'url': f'https://finance.naver.com/research/company_read.naver?nid={item["id"]}'})
    return {'score': score if score is not None and 1 <= score <= 5 else None,
            'target_price': target if target is not None and target > 0 else None,
            'date': date(info.get('createDate')), 'reports': reports[:20]}


def parse_analysis(code, integration, annual, quarter):
    valid = [valid_payload(p, code) for p in (integration, annual, quarter)]
    if not any(valid):
        raise ValueError('No analysis available')
    overview = None
    for payload in (annual, quarter):
        summary = payload.get('corporationSummary') if valid_payload(payload, code) else None
        if isinstance(summary, dict):
            overview = [summary[key].strip() for key in ('comment1', 'comment2', 'comment3')
                        if isinstance(summary.get(key), str) and summary[key].strip()]
            if overview:
                break
    return {'symbol_code': code, 'annual': parse_finance(annual, code),
            'quarter': parse_finance(quarter, code), 'investors': parse_investors(integration, code),
            'opinions': parse_opinions(integration, code), 'overview': overview,
            'source': '네이버 증권', 'observed_at': datetime.now(timezone.utc).isoformat()}


async def get_stock_analysis(code):
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
        async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            payloads = await asyncio.gather(*(fetch(client, path) for path in
                ('integration', 'finance/annual', 'finance/quarter')), return_exceptions=True)
        result = parse_analysis(code, *payloads)
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as error:
        raise HTTPException(503, '종목 분석 자료를 불러오지 못했습니다. 잠시 후 다시 조회해 주세요.') from error
    if len(_cache) >= 256:
        _cache.pop(next(iter(_cache)))
    partial = any(result[key] is None for key in ('annual', 'quarter', 'investors', 'opinions', 'overview'))
    _cache[code] = (time.monotonic() + (30 if partial else 300), result)
    return result
