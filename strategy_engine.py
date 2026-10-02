from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np
import pandas as pd

from fyers_data import FyersClient, get_history_cached
from universe_manager import load_catalog, symbols_for_group

PRICE_DIR = Path("data/prices")
INDEX_DIR = Path("data/indices")
HISTORY_CONSTITUENTS = Path("historical_constituents.csv")

LOOKBACK_DEFAULTS = [252, 120, 90, 60]

# Process-level cache so repeated Streamlit reruns reuse loaded history.
_MEMORY_HISTORY_CACHE: Dict[str, pd.DataFrame] = {}


def clean_symbol(symbol: str) -> str:
    return str(symbol).replace(":", "_").replace("/", "_").replace("?", "_")


def _load_local_history(symbol: str, cache_dir: Path = PRICE_DIR) -> pd.DataFrame:
    path = cache_dir / f"{clean_symbol(symbol)}.parquet"
    csv_path = cache_dir / f"{clean_symbol(symbol)}.csv"
    if not path.exists() and not csv_path.exists():
        # Common fallback for index history.
        path = INDEX_DIR / f"{clean_symbol(symbol)}.parquet"
        csv_path = INDEX_DIR / f"{clean_symbol(symbol)}.csv"
    if path.exists():
        df = pd.read_parquet(path)
    elif csv_path.exists():
        df = pd.read_csv(csv_path)
    else:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    if "date" not in df.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    parsed = pd.to_datetime(df["date"], errors="coerce")
    normalized = []
    for value in parsed:
        if pd.isna(value):
            normalized.append(pd.NaT)
            continue
        ts = pd.Timestamp(value)
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        normalized.append(ts.normalize())
    df["date"] = pd.Series(normalized, index=df.index)
    df = df.dropna(subset=["date"])
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.sort_values("date").drop_duplicates("date")


def save_history(df: pd.DataFrame, symbol: str, index: bool = False) -> Path:
    directory = INDEX_DIR if index else PRICE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{clean_symbol(symbol)}.parquet"
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception:
        csv_path = directory / f"{clean_symbol(symbol)}.csv"
        df.to_csv(csv_path, index=False)
        return csv_path


