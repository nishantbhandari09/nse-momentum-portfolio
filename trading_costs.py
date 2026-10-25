"""Configurable trading-cost model for daily equity backtests.

Rates are user-editable basis-point assumptions, not broker quotations.  Fees
and slippage are applied to the actual buy/sell turnover on each rebalance.
"""
from __future__ import annotations

import math
from typing import Mapping

DEFAULT_COST_CONFIG = {
    "buy_fee_bps": 15.0,
    "sell_fee_bps": 25.0,
    "slippage_bps": 5.0,
}


def normalize_cost_config(config: Mapping | None = None) -> dict:
    """Validate cost rates and return a complete, bounded configuration."""
    supplied = dict(config or {})
    result = {}
    for key, default in DEFAULT_COST_CONFIG.items():
        try:
            value = float(supplied.get(key, default))
        except (TypeError, ValueError):
            value = default
        if not math.isfinite(value):
            value = default
        result[key] = min(max(value, 0.0), 1000.0)
    return result


def effective_cost_rate(side: str, config: Mapping | None = None) -> float:
    """Return the all-in estimated fee+slippage rate as a decimal."""
    values = normalize_cost_config(config)
    side_key = "buy_fee_bps" if str(side).lower() == "buy" else "sell_fee_bps"
    return (values[side_key] + values["slippage_bps"]) / 10000.0


def estimate_trade_cost(notional: float, side: str, config: Mapping | None = None) -> float:
    """Estimate fee plus slippage cost for a positive trade notional."""
    try:
        amount = max(float(notional), 0.0)
    except (TypeError, ValueError):
        amount = 0.0
    return amount * effective_cost_rate(side, config)


def rebalance_holdings(
    current_holdings: Mapping[str, int],
    target_holdings: Mapping[str, int],
    close_row,
    current_cash: float,
    config: Mapping | None = None,
    allow_leverage: bool = False,
) -> tuple[dict[str, int], float, dict]:
    """Trade from current quantities toward target quantities, sells first.

    When leverage is disabled, purchases are reduced to fit available cash after
    estimated fees. A leverage-enabled strategy keeps its configured target size.
    Symbols without a usable execution price are not traded.
    """
    rates = normalize_cost_config(config)
    holdings = {
        str(symbol): max(int(quantity), 0)
        for symbol, quantity in (current_holdings or {}).items()
        if int(quantity) > 0
    }
    targets = {
        str(symbol): max(int(quantity), 0)
        for symbol, quantity in (target_holdings or {}).items()
        if int(quantity) > 0
    }
    cash = float(current_cash)
    buy_turnover = 0.0
    sell_turnover = 0.0
    total_cost = 0.0
    trade_count = 0
    sold = {}

    # Reduce or exit existing positions first so sale proceeds can fund buys.
    for symbol in sorted(set(holdings) | set(targets)):
        old_qty = holdings.get(symbol, 0)
        desired_qty = targets.get(symbol, 0)
        sell_qty = max(old_qty - desired_qty, 0)
        if not sell_qty:
            continue
        try:
            price = float(close_row.get(symbol))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        notional = sell_qty * price
        cost = estimate_trade_cost(notional, "sell", rates)
        cash += notional - cost
        sell_turnover += notional
        total_cost += cost
        trade_count += 1
        remaining = old_qty - sell_qty
        if remaining:
            sold[symbol] = remaining

    holdings = {
        symbol: min(quantity, targets.get(symbol, 0))
        for symbol, quantity in holdings.items()
        if min(quantity, targets.get(symbol, 0)) > 0
    }
    holdings.update(sold)

    # Buy only the additional shares needed to reach each target.
    buy_rate = effective_cost_rate("buy", rates)
    for symbol in sorted(targets):
        desired_qty = targets[symbol]
        already_held = holdings.get(symbol, 0)
        buy_qty = max(desired_qty - already_held, 0)
        if not buy_qty:
            continue
        try:
            price = float(close_row.get(symbol))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        if not allow_leverage:
            affordable = int(max(cash, 0.0) // (price * (1.0 + buy_rate)))
            buy_qty = min(buy_qty, affordable)
        if buy_qty <= 0:
            continue
        notional = buy_qty * price
        cost = estimate_trade_cost(notional, "buy", rates)
        cash -= notional + cost
        holdings[symbol] = already_held + buy_qty
        buy_turnover += notional
        total_cost += cost
        trade_count += 1

    holdings = {symbol: int(quantity) for symbol, quantity in holdings.items() if quantity > 0}
    stats = {
        "Trading Costs": float(total_cost),
        "Buy Turnover": float(buy_turnover),
        "Sell Turnover": float(sell_turnover),
        "Trade Count": int(trade_count),
    }
    return holdings, cash, stats
