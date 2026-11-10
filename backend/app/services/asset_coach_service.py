"""내 자산 AI 코치. 계좌 숫자는 여기서 계산하고, AI 서버는 그 숫자로 문장만 쓴다."""
import asyncio
import logging
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from weakref import WeakValueDictionary

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.models.portfolio import Portfolio
from app.models.security import ItemMaster
from app.schemas.asset_coach import AssetCoachAdvice, AssetCoachFacts, AssetCoachResponse, CoachHolding
from app.services import kis_service
from app.services.ai_service_auth import AIServiceNotConfigured, service_headers
from app.services.asset_history_service import KST, owned_account
from app.services.pattern_brief_service import CONCENTRATED_PCT, DIVERSIFIED_MIN_SYMBOLS, DIVERSIFIED_PCT, ratio, rounded

logger = logging.getLogger(__name__)
CACHE_SIZE = 500
# 위험을 가늠해 보는 예시에 쓰는 하락 폭. 예측이 아니라 계좌 크기를 실감하게 하는 고정 값이다.
STRESS_DROP_PCT = Decimal(10)


def won(value):
    return Decimal(value).quantize(Decimal(1))


def severity(return_pct):
    size = abs(return_pct) if return_pct is not None else Decimal(0)
    return "small" if size < 3 else "moderate" if size < 10 else "large"


def concentration(weights):
    top = max(weights)
    if top >= CONCENTRATED_PCT:
        return "concentrated"
    if top < DIVERSIFIED_PCT and len(weights) >= DIVERSIFIED_MIN_SYMBOLS:
        return "diversified"
    return "moderate"


def holdings_of(db, account_id):
    rows = (db.query(Portfolio, ItemMaster.name)
            .outerjoin(ItemMaster, ItemMaster.symbol_code == Portfolio.symbol_code)
            .filter(Portfolio.account_id == account_id, Portfolio.hold_quantity != 0).all())
    return [(row.symbol_code, name or row.symbol_code, int(row.hold_quantity), Decimal(row.avg_price)) for row, name in rows]


async def price_of(code):
    try:
        quote = await kis_service.get_current_price(code)
        price = Decimal(str(quote.get("current_price")))
        if not price.is_finite() or price <= 0:
            raise ValueError("missing price")
        return price
    except Exception as exc:
        raise HTTPException(503, "일부 시세를 확인할 수 없어 AI 조언을 만들지 못했어요.") from exc


def build_facts(holdings, prices, cash, now):
    """보유 (코드, 이름, 수량, 평균단가)와 현재가로 계좌 사실을 만든다."""
    rows = []
    for code, name, quantity, avg in holdings:
        cost, market = avg * quantity, prices[code] * quantity
        rows.append(dict(name=name, cost=cost, market=market, pnl=won(market - cost),
                         return_pct=rounded((prices[code] - avg) / avg * 100) if avg > 0 else None))
    cost_total = won(sum(r["cost"] for r in rows))
    market_total = won(sum(r["market"] for r in rows))
    pnl = market_total - cost_total
    return_pct = rounded(pnl / cost_total * 100) if cost_total > 0 else None
    for r in rows:
        r["weight_pct"] = ratio(r["market"], market_total)

    def item(r):
        return CoachHolding(name=r["name"], weight_pct=r["weight_pct"], pnl=r["pnl"], return_pct=r["return_pct"])

    by_weight = sorted(rows, key=lambda r: r["market"], reverse=True)
    losers = [r for r in rows if r["pnl"] < 0]
    gainers = [r for r in rows if r["pnl"] > 0]
    total = cash + market_total
    return AssetCoachFacts(
        as_of=now, tone="loss" if pnl < 0 else "profit" if pnl > 0 else "flat", severity=severity(return_pct),
        cost_total=cost_total, market_total=market_total, cash=won(cash), unrealized_pnl=pnl, return_pct=return_pct,
        cash_weight_pct=ratio(cash, total) if total > 0 else None,
        holdings_count=len(rows), losing_count=len(losers), gaining_count=len(gainers),
        concentration=concentration([r["weight_pct"] for r in rows]),
        top_holdings=[item(r) for r in by_weight[:3]],
        top3_weight_pct=sum((r["weight_pct"] for r in by_weight[:3]), Decimal(0)) if len(rows) >= 4 else None,
        worst_holding=item(min(losers, key=lambda r: r["pnl"])) if losers else None,
        best_holding=item(max(gainers, key=lambda r: r["pnl"])) if gainers else None,
        stress_drop_pct=STRESS_DROP_PCT, stress_drop_amount=won(market_total * STRESS_DROP_PCT / 100),
    )


async def request_coach(facts):
    try:
        headers = service_headers()
    except AIServiceNotConfigured:
        raise HTTPException(503, "AI 조언을 지금 만들 수 없어요.") from None
    base = httpx.URL(settings.AI_SERVER_URL.strip())
    if base.scheme not in ("http", "https") or not base.host:
        raise HTTPException(503, "AI 조언을 지금 만들 수 없어요.")
    try:
        # AI 서버가 20초씩 두 번까지 시도하므로 그보다 길게 기다린다.
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(str(base).rstrip("/") + "/brief/asset-coach", headers=headers,
                                         json={"facts": facts.model_dump(mode="json")})
        response.raise_for_status()
        return AssetCoachAdvice.model_validate(response.json())
    except httpx.HTTPStatusError as exc:
        status = 503 if exc.response.status_code == 503 else 502
        raise HTTPException(status, "AI 조언을 만들지 못했어요.") from None
    except (httpx.HTTPError, ValueError, ValidationError):
        raise HTTPException(502, "AI 조언을 만들지 못했어요.") from None


class AssetCoachService:
    def __init__(self, generate=request_coach, now=lambda: datetime.now(KST)):
        self.generate = generate
        self.now = now
        self.cache = OrderedDict()
        self.locks = WeakValueDictionary()

    async def get(self, db, account_id, user_id):
        account = owned_account(db, account_id, user_id)
        holdings = holdings_of(db, account.account_id)
        cash = Decimal(account.withdrawable_cash or 0)
        db.rollback()  # 시세를 기다리는 동안 DB 잠금을 잡지 않는다.
        if not holdings:
            return None
        if any(quantity < 0 for _, _, quantity, _ in holdings):
            raise HTTPException(503, "보유수량을 확인할 수 없어 AI 조언을 만들지 못했어요.")
        prices = {}
        for code, *_ in holdings:
            prices[code] = await price_of(code)
        now = self.now()
        facts = build_facts(holdings, prices, cash, now)
        bucket = None if facts.return_pct is None else int(facts.return_pct.to_integral_value(rounding=ROUND_FLOOR))
        # 같은 날 보유가 그대로이고 수익률이 같은 1%p 구간이면 저장해 둔 조언을 다시 쓴다.
        key = (account.account_id, now.date(), tuple(sorted((c, q, a) for c, _, q, a in holdings)), facts.tone, bucket)
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self.cache.get(key)
            if hit:
                self.cache.move_to_end(key)
                return hit.model_copy(update={"cached": True})
            advice = await self.generate(facts)
            result = AssetCoachResponse(**advice.model_dump(), tone=facts.tone, as_of=facts.as_of, cached=False)
            self.cache[key] = result
            while len(self.cache) > CACHE_SIZE:
                self.cache.popitem(last=False)
            logger.info("asset_coach_diagnostic account=%s result=generated", account.account_id)
            return result


service = AssetCoachService()
