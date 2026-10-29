"""본인 계좌의 DB 체결 기록만 집계한다. 외부 조회는 수행하지 않는다."""
from datetime import timezone, timedelta, time
from decimal import Decimal, ROUND_HALF_UP
from collections import defaultdict, deque
from fastapi import HTTPException
from app.models.account import Account
from app.models.order import Order
from app.models.portfolio import Portfolio
from app.models.security import ItemMaster
from app.models.user import User
from app.schemas.pattern_brief import PatternFacts, TAG_LABELS, INVESTMENT_STYLE_DESCRIPTIONS

KST = timezone(timedelta(hours=9))


def rounded(value):
    result = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return Decimal(0) if result == 0 else result


def ratio(part, total):
    return rounded(Decimal(part) / Decimal(total) * 100) if total else Decimal(0)


def kst(value):
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(KST)


HOLDING_SHORT_DAYS = Decimal(7)
HOLDING_SWING_DAYS = Decimal(30)
CONCENTRATED_PCT = Decimal(60)
DIVERSIFIED_PCT = Decimal(30)
DIVERSIFIED_MIN_SYMBOLS = 3
ORDER_HEAVY_PCT = Decimal(70)
OPENING_HEAVY_PCT = Decimal(50)
MISU_MIN_COUNT = 1


MIN_ORDER_STYLE_FILLS = 5
MIN_CONCENTRATION_BUYS = 3
MIN_HOLDING_SELL_ORDERS = 3
MIN_BEHAVIOR_SAMPLES = {
    "market_heavy": ("fills", MIN_ORDER_STYLE_FILLS),
    "limit_used": ("fills", MIN_ORDER_STYLE_FILLS),
    "opening_heavy": ("fills", MIN_ORDER_STYLE_FILLS),
    "spread_timing": ("fills", MIN_ORDER_STYLE_FILLS),
    "concentrated": ("buys", MIN_CONCENTRATION_BUYS),
    "diversified": ("buys", MIN_CONCENTRATION_BUYS),
    "day_trading": ("matched_sells", MIN_HOLDING_SELL_ORDERS),
    "patient_hold": ("matched_sells", MIN_HOLDING_SELL_ORDERS),
}


def data_confidence(count):
    return "preliminary" if count < 10 else "moderate" if count < 30 else "sufficient"


def enough_samples(facts, code):
    requirement = MIN_BEHAVIOR_SAMPLES.get(code)
    if requirement is None:
        return True
    counts = {"fills": filled_count(facts), "buys": facts.counts.buy_filled,
              "matched_sells": facts.holding.matched_sell_orders}
    return counts[requirement[0]] >= requirement[1]


def filled_count(facts):
    return facts.counts.buy_filled + facts.counts.sell_filled


# 강도가 같으면 이 표의 순서를 유지한다.
BEHAVIOR_CATALOG = (
    {"code":"uses_misu", "direction":"+", "grade":"strong",
     "condition":lambda f:f.order_style.misu_count >= 1,
     "strength":lambda f:max(Decimal(40), ratio(f.order_style.misu_count, filled_count(f)))},
    {"code":"concentrated", "direction":"+", "grade":"strong",
     "condition":lambda f:f.concentration.top_symbol_buy_share_pct >= 60,
     "strength":lambda f:f.concentration.top_symbol_buy_share_pct},
    {"code":"day_trading", "direction":"+", "grade":"strong",
     "condition":lambda f:f.holding.same_day_ratio_pct is not None and f.holding.same_day_ratio_pct >= 50,
     "strength":lambda f:f.holding.same_day_ratio_pct},
    {"code":"market_heavy", "direction":"+", "grade":"mild",
     "condition":lambda f:f.order_style.market_ratio_pct >= 70,
     "strength":lambda f:f.order_style.market_ratio_pct},
    {"code":"opening_heavy", "direction":"+", "grade":"mild",
     "condition":lambda f:f.timing.opening_30min_ratio_pct >= 50,
     "strength":lambda f:f.timing.opening_30min_ratio_pct},
    {"code":"no_misu", "direction":"-", "grade":"strong",
     "condition":lambda f:f.order_style.misu_count == 0 and filled_count(f) >= 3,
     "strength":lambda f:Decimal(60)},
    {"code":"diversified", "direction":"-", "grade":"strong",
     "condition":lambda f:f.concentration.top_symbol_buy_share_pct < 30 and f.counts.symbols_traded >= 3,
     "strength":lambda f:100-f.concentration.top_symbol_buy_share_pct},
    {"code":"patient_hold", "direction":"-", "grade":"strong",
     "condition":lambda f:f.holding.matched_sell_quantity > 0 and f.holding.avg_holding_days >= 8,
     "strength":lambda f:min(Decimal(100), f.holding.avg_holding_days/30*100)},
    {"code":"limit_used", "direction":"-", "grade":"mild",
     "condition":lambda f:f.order_style.limit_ratio_pct >= 50,
     "strength":lambda f:f.order_style.limit_ratio_pct},
    {"code":"spread_timing", "direction":"-", "grade":"mild",
     "condition":lambda f:f.timing.opening_30min_ratio_pct < 20 and filled_count(f) >= 3,
     "strength":lambda f:100-f.timing.opening_30min_ratio_pct},
)