def _slice_history(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    x = df.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x = x.dropna(subset=["date"])
    return x[(x["date"].dt.date >= start) & (x["date"].dt.date <= end)].copy()


def _live_number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return None


def apply_live_bar(
    hist: pd.DataFrame,
    symbol: str,
    asof: date,
    live_snapshot: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> pd.DataFrame:
    """Overlay today's FYERS SymbolUpdate on cached daily history."""
    if not live_snapshot or symbol not in live_snapshot or hist.empty:
        return hist
    payload = live_snapshot.get(symbol) or {}
    ltp = _live_number(payload, "ltp", "last_price")
    if ltp is None or ltp <= 0:
        return hist

    day = pd.Timestamp(asof).normalize()
    open_price = _live_number(payload, "open_price", "open") or ltp
    high_price = _live_number(payload, "high_price", "high") or ltp
    low_price = _live_number(payload, "low_price", "low") or ltp
    volume = _live_number(payload, "vol_traded_today", "volume")

    x = hist.copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.normalize()
    x = x.dropna(subset=["date"])
    fallback_volume = 0.0
    if "volume" in x.columns and not x.empty:
        try:
            fallback_volume = float(x.iloc[-1]["volume"])
        except Exception:
            fallback_volume = 0.0

    row = {
        "date": day,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": ltp,
        "volume": volume if volume is not None else fallback_volume,
    }
    x = x[x["date"] != day]
    x = pd.concat([x, pd.DataFrame([row])], ignore_index=True)
    return x.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)


def load_history_for_symbol(symbol: str, start: date, end: date, client: Optional[FyersClient] = None,
                             allow_fetch: bool = False) -> pd.DataFrame:
    is_index = "-INDEX" in symbol.upper()
    cache_dir = INDEX_DIR if is_index else PRICE_DIR

    memory = _MEMORY_HISTORY_CACHE.get(symbol)
    if memory is not None and not memory.empty:
        memory = memory.copy()
        dates = pd.to_datetime(memory["date"], errors="coerce")
        if not dates.dropna().empty:
            min_date = dates.min().date()
            max_date = dates.max().date()
            if min_date <= start and max_date >= end:
                return _slice_history(memory, start, end)

    # Allow FYERS to extend/repair the requested range only when explicitly requested.
    if allow_fetch and client:
        fetched = get_history_cached(client, symbol, start, end, str(cache_dir))
        if not fetched.empty:
            save_history(fetched, symbol, index=is_index)
            _MEMORY_HISTORY_CACHE[symbol] = fetched.copy()
            return _slice_history(fetched, start, end)

    if memory is not None and not memory.empty:
        return _slice_history(memory, start, end)

    df = _load_local_history(symbol)
    if df.empty:
        return df
    _MEMORY_HISTORY_CACHE[symbol] = df.copy()
    return _slice_history(df, start, end)


def normalize_lookbacks(raw: Dict | None) -> Dict[int, float]:
    """Normalize saved JSON keys (often strings) back to integer lookback periods."""
    raw = raw or {}
    out: Dict[int, float] = {}
    for key, value in raw.items():
        try:
            out[int(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out or {252: 0.4, 120: 0.3, 90: 0.2, 60: 0.1}


def _return(df: pd.DataFrame, n: int, asof: pd.Timestamp) -> float:
    hist = df[df["date"] <= asof].sort_values("date")
    if len(hist) <= n:
        return np.nan
    current = float(hist.iloc[-1]["close"])
    past = float(hist.iloc[-(n + 1)]["close"])
    return current / past - 1.0 if past else np.nan


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def vstop_long(df: pd.DataFrame, atr_period: int = 14, multiplier: float = 3.0) -> pd.DataFrame:
    x = df.sort_values("date").copy()
    prev_close = x["close"].shift(1)
    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - prev_close).abs(),
        (x["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_period, min_periods=atr_period).mean()
    highest = x["high"].rolling(atr_period, min_periods=atr_period).max()
    x["vstop"] = highest - multiplier * atr
    return x


def market_gate(index_history: pd.DataFrame, asof: pd.Timestamp, indicator: str, period: int,
                atr_period: int = 14, multiplier: float = 3.0) -> Tuple[bool, str, float]:
    if index_history.empty:
        return True, "NO INDEX DATA", np.nan
    h = index_history[index_history["date"] <= asof].sort_values("date").copy()
    if len(h) < max(period, atr_period) + 5:
        return True, "INSUFFICIENT INDEX HISTORY", np.nan
    close = float(h.iloc[-1]["close"])
    ind = indicator.upper()
    if ind == "EMA":
        val = float(ema(h["close"], period).iloc[-1])
    elif ind == "SMA":
        val = float(sma(h["close"], period).iloc[-1])
    else:
        v = vstop_long(h, atr_period, multiplier)
        val = float(v["vstop"].iloc[-1])
    if np.isnan(val):
        return True, "INDICATOR NOT READY", val
    allowed = close >= val
    return allowed, ("ENTRY ALLOWED" if allowed else "ENTRY BLOCKED"), val


def point_in_time_universe(group: str, asof: date, catalog: Optional[pd.DataFrame] = None) -> List[str]:
    catalog = catalog if catalog is not None else load_catalog()
    base = symbols_for_group(group, catalog)
    if not HISTORY_CONSTITUENTS.exists() or base.empty:
        return base["symbol"].tolist()
    try:
        hc = pd.read_csv(HISTORY_CONSTITUENTS, comment="#")
        needed = {"symbol", "group", "valid_from", "valid_to"}
        if not needed.issubset(hc.columns):
            return base["symbol"].tolist()
        valid_from = pd.to_datetime(hc["valid_from"], errors="coerce")
        valid_to = pd.to_datetime(hc["valid_to"], errors="coerce")
        hc["valid_from"] = valid_from.apply(lambda x: x.date() if pd.notna(x) else None)
        hc["valid_to"] = valid_to.apply(lambda x: x.date() if pd.notna(x) else None)
        active = hc[(hc["group"].astype(str).str.upper() == group.upper()) &
                    (hc["valid_from"] <= asof) &
                    ((hc["valid_to"].isna()) | (hc["valid_to"] >= asof))]
        if active.empty:
            return base["symbol"].tolist()
        return active["symbol"].astype(str).tolist()
    except Exception:
        return base["symbol"].tolist()


def rank_symbols(symbols: Iterable[str], asof: pd.Timestamp, lookbacks: Dict[int, float],
                  start: date, end: date, client: Optional[FyersClient] = None,
                  allow_fetch: bool = False,
                  history_map: Optional[Mapping[str, pd.DataFrame]] = None,
                  live_snapshot: Optional[Mapping[str, Mapping[str, Any]]] = None) -> pd.DataFrame:
    rows = []
    for symbol in symbols:
        hist = history_map.get(symbol) if history_map is not None else None
        if hist is None:
            hist = load_history_for_symbol(
                symbol, start, end, client=client, allow_fetch=allow_fetch
            )
        hist = apply_live_bar(hist, symbol, asof.date(), live_snapshot)
        if hist.empty:
            continue
        returns = {n: _return(hist, int(n), asof) for n in lookbacks}
        if all(pd.isna(v) for v in returns.values()):
            continue
        score = 0.0
        weight_total = 0.0
        for n, w in lookbacks.items():
            v = returns[n]
            if pd.notna(v):
                score += float(v) * float(w)
                weight_total += float(w)
        if weight_total <= 0:
            continue
        score /= weight_total
        close_hist = hist[hist["date"] <= asof]
        last_close = float(close_hist.iloc[-1]["close"])
        rows.append({"symbol": symbol, "score": score, "last_close": last_close, **{f"ret_{n}": v for n, v in returns.items()}})
    if not rows:
        return pd.DataFrame(columns=["rank", "symbol", "score", "last_close"])
    out = pd.DataFrame(rows).sort_values(["score", "symbol"], ascending=[False, True]).reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    return out[["rank", "symbol", "score", "last_close"] + [c for c in out.columns if c.startswith("ret_")]]




def apply_scan_conditions(
    ranked: pd.DataFrame,
    strategy: Dict,
    asof: date,
    client: Optional[FyersClient] = None,
    start: Optional[date] = None,
    allow_fetch: bool = False,
    history_map: Optional[Mapping[str, pd.DataFrame]] = None,
    live_snapshot: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> pd.DataFrame:
    if ranked.empty:
        return ranked
    conditions = strategy.get("conditions", {}) or {}
    out = ranked.copy()
    keep = pd.Series(True, index=out.index)
    for i, row in out.iterrows():
        symbol_start = start or (asof - timedelta(days=550))
        hist = history_map.get(row["symbol"]) if history_map is not None else None
        if hist is None:
            hist = load_history_for_symbol(
                row["symbol"], symbol_start, asof,
                client=client, allow_fetch=allow_fetch
            )
        hist = apply_live_bar(hist, row["symbol"], asof, live_snapshot)
        if hist.empty:
            keep.loc[i] = False
            continue
        h = hist[hist["date"] <= pd.Timestamp(asof)].sort_values("date")
        if h.empty:
            keep.loc[i] = False
            continue
        close = float(h.iloc[-1]["close"])
        if conditions.get("above_ema"):
            period = int(conditions.get("ema_period", 50))
            if len(h) < period or close < float(ema(h["close"], period).iloc[-1]):
                keep.loc[i] = False
                continue
        if conditions.get("above_sma"):
            period = int(conditions.get("sma_period", 50))
            if len(h) < period or close < float(sma(h["close"], period).iloc[-1]):
                keep.loc[i] = False
                continue
        days = min(252, len(h))
        hi = float(h["high"].tail(days).max())
        lo = float(h["low"].tail(days).min())
        range_52 = hi - lo
        distance_from_high = ((hi - close) / hi * 100.0) if hi > 0 else np.nan
        position_from_low = ((close - lo) / range_52 * 100.0) if range_52 > 0 else np.nan
        out.loc[i, "52W High"] = hi
        out.loc[i, "52W Low"] = lo
        out.loc[i, "Distance From 52W High %"] = distance_from_high
        out.loc[i, "Position From 52W Low %"] = position_from_low

        if conditions.get("near_52w"):
            threshold = float(conditions.get("near_52w_pct", 40.0))
            reference = str(conditions.get("retracement_reference", "52W High"))
            if reference == "52W Low":
                passes_retracement = pd.notna(position_from_low) and position_from_low >= threshold
            else:
                passes_retracement = pd.notna(distance_from_high) and distance_from_high <= threshold
            if not passes_retracement:
                keep.loc[i] = False
                continue

        if conditions.get("breakout_52w"):
            prior = h["high"].tail(253).iloc[:-1].max() if len(h) > 252 else np.nan
            if pd.isna(prior) or close <= float(prior):
                keep.loc[i] = False
                continue
    out = out.loc[keep].copy().reset_index(drop=True)
    out["rank"] = np.arange(1, len(out) + 1)
    top_n = int(strategy.get("top_n", 20))
    exit_rank = int(strategy.get("exit_rank", 40))
    out["Signal"] = np.where(
        out["rank"] <= top_n,
        "ENTRY / TOP HOLDING",
        np.where(out["rank"] <= exit_rank, "HOLD / BUFFER", "EXIT ZONE"),
    )
    return out

def scan_strategy(
    strategy: Dict,
    asof: date,
    client: Optional[FyersClient] = None,
    auto_fetch: bool = True,
    live_snapshot: Optional[Mapping[str, Mapping[str, Any]]] = None,
    progress_callback: Optional[Any] = None,
) -> Tuple[pd.DataFrame, Dict]:
    group = strategy["group"]
    lookbacks = normalize_lookbacks(strategy.get("lookbacks"))
    conditions = strategy.get("conditions", {}) or {}
    ema_period = int(conditions.get("ema_period", 50))
    history_days = max(max(lookbacks.keys()) + 180, 450, ema_period * 3)
    start = asof - timedelta(days=history_days)
    end = asof
    symbols = point_in_time_universe(group, asof)

    # Load history once per symbol, then reuse the same in-memory frame for ranking
    # and technical filters.
    history_map: Dict[str, pd.DataFrame] = {}
    missing: List[str] = []
    total = len(symbols)
    for idx, symbol in enumerate(symbols, start=1):
        hist = load_history_for_symbol(
            symbol, start, end, client=client, allow_fetch=bool(auto_fetch)
        )
        if hist.empty:
            missing.append(symbol)
        else:
            history_map[symbol] = hist
        if progress_callback is not None:
            try:
                progress_callback(idx, total)
            except Exception:
                pass

    ranked = rank_symbols(
        symbols, pd.Timestamp(asof), lookbacks, start, end,
        client=client, allow_fetch=False,
        history_map=history_map, live_snapshot=live_snapshot,
    )
    ranked = apply_scan_conditions(
        ranked, strategy, asof,
        client=client, start=start, allow_fetch=False,
        history_map=history_map, live_snapshot=live_snapshot,
    )
    status = {
        "universe_size": len(symbols),
        "history_loaded": len(history_map),
        "history_missing": len(missing),
        "missing_symbols": missing[:25],
        "ranked_size": len(ranked),
        "asof": str(asof),
        "group": group,
        "history_start": str(start),
        "history_end": str(end),
        "auto_fetch": bool(auto_fetch),
        "live_symbols": len(live_snapshot or {}),
        "live_overlay": bool(live_snapshot),
    }
    return ranked, status


@dataclass
class Holding:
    symbol: str
    qty: int
    avg_price: float


def _next_trading_date(index: pd.DatetimeIndex, signal_date: pd.Timestamp) -> Optional[pd.Timestamp]:
    future = index[index > signal_date]
    return future.min() if len(future) else None


def _price_on(df: pd.DataFrame, dt: pd.Timestamp, field: str) -> float:
    if df.empty:
        return np.nan
    x = df[df["date"] == dt]
    if x.empty:
        # Use first available trading day after dt.
        x = df[df["date"] > dt].head(1)
    if x.empty:
        return np.nan
    return float(x.iloc[0][field])


def backtest_strategy(strategy: Dict, start_date: date, end_date: date, initial_capital: float,
                      client: Optional[FyersClient] = None, auto_fetch: bool = False) -> Dict[str, pd.DataFrame | float | str | int]:
    # Signal is created from the rebalance day's close; trades are executed at the next trading day's open.
    rebalance = str(strategy.get("rebalance", "Monthly"))
    dates = pd.date_range(start_date, end_date, freq="B")
    if dates.empty:
        raise ValueError("Backtest date range is empty.")
    if rebalance.lower().startswith("quarter"):
        signal_dates = pd.Series(dates).groupby(dates.to_period("Q")).max().tolist()
    else:
        signal_dates = pd.Series(dates).groupby(dates.to_period("M")).max().tolist()
    # Use the trading calendar implied by the downloaded market/index data where possible.
    holdings: Dict[str, Holding] = {}
    cash = float(initial_capital)
    trades: List[dict] = []
    rebalance_rows: List[dict] = []
    equity_rows: List[dict] = []
    lookbacks = normalize_lookbacks(strategy.get("lookbacks"))
    top_n = int(strategy.get("top_n", 20))
    exit_rank = int(strategy.get("exit_rank", 40))
    market_index = strategy.get("market_index", "NSE:NIFTY50-INDEX")
    gate_indicator = strategy.get("market_indicator", "EMA")
    gate_period = int(strategy.get("market_period", 200))
    atr_period = int(strategy.get("vstop_atr", 14))
    vstop_mult = float(strategy.get("vstop_multiplier", 3.0))
    cost_bps = float(strategy.get("transaction_cost_bps", 10.0))
    slip_bps = float(strategy.get("slippage_bps", 5.0))

    all_needed_symbols = set()
    catalog = load_catalog()
    # Build broad list of possible symbols for the group over the whole backtest period.
    if group_is_broad(strategy.get("group", "ALL")):
        current = catalog
    else:
        current = symbols_for_group(strategy.get("group", "ALL"), catalog)
        # symbols_for_group may auto-sync NIFTY constituent lists; reload the catalog
        # so monthly point-in-time universe checks use the refreshed constituent set.
        catalog = load_catalog()
    if not current.empty:
        all_needed_symbols.update(current["symbol"].tolist())
    all_needed_symbols.add(market_index)

    histories: Dict[str, pd.DataFrame] = {}
    extended_start = start_date - timedelta(days=int(max(lookbacks.keys())) * 2 + gate_period + 50)
    for symbol in list(all_needed_symbols):
        hist = load_history_for_symbol(symbol, extended_start, end_date, client=client, allow_fetch=auto_fetch)
        if not hist.empty:
            histories[symbol] = hist

    if market_index in histories:
        market_hist = histories[market_index]
    else:
        market_hist = _load_local_history(market_index)

    def portfolio_value(dt: pd.Timestamp, use_close: bool = True) -> float:
        total = cash
        for h in holdings.values():
            hist = histories.get(h.symbol, _load_local_history(h.symbol))
            px = _price_on(hist, dt, "close" if use_close else "open")
            if pd.notna(px):
                total += h.qty * px
        return total

    for signal_date in signal_dates:
        signal_date = pd.Timestamp(signal_date).normalize()
        if signal_date < pd.Timestamp(start_date) or signal_date > pd.Timestamp(end_date):
            continue
        execution_dates = pd.DatetimeIndex([d for h in histories.values() for d in h["date"] if d > signal_date])
        exec_date = execution_dates.min() if len(execution_dates) else None
        if exec_date is None or exec_date > pd.Timestamp(end_date):
            continue
        allowed, gate_status, gate_value = market_gate(market_hist, signal_date, gate_indicator, gate_period, atr_period, vstop_mult)
        symbols = point_in_time_universe(strategy.get("group", "ALL"), signal_date.date())
        rank_df = rank_symbols(
            symbols, signal_date, lookbacks, extended_start, signal_date.date(),
            client=client, allow_fetch=auto_fetch
        )
        rank_df = apply_scan_conditions(
            rank_df, strategy, signal_date.date(),
            client=client, start=extended_start, allow_fetch=auto_fetch
        )
        rank_map = dict(zip(rank_df["symbol"], rank_df["rank"])) if not rank_df.empty else {}
        price_map_close = {}
        for sym, h in list(histories.items()):
            if signal_date in set(h["date"]):
                price_map_close[sym] = float(h.loc[h["date"] == signal_date, "close"].iloc[0])

        exits = []
        for sym, h in list(holdings.items()):
            rank = rank_map.get(sym, 10**9)
            if rank > exit_rank:
                exits.append((sym, "RANK_EXIT" if rank < 10**8 else "NOT_IN_UNIVERSE", rank))
        cash_before = cash
        entry_candidates = []
        # Execute exits first so sale proceeds are available for entries.
        for sym, reason, rank in exits:
            hist = histories.get(sym, pd.DataFrame())
            px = _price_on(hist, exec_date, "open")
            if pd.isna(px):
                continue
            h = holdings.pop(sym)
            gross = h.qty * px
            costs = gross * (cost_bps + slip_bps) / 10000.0
            cash += gross - costs
            pnl = (px - h.avg_price) * h.qty - costs
            trades.append({"signal_date": signal_date.date(), "execution_date": exec_date.date(), "action": "SELL",
                           "symbol": sym, "rank": rank if rank < 10**8 else None, "qty": h.qty, "price": px,
                           "amount": gross, "cost": costs, "pnl": pnl, "reason": reason, "market_status": gate_status})
        if allowed:
            target = rank_df[rank_df["rank"] <= top_n]
            for _, row in target.iterrows():
                if row["symbol"] not in holdings:
                    entry_candidates.append(row["symbol"])
            current_equity = portfolio_value(exec_date, use_close=False)
            target_value = current_equity / max(top_n, 1)
            available_slots = max(len(entry_candidates), 1)
            for sym in entry_candidates:
                hist = histories.get(sym, pd.DataFrame())
                px = _price_on(hist, exec_date, "open")
                if pd.isna(px) or px <= 0:
                    continue
                desired = min(target_value, cash / available_slots)
                qty = int(math.floor(desired / (px * (1 + (cost_bps + slip_bps) / 10000.0))))
                if qty <= 0:
                    continue
                gross = qty * px
                costs = gross * (cost_bps + slip_bps) / 10000.0
                cash -= gross + costs
                holdings[sym] = Holding(sym, qty, float(px))
                trades.append({"signal_date": signal_date.date(), "execution_date": exec_date.date(), "action": "BUY",
                               "symbol": sym, "rank": int(rank_map.get(sym, 0)), "qty": qty, "price": px,
                               "amount": gross, "cost": costs, "pnl": 0.0, "reason": "TOP_N_ENTRY", "market_status": gate_status})
                available_slots = max(available_slots - 1, 1)

        eq = portfolio_value(exec_date, use_close=True)
        rebalance_rows.append({"signal_date": signal_date.date(), "execution_date": exec_date.date(),
                               "market_status": gate_status, "market_close": _price_on(market_hist, signal_date, "close"),
                               "market_indicator": gate_value, "cash_after": cash, "holdings": len(holdings),
                               "equity": eq, "entries": sum(1 for t in trades if t["execution_date"] == exec_date.date() and t["action"] == "BUY"),
                               "exits": sum(1 for t in trades if t["execution_date"] == exec_date.date() and t["action"] == "SELL")})
        equity_rows.append({"date": exec_date.date(), "equity": eq, "cash": cash, "holdings": len(holdings)})

    trades_df = pd.DataFrame(trades)
    rebalance_df = pd.DataFrame(rebalance_rows)
    equity_df = pd.DataFrame(equity_rows).drop_duplicates("date").sort_values("date")
    if equity_df.empty:
        ending = initial_capital
        total_return = 0.0
        cagr = 0.0
        max_dd = 0.0
    else:
        ending = float(equity_df.iloc[-1]["equity"])
        total_return = ending / initial_capital - 1 if initial_capital else 0
        years = max((pd.Timestamp(equity_df.iloc[-1]["date"]) - pd.Timestamp(start_date)).days / 365.25, 1/365.25)
        cagr = (ending / initial_capital) ** (1 / years) - 1 if initial_capital and ending > 0 else -1.0
        peak = equity_df["equity"].cummax()
        dd = equity_df["equity"] / peak - 1
        max_dd = float(dd.min()) if len(dd) else 0.0
    closed = trades_df[trades_df["action"] == "SELL"] if not trades_df.empty else pd.DataFrame()
    wins = int((closed["pnl"] > 0).sum()) if not closed.empty else 0
    losses = int((closed["pnl"] <= 0).sum()) if not closed.empty else 0
    gross_profit = float(closed.loc[closed["pnl"] > 0, "pnl"].sum()) if not closed.empty else 0.0
    gross_loss = float(-closed.loc[closed["pnl"] < 0, "pnl"].sum()) if not closed.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else np.inf if gross_profit > 0 else 0.0
    metrics = {
        "starting_capital": initial_capital, "ending_value": ending, "total_return": total_return,
        "cagr": cagr, "max_drawdown": max_dd, "total_trades": len(trades_df),
        "buy_count": int((trades_df["action"] == "BUY").sum()) if not trades_df.empty else 0,
        "sell_count": int((trades_df["action"] == "SELL").sum()) if not trades_df.empty else 0,
        "win_rate": wins / (wins + losses) if wins + losses else 0.0,
        "profit_factor": profit_factor,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "ending_cash": cash, "open_positions": len(holdings),
    }
    open_df = pd.DataFrame([{"symbol": h.symbol, "qty": h.qty, "avg_price": h.avg_price} for h in holdings.values()])
    return {"trades": trades_df, "rebalances": rebalance_df, "equity": equity_df, "open_holdings": open_df, "metrics": metrics}


def group_is_broad(group: str) -> bool:
    return group.upper() in {"ALL", "ALL_ETF", "ALL_INDEX", "DOMESTIC_ETF", "INTERNATIONAL_ETF", "DEFENSIVE"}
