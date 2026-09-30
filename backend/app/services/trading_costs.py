"""Versioned KRX equity costs. Integer won/Decimal only; unknown products fail closed."""
import json
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

POLICY = json.loads(Path(__file__).with_name('trading_policy.json').read_text(encoding='utf-8'))


def calculate_costs(price, quantity, side, market):
    price = Decimal(str(price))
    if not price.is_finite() or price <= 0 or price != price.to_integral_value():
        raise ValueError('유효한 원화 체결가격이 필요합니다.')
    if isinstance(quantity, bool) or not isinstance(quantity, int) or not 0 < quantity <= 1_000_000:
        raise ValueError('주문 수량을 확인해 주세요.')
    if side not in ('매수', '매도') or market not in POLICY['markets']:
        raise ValueError('코스피·코스닥 일반주식의 과세 분류를 확인할 수 없습니다.')
    gross = price * quantity
    if gross > 9_000_000_000_000_000:
        raise ValueError('주문 금액이 허용 범위를 넘었습니다.')

    def fee(ppm, unit):
        return (gross * Decimal(ppm) / 1_000_000 / unit).to_integral_value(rounding=ROUND_DOWN) * unit

    rates = POLICY['markets'][market]
    commission = fee(POLICY['commission_ppm'], POLICY['commission_unit'])
    tax = fee(rates['transaction_tax_ppm'], 1) if side == '매도' else Decimal(0)
    rural = fee(rates['rural_tax_ppm'], 1) if side == '매도' else Decimal(0)
    costs = commission + tax + rural
    return dict(commission=commission, transaction_tax=tax, rural_tax=rural,
                cash_delta=-(gross + costs) if side == '매수' else gross - costs,
                tax_market=market, cost_policy_version=POLICY['version'])


def equity_market(security):
    if security.instrument_type != 'EQUITY' or security.tax_market not in POLICY['markets']:
        raise ValueError('과세 시장·상품 분류가 확인된 코스피·코스닥 일반주식만 주문할 수 있습니다. ETF·ETN·ELW·파생상품은 이 주문에서 지원하지 않습니다.')
    return security.tax_market