STYLE_COMPARISON_RULES = {
    "안정형": {"matches": ("-", None), "differs": ("+", None)},
    "안정추구형": {"matches": ("-", None), "differs": ("+", None)},
    "위험중립형": {"matches": ("-", None), "differs": ("+", "strong")},
    "적극투자형": {"matches": ("+", None), "differs": None},
    "공격투자형": {"matches": ("+", None), "differs": None},
}


def _rank_behaviors(facts):
    catalog = {rule["code"]:rule for rule in BEHAVIOR_CATALOG}
    items = [{"code":rule["code"], "strength":rounded(min(Decimal(100), max(Decimal(0), rule["strength"](facts))))}
             for rule in BEHAVIOR_CATALOG if enough_samples(facts, rule["code"]) and rule["condition"](facts)]
    return sorted(items, key=lambda item:(catalog[item["code"]]["grade"] != "strong", -item["strength"]))


def determine_style_comparison_basis(facts):
    rules = STYLE_COMPARISON_RULES.get(facts.investment_style)
    if rules is None:
        return None
    catalog = {rule["code"]:rule for rule in BEHAVIOR_CATALOG}
    highlights = _rank_behaviors(facts)
    result = {}
    limit = 1 if data_confidence(filled_count(facts)) == "preliminary" else 3
    for field, selection in rules.items():
        result[field] = [] if selection is None else [item for item in highlights
            if catalog[item["code"]]["direction"] == selection[0]
            and (selection[1] is None or catalog[item["code"]]["grade"] == selection[1])][:limit]
    return result


def determine_pattern_highlights(facts):
    basis = determine_style_comparison_basis(facts)
    compared = {item["code"] for items in basis.values() for item in items} if basis is not None else set()
    return [item for item in _rank_behaviors(facts) if item["code"] not in compared][:3]


def holding_duration(orders):
    lots = defaultdict(deque)
    matched = unmatched = same_day = weighted_days = matched_orders = 0
    # 같은 시각의 매수는 해당 시각의 매도와 짝지을 수 있다. 체결 선후관계를 추정하지 않는다.
    ordered = sorted((o for o in orders if o.filled_at is not None),
                     key=lambda o:(kst(o.filled_at), o.order_type != "매수"))
    for order in ordered:
        day = kst(order.filled_at).date()
        if order.order_type == "매수":
            lots[order.symbol_code].append([day, order.quantity])
        elif order.order_type == "매도":
            remaining = order.quantity
            matched_before = matched
            queue = lots[order.symbol_code]
            while remaining and queue:
                lot = queue[0]
                quantity = min(remaining, lot[1])
                days = (day - lot[0]).days
                matched += quantity
                weighted_days += quantity * days
                same_day += quantity if days == 0 else 0
                remaining -= quantity
                lot[1] -= quantity
                if not lot[1]: queue.popleft()
            unmatched += remaining
            matched_orders += matched > matched_before
    return {"matched_sell_orders":matched_orders,"matched_sell_quantity":matched,"unmatched_sell_quantity":unmatched,
            "avg_holding_days":rounded(Decimal(weighted_days)/matched) if matched else None,
            "same_day_ratio_pct":ratio(same_day,matched) if matched else None}


