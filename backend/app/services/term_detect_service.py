"""어려운 말 찾기 AI 중계: 글 기준 캐시와 사용자별 호출 제한."""
import asyncio
from collections import OrderedDict, defaultdict, deque
import hashlib
import json
import time
import unicodedata
import httpx
from app.config import settings
from app.models.security import ItemMaster
from app.services.kis_service import search_listed_stocks
from app.schemas.term_detect import DetectedTerm, TermDetectRequest, TermDetectResponse
from app.services.ai_service_auth import AIServiceNotConfigured, service_headers

CACHE_TTL = 6 * 3600
CACHE_MAX_SIZE = 512
# 캐시에 없는 글만 횟수에 넣는다.
RATE_LIMIT = 15
RATE_WINDOW = 600
_cache = OrderedDict()
# 기사에서 흔히 쓰는 이름과 상장 종목명이 다른 경우. 종목 코드를 찾을 때만 쓴다.
ALIASES = {
    "네이버": "NAVER", "하이닉스": "SK하이닉스", "에스케이하이닉스": "SK하이닉스", "엘지전자": "LG전자",
    "엘지화학": "LG화학", "엘지에너지솔루션": "LG에너지솔루션", "현대자동차": "현대차", "기아자동차": "기아",
    "포스코홀딩스": "POSCO홀딩스", "케이티": "KT", "한국전력공사": "한국전력", "삼성바이오": "삼성바이오로직스",
}
NAMES_TTL = 600
_names = {"at": None, "names": []}
_calls = defaultdict(deque)


class TermDetectUnavailable(Exception):
    def __init__(self, status_code=502, detail="어려운 말 찾기를 잠시 사용할 수 없습니다."):
        self.status_code = status_code
        super().__init__(detail)


def request_key(request):
    text = " ".join(unicodedata.normalize("NFKC", request.text).split())
    known = sorted({unicodedata.normalize("NFKC", k) for k in request.known})
    return hashlib.sha256(json.dumps([text, known], ensure_ascii=False).encode("utf-8")).hexdigest()


def plain(name):
    return "".join(unicodedata.normalize("NFKC", name).split()).casefold()


def listed_names_in(db, text, now):
    """DB 종목표에 있는 이름 가운데 글에 그대로 나온 것. AI가 회사를 빠뜨리지 않게 힌트로 준다."""
    if _names["at"] is None or now - _names["at"] >= NAMES_TTL:
        _names.update(at=now, names=[row[0] for row in db.query(ItemMaster.name).all() if row[0] and len(row[0]) >= 2])
    found = {name for name in _names["names"] if name in text}
    found |= {alias for alias in ALIASES if alias in text}
    # 더 긴 이름 안에 든 짧은 이름(삼성생명 안의 삼성)은 뺀다.
    return sorted((n for n in found if not any(n != m and n in m for m in found)), key=text.index)[:20]


MARKETS = {"KOSPI": "코스피", "KOSDAQ": "코스닥", "코스피": "코스피", "코스닥": "코스닥"}


async def listed_code(db, name):
    """기업 이름과 똑같은 국내 상장 종목의 (코드, 시장). DB 종목표를 먼저 보고, 없으면 종목 검색을 쓴다."""
    name = ALIASES.get(name, name)
    row = db.query(ItemMaster.symbol_code, ItemMaster.market_type).filter(ItemMaster.name == name).first()
    if row:
        return row[0], MARKETS.get(row[1], "국내 상장")
    for item in await search_listed_stocks(name, 10):
        if plain(item["name"]) == plain(name):
            return item["symbol_code"], MARKETS.get(item.get("market_type"), "국내 상장")
    return None


def take_call(user_id, now):
    calls = _calls[user_id]
    while calls and now - calls[0] >= RATE_WINDOW:
        calls.popleft()
    if len(calls) >= RATE_LIMIT:
        raise TermDetectUnavailable(429, "잠시 후 다시 시도해 주세요. 어려운 말 찾기는 10분에 15번까지 할 수 있어요.")
    calls.append(now)


async def detect_terms(request: TermDetectRequest, user_id, db=None) -> TermDetectResponse:
    key = request_key(request)
    now = time.monotonic()
    cached = _cache.get(key)
    if cached and now - cached[0] < CACHE_TTL:
        _cache.move_to_end(key)
        return cached[1].model_copy(deep=True)
    _cache.pop(key, None)
    try:
        headers = service_headers()
        base = httpx.URL(settings.AI_SERVER_URL.strip())
        if base.scheme not in ("http", "https") or not base.host:
            raise TermDetectUnavailable(503)
    except (AIServiceNotConfigured, httpx.InvalidURL, AttributeError):
        raise TermDetectUnavailable(503) from None
    take_call(user_id, now)
    payload = request.model_dump()
    payload["companies"] = []
    if db is not None:
        try:
            payload["companies"] = listed_names_in(db, request.text, now)
        except Exception:  # 힌트는 없어도 된다.
            payload["companies"] = []
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.post(str(base).rstrip("/") + "/terms/detect", headers=headers,
                                         json=payload)
            response.raise_for_status()
            result = TermDetectResponse.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        raise TermDetectUnavailable(503 if exc.response.status_code == 503 else 502) from None
    except (httpx.InvalidURL, httpx.UnsupportedProtocol, httpx.ConnectError):
        raise TermDetectUnavailable(503) from None
    except (httpx.HTTPError, ValueError):
        raise TermDetectUnavailable() from None
    # AI 서버가 검사했더라도, 글에 없는 말은 하이라이트할 수 없으니 한 번 더 거른다.
    result.terms = [t for t in result.terms if t.term and t.term in request.text][:18]
    # 힌트로 준 상장사를 AI가 빠뜨리면 종목 데이터만으로 채운다. 설명은 시장 이름만 쓴다.
    named = {plain(t.term) for t in result.terms}
    for name in payload["companies"]:
        if plain(name) not in named and name not in ALIASES:
            result.terms.append(DetectedTerm(term=name, tier="company", meaning="", basis="data"))
    companies = [t for t in result.terms if t.tier == "company"]
    if db is not None and companies:
        codes = await asyncio.gather(*(listed_code(db, t.term) for t in companies), return_exceptions=True)
        for term, found in zip(companies, codes):
            if isinstance(found, tuple):
                # 국내 상장사의 시장은 AI 말 대신 종목 데이터로 쓴다.
                term.symbol_code, term.listing = found
                term.listing_checked = True
                if term.basis == "data":
                    term.meaning = f"{term.listing}에 상장된 국내 기업이에요. 종목 보기에서 시세를 확인할 수 있어요."
    result.terms = [t for t in result.terms if t.meaning]
    _cache[key] = (time.monotonic(), result.model_copy(deep=True))
    while len(_cache) > CACHE_MAX_SIZE:
        _cache.popitem(last=False)
    return result
