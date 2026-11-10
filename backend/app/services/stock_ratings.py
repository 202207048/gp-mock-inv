"""Broker ratings as published by Naver/WiseReport (not inferred from scores)."""
from html.parser import HTMLParser
import re
import time

import httpx
from fastapi import HTTPException
from app.services.stock_fundamentals import number
from app.services.stock_analysis import date

_cache = {}


class RatingsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.code = None
        self.active = False
        self.found = False
        self.cell = None
        self.row = []
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id') == 'hd_cmp_cd':
            self.code = attrs.get('value')
        if tag == 'table' and attrs.get('id') == 'cTB24':
            self.active = self.found = True
        if self.active and tag == 'tr':
            self.row = []
        if self.active and tag == 'td':
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td' and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        if self.active and tag == 'tr' and len(self.row) == 7:
            self.rows.append(self.row)
        if self.active and tag == 'table':
            self.active = False


def parse_ratings(html, code):
    parser = RatingsParser()
    parser.feed(html)
    if parser.code != code or not parser.found:
        raise ValueError('Unrecognized broker rating table')
    rows = []
    for broker, day, target, previous_target, change, opinion, previous_opinion in parser.rows:
        # Source's dates are YY/MM/DD. Keep unrated text verbatim.
        published = date('20' + day.replace('/', '')) if re.fullmatch(r'\d{2}/\d{2}/\d{2}', day) else None
        if not broker or not published:
            continue
        rows.append({'broker': broker, 'date': published, 'target_price': number(target),
                     'previous_target_price': number(previous_target), 'target_change': number(change),
                     'opinion': opinion or None, 'previous_opinion': previous_opinion or None})
    return {'symbol_code': code, 'rows': rows, 'source': '네이버 기업분석 · WiseReport',
            'source_url': f'https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={code}'}


async def get_stock_ratings(code):
    if not re.fullmatch(r'[A-Z0-9]{6}', code):
        raise HTTPException(422, '유효한 종목코드를 입력해 주세요.')
    cached = _cache.get(code)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=12, headers={'User-Agent': 'Mozilla/5.0'}) as client:
            response = await client.get('https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx', params={'cmp_cd': code})
            response.raise_for_status()
            result = parse_ratings(response.text, code)
    except (httpx.HTTPError, ValueError) as error:
        raise HTTPException(503, '증권사별 투자의견을 불러오지 못했습니다.') from error
    if len(_cache) >= 256:
        _cache.pop(next(iter(_cache)))
    _cache[code] = (time.monotonic() + 300, result)
    return result