def determine_style_tags(facts):
    codes = []
    holding = facts.holding
    if holding.matched_sell_quantity > 0 and holding.matched_sell_orders >= MIN_HOLDING_SELL_ORDERS:
        days = holding.avg_holding_days
        codes.append("day_trading" if days == 0 else "short_term" if days <= HOLDING_SHORT_DAYS
                     else "swing" if days <= HOLDING_SWING_DAYS else "long_term")
    share = facts.concentration.top_symbol_buy_share_pct
    if facts.counts.buy_filled >= MIN_CONCENTRATION_BUYS and share >= CONCENTRATED_PCT: codes.append("concentrated")
    elif facts.counts.buy_filled >= MIN_CONCENTRATION_BUYS and share < DIVERSIFIED_PCT and facts.counts.symbols_traded >= DIVERSIFIED_MIN_SYMBOLS:
        codes.append("diversified")
    if filled_count(facts) >= MIN_ORDER_STYLE_FILLS and facts.order_style.market_ratio_pct >= ORDER_HEAVY_PCT: codes.append("market_order_heavy")
    elif filled_count(facts) >= MIN_ORDER_STYLE_FILLS and facts.order_style.limit_ratio_pct >= ORDER_HEAVY_PCT: codes.append("limit_order_heavy")
    if filled_count(facts) >= MIN_ORDER_STYLE_FILLS and facts.timing.opening_30min_ratio_pct >= OPENING_HEAVY_PCT: codes.append("opening_heavy")
    if facts.order_style.misu_count >= MISU_MIN_COUNT: codes.append("uses_misu")
    return [{"code":code,"label":TAG_LABELS[code]} for code in codes]


def build_pattern_facts(db, account_id: int, user_id: int) -> PatternFacts:
    account = db.query(Account).filter(Account.account_id==account_id, Account.user_id==user_id).first()
    if account is None:
        raise HTTPException(404, "계좌를 찾을 수 없습니다.")
    rows = db.query(Order, ItemMaster.name).outerjoin(ItemMaster, ItemMaster.symbol_code==Order.symbol_code).filter(
        Order.account_id==account_id, Order.status=="체결").all()
    orders = [row[0] for row in rows]
    buys = [o for o in orders if o.order_type=="매수"]
    sells = [o for o in orders if o.order_type=="매도"]
    amounts = defaultdict(Decimal)
    names = {}
    for order, name in rows:
        names[order.symbol_code] = name
        if order.order_type=="매수": amounts[order.symbol_code] += order.price * order.quantity
    total = sum(amounts.values(), Decimal(0))
    top = sorted(amounts, key=lambda code:(-amounts[code],code))[:3]
    returns = [(o.price / o.sale_cost_basis - 1)*100 for o in sells
               if o.sale_cost_basis is not None and o.sale_cost_basis > 0]
    stamps = [kst(o.filled_at) for o in orders if o.filled_at is not None]
    first, last = (min(stamps),max(stamps)) if stamps else (None,None)
    user = db.query(User).filter(User.user_id==user_id).first()
    facts = PatternFacts.model_validate({
        "period":{"first_filled_at":first,"last_filled_at":last,
                  "days":(last.date()-first.date()).days if stamps else 0},
        "counts":{"buy_filled":len(buys),"sell_filled":len(sells),
                  "symbols_traded":len({o.symbol_code for o in orders})},
        "concentration":{"top_symbol_buy_share_pct":ratio(amounts[top[0]],total) if top else 0,
                         "top_symbols":[{"symbol_code":c,"name":names[c],"buy_share_pct":ratio(amounts[c],total)} for c in top]},
        "realized":{"sells_with_basis":len(returns),"wins":sum(x>0 for x in returns),
                    "losses":sum(x<0 for x in returns),
                    "breakeven":sum(x==0 for x in returns),"unknown_basis":len(sells)-len(returns),
                    "avg_realized_return_pct":rounded(sum(returns)/len(returns)) if returns else None},
        "order_style":{"market_ratio_pct":ratio(sum(o.price_type=="시장가" for o in orders),len(orders)),
                       "limit_ratio_pct":ratio(sum(o.price_type=="지정가" for o in orders),len(orders)),
                       "misu_count":sum(o.funding_type!="현금" for o in orders)},
        "timing":{"opening_30min_ratio_pct":ratio(sum(o.created_at is not None and
                   time(9)<=kst(o.created_at).time()<time(9,30) for o in orders),len(orders))},
        "holding":holding_duration(orders), "style_tags":[], "style_comparison_basis":None, "pattern_highlights":[],
        "holdings_count":db.query(Portfolio.symbol_code).filter(Portfolio.account_id==account_id,
                         Portfolio.hold_quantity>0).distinct().count(),
        "investment_style":user.investment_style or None if user else None,
        "investment_style_description":INVESTMENT_STYLE_DESCRIPTIONS.get(user.investment_style) if user else None,
        "data_confidence":data_confidence(len(buys)+len(sells)),
    })

    payload = facts.model_dump()
    payload["style_tags"] = determine_style_tags(facts)
    tagged = PatternFacts.model_validate(payload)
    payload["pattern_highlights"] = determine_pattern_highlights(tagged)
    payload["style_comparison_basis"] = determine_style_comparison_basis(tagged)
    return PatternFacts.model_validate(payload)
