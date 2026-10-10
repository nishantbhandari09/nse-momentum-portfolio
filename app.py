import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
from io import StringIO
from datetime import datetime, timedelta

from fyers_auth import (
    get_login_url,
    exchange_code_for_token,
    get_authenticated_fyers,
    create_signed_state,
    validate_signed_state,
    FyersLoginError,
)
import momentum_data
import index_history_data
from etf_index_universe import fetch_etf_and_index_universe
from index_constituents import (
    INDEX_CATEGORIES,
    ALL_ETF_CATEGORY,
    fetch_index_constituents,
    fetch_all_etfs_and_bees,
)
from etf_group_universe import (
    ETF_GROUP_NAMES,
    ETF_GROUP_SYMBOLS,
    ETF_GROUP_ALIASES,
    get_etf_group_symbols,
)

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(page_title="Quantitative Strategy Engine & Backtest Studio", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #F4F7FE; }
    .stMetric { background-color: #FFFFFF; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
    .status-active { color: #00875A; font-weight: bold; background-color: #E3FCEF; padding: 2px 8px; border-radius: 4px; }
    .status-paused { color: #DE350B; font-weight: bold; background-color: #FFEBE6; padding: 2px 8px; border-radius: 4px; }
    </style>
""", unsafe_allow_html=True)

ASSET_GROUPS_MAPPING = {
    "Gold ETF": ["GOLDBEES.NS"],
    "LiquidBEES": ["LIQUIDBEES.NS"],
    "Gov Bond": ["SETFGSEC.NS"],
}

# Kept as a small overlay for portfolio metrics and regime checks. This is
# deliberately separate from the full user-selected ALL ETF universe.
ALWAYS_FETCH_OVERLAY_TICKERS = [
    "GOLDBEES.NS", "LIQUIDBEES.NS", "SETFGSEC.NS",
    "NIFTYBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS",
]

NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

# ==========================================
# DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_tickers_by_group(groups):
    tickers = []

    if "Nifty 500" in groups or "Nifty 200" in groups:
        official_nse_url = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            response = session.get(official_nse_url, timeout=10)
            if response.status_code == 200:
                df = pd.read_csv(StringIO(response.text))
                if "Symbol" in df.columns:
                    n500 = [f"{str(sym).strip()}.NS" for sym in df["Symbol"].dropna().unique()]
                    if "Nifty 200" in groups and "Nifty 500" not in groups:
                        tickers.extend(n500[:200])
                    else:
                        tickers.extend(n500)
        except Exception:
            github_mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
            try:
                df_mirror = pd.read_csv(github_mirror_url)
                if "Symbol" in df_mirror.columns:
                    n500 = [f"{str(sym).strip()}.NS" for sym in df_mirror["Symbol"].dropna().unique()]
                    if "Nifty 200" in groups and "Nifty 500" not in groups:
                        tickers.extend(n500[:200])
                    else:
                        tickers.extend(n500)
            except Exception:
                tickers.extend(["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "LT.NS", "SBIN.NS"])

    for grp in groups:
        if grp in ETF_GROUP_SYMBOLS or grp in ETF_GROUP_ALIASES:
            tickers.extend(get_etf_group_symbols(grp))
        elif grp in ASSET_GROUPS_MAPPING:
            tickers.extend(ASSET_GROUPS_MAPPING[grp])

    known_group_names = (
        set(ASSET_GROUPS_MAPPING)
        | set(ETF_GROUP_SYMBOLS)
        | set(ETF_GROUP_ALIASES)
        | {"Nifty 500", "Nifty 200"}
    )
    remaining_groups = [g for g in groups if g not in known_group_names]
    if remaining_groups:
        try:
            etf_universe = fetch_etf_and_index_universe()
            for grp in remaining_groups:
                tickers.extend(etf["symbol"] for etf in etf_universe["etfs"].get(grp, []))
        except Exception:
            pass

    if not tickers:
        tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

    return list(set(tickers))


def get_selected_etf_tickers(groups):
    """Resolve selected ETF groups to symbols, including older broker categories."""
    tickers = set()
    for group in groups:
        if group in ETF_GROUP_SYMBOLS or group in ETF_GROUP_ALIASES:
            tickers.update(get_etf_group_symbols(group))
        elif group in ASSET_GROUPS_MAPPING:
            tickers.update(ASSET_GROUPS_MAPPING[group])

    # Keep previously available broker-derived ETF categories working too.
    try:
        etf_universe = fetch_etf_and_index_universe()
        for group in groups:
            for item in etf_universe["etfs"].get(group, []):
                symbol = str(item.get("symbol", "")).strip()
                if symbol:
                    tickers.add(symbol if symbol.endswith(".NS") else f"{symbol}.NS")
    except Exception:
        pass
    return tickers


@st.cache_data(ttl=3600)
def fetch_market_data(tickers, period="5y", full_history=False):
    defensive_and_regime = list(set(ALWAYS_FETCH_OVERLAY_TICKERS + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]))
    archive_based = momentum_data.get_prices_with_live_topup(
        tickers,
        extra_tickers=defensive_and_regime,
        missing_period="max" if full_history else "5y",
    )

    if not archive_based.empty:
        prices = archive_based
    else:
        all_tickers = list(set(tickers + defensive_and_regime))
        data = yf.download(tickers=all_tickers, period=period, interval="1d", auto_adjust=True, progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            prices = data["Close"] if "Close" in data.columns else data["Adj Close"]
        else:
            prices = data

    # Prefer the repository-stored official Nifty index close series over
    # vendor fallback values. These CSVs persist in GitHub and are updated daily.
    try:
        stored_index_prices = index_history_data.load_index_close_prices()
        if not stored_index_prices.empty:
            if prices is None or prices.empty:
                prices = stored_index_prices
            else:
                prices = prices.combine_first(stored_index_prices)
                prices.update(stored_index_prices)
    except Exception:
        # Historical index files may not have been seeded yet; retain the
        # existing archive/Yahoo fallback rather than blocking the app.
        pass

    # Keep ETFs with shorter available histories; never backfill future prices.
    prices = prices.loc[:, ~prices.isna().all(axis=0)].sort_index().ffill()
    return prices

# ==========================================
# FYERS - LIVE PRICE OVERLAY
# ==========================================
def get_fyers_client():
    if "fyers_access_token" not in st.session_state:
        raise RuntimeError("not connected yet this session — use the Fyers Connection panel above")
    return get_authenticated_fyers(st.session_state["fyers_client_id"], st.session_state["fyers_access_token"])


def render_fyers_connection_panel():
    # The OAuth callback returns to this same Streamlit URL. Capture the code
    # directly from query parameters and exchange it automatically. The user
    # never needs to copy/paste an auth_code.
    app_id = str(st.secrets.get("FYERS_APP_ID") or "").strip()
    # Accept either common secret name to avoid breaking existing Streamlit setups.
    secret_key = str(st.secrets.get("FYERS_SECRET_ID") or st.secrets.get("FYERS_SECRET_KEY") or "").strip()
    redirect_uri = str(st.secrets.get("FYERS_REDIRECT_URI") or "").strip()

    callback_code = str(st.query_params.get("auth_code", "") or st.query_params.get("code", "")).strip()
    callback_state = str(st.query_params.get("state", "")).strip()
    callback_error = str(st.query_params.get("error_description", "") or st.query_params.get("error", "")).strip()

    if callback_error:
        st.error(f"FYERS login was cancelled or rejected: {callback_error}")
        st.query_params.clear()
    elif callback_code:
        try:
            if not (app_id and secret_key and redirect_uri):
                raise FyersLoginError(
                    "Add FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI in Streamlit Secrets first."
                )
            if not validate_signed_state(callback_state, secret_key):
                raise FyersLoginError(
                    "The FYERS login return could not be verified or has expired. "
                    "Click Connect FYERS and complete a fresh login."
                )
            with st.spinner("Finishing FYERS connection..."):
                token = exchange_code_for_token(app_id, secret_key, redirect_uri, callback_code)
            st.session_state["fyers_access_token"] = token
            st.session_state["fyers_client_id"] = app_id
            st.session_state["fyers_connected_at"] = datetime.now().isoformat(timespec="seconds")
            st.query_params.clear()
            st.rerun()
        except FyersLoginError as e:
            st.error(str(e))
            st.query_params.clear()
        except Exception as e:
            st.error(f"FYERS connection failed: {e}")
            st.query_params.clear()

    with st.expander("🔌 FYERS Connection", expanded="fyers_access_token" not in st.session_state):
        if "fyers_access_token" in st.session_state:
            st.success("Connected to FYERS for this session.")
            connected_at = st.session_state.get("fyers_connected_at")
            if connected_at:
                st.caption(f"Connected at {connected_at}")
            if st.button("Disconnect FYERS"):
                st.session_state.pop("fyers_access_token", None)
                st.session_state.pop("fyers_client_id", None)
                st.session_state.pop("fyers_connected_at", None)
                st.rerun()
            return

        if not (app_id and secret_key and redirect_uri):
            st.warning(
                "Complete FYERS setup in Streamlit Secrets. Required keys: "
                "FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI. "
                "Use the App ID and Secret from the same activated app, and make the redirect URL "
                "exactly match the URL registered in the FYERS API dashboard."
            )
            return

        try:
            state = create_signed_state(secret_key)
            login_url = get_login_url(app_id, secret_key, redirect_uri, state=state).generate_authcode()
            st.link_button("🔐 Connect FYERS", login_url, type="primary", use_container_width=True)
            st.caption(
                "Sign in on FYERS. After approval, you will return here and the app will finish "
                "the connection automatically—no copying or pasting an auth code."
            )
        except FyersLoginError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Couldn't set up FYERS login: {e}")


def get_live_prices(yf_tickers):
    yf_tickers = [t for t in dict.fromkeys(yf_tickers) if t and not t.startswith("^")]
    if not yf_tickers:
        return {}
    try:
        fyers = get_fyers_client()
    except Exception:
        st.session_state["_fyers_status"] = str(Exception)
        return {}

    fyers_symbols = {t: "NSE:" + t.replace(".NS", "") + "-EQ" for t in yf_tickers}
    try:
        response = fyers.quotes({"symbols": ",".join(fyers_symbols.values())})
    except Exception as e:
        st.session_state["_fyers_status"] = f"request failed ({e})"
        return {}

    if response.get("s") != "ok":
        st.session_state["_fyers_status"] = f"request failed ({response})"
        return {}

    st.session_state["_fyers_status"] = "live"
    by_symbol = {item.get("n"): item.get("v", {}).get("lp") for item in response.get("d", [])}
    return {
        ticker: by_symbol[sym]
        for ticker, sym in fyers_symbols.items()
        if by_symbol.get(sym) is not None
    }


def apply_live_price_overlay(prices_df, tickers):
    live_prices = get_live_prices(tickers)
    result = {}
    for t in tickers:
        if t in live_prices:
            result[t] = float(live_prices[t])
        elif not prices_df.empty and t in prices_df.columns:
            series = prices_df[t].dropna()
            if not series.empty:
                result[t] = float(series.iloc[-1])
    return result

# ==========================================
# STATE INITIALIZATION
# ==========================================
if "navigation_tab" not in st.session_state:
    st.session_state.navigation_tab = "DASHBOARD"

if "active_strategy_view" not in st.session_state:
    st.session_state.active_strategy_view = None

if "editing_strategy_name" not in st.session_state:
    st.session_state.editing_strategy_name = None

if "strategies" not in st.session_state:
    st.session_state.strategies = {
        "Alpha Momentum Multiplier": {
            "created_date": (datetime.now() - timedelta(days=43)).strftime("%Y-%m-%d"),
            "status": "Active",
            "type": "Real",
            "allocated": 100000.0,
            "realized_pnl": 0.0,
            "rebalance_freq": "Monthly",
            "rebalance_day": 1,
            "groups": ["Nifty 500", "Gold ETF"],
            "allocation_multiplier": 1.0,
            "entry_rank": 10,
            "exit_rank": 20,
            "period_days": [252, 120, 60],
            "period_weights": [0.5, 0.3, 0.2],
            "pct_from_high": 15.0,
            "pct_from_low": 0.0,
            "moving_average": "200 EMA",
            "use_rs": True,
            "rs_benchmark": "Nifty 500 / G-Sec",
            "min_price": 10.0,
            "skip_days": 21,
            "positions": []
        }
    }

# ==========================================
# METRICS & CALCULATIONS ENGINE
# ==========================================
def calculate_days_to_rebalance(rebalance_day):
    today = datetime.now()
    target_date = datetime(today.year, today.month, min(rebalance_day, 28))
    if target_date < today:
        if today.month == 12:
            target_date = datetime(today.year + 1, 1, min(rebalance_day, 28))
        else:
            target_date = datetime(today.year, today.month + 1, min(rebalance_day, 28))
    return (target_date - today).days


def calculate_strategy_metrics(strat):
    positions = strat.get("positions", [])
    realized_pnl = strat.get("realized_pnl", 0.0)
    allocated = strat["allocated"]

    unrealized_pnl = 0.0
    current_holding_val = 0.0
    total_cost_basis = 0.0
    positions_data = []

    if positions and strat["status"] == "Active":
        tickers = [p["Symbol"] for p in positions]
        prices_df = fetch_market_data(tickers, period="1mo")
        live_or_close = apply_live_price_overlay(prices_df, tickers)

        for pos in positions:
            sym = pos["Symbol"]
            buy_qty = pos["Buy Qty"]
            buy_price = pos["Buy Price"]
            cmp_price = live_or_close.get(sym, buy_price)

            cost_val = buy_price * buy_qty
            curr_val = cmp_price * buy_qty
            pos_unrealized = curr_val - cost_val
            pos_pnl_pct = ((cmp_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0

            total_cost_basis += cost_val
            unrealized_pnl += pos_unrealized
            current_holding_val += curr_val

            positions_data.append({
                "Symbol": sym.replace(".NS", ""),
                "Buy Qty": buy_qty,
                "Buy Price": f"₹{buy_price:,.2f}",
                "Entry Date": pos["Entry Date"],
                "CMP": f"₹{cmp_price:,.2f}",
                "Current Value": f"₹{curr_val:,.2f}",
                "Current P&L": f"₹{pos_unrealized:,.2f}",
                "Current P&L %": f"{pos_pnl_pct:.2f}%"
            })

    cash_reserve = max(0.0, allocated - total_cost_basis) if total_cost_basis > 0 else allocated
    total_pnl = realized_pnl + unrealized_pnl
    returns_pct = (total_pnl / allocated * 100) if allocated > 0 else 0.0
    current_total_value = cash_reserve + current_holding_val + realized_pnl

    return {
        "allocated": allocated,
        "cash_reserve": cash_reserve,
        "current_holding_val": current_holding_val,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "returns_pct": returns_pct,
        "current_total_value": current_total_value,
        "positions_data": positions_data
    }


MAJOR_MARKET_INDICES = {
    "Nifty 50": "^NSEI",
    "Nifty Bank": "^NSEBANK",
    "Nifty 500": "^CRSLDX",
    "Nifty 200": "^CNX200",
    "Nifty IT": "^CNXIT",
    "Nifty Pharma": "^CNXPHARMA",
    "Nifty Auto": "^CNXAUTO",
    "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL",
}


def _market_trend_is_bullish(strat, as_of_date=None):
    """Return True when the selected index is above its configured trend indicator."""
    if not strat.get("use_market_trend_filter", False):
        return True
    index_name = strat.get("market_trend_index", "Nifty 50")
    ticker = MAJOR_MARKET_INDICES.get(index_name)
    if not ticker:
        return True
    try:
        history = fetch_market_data([ticker], period="5y")
        if history.empty or ticker not in history.columns:
            return False
        series = history[ticker].dropna()
        if as_of_date is not None:
            series = series.loc[:pd.Timestamp(as_of_date)]
        indicator = strat.get("market_trend_indicator", "Moving Average")
        if indicator == "Moving Average":
            period = int(strat.get("market_trend_ma_period", 200))
            ma_type = strat.get("market_trend_ma_type", "EMA")
            average = series.ewm(span=period, adjust=False).mean() if ma_type == "EMA" else series.rolling(period).mean()
            if len(series) < period or pd.isna(average.iloc[-1]):
                return False
            return bool(series.iloc[-1] > average.iloc[-1])
        # Volatility Stop (ATR-based trailing stop). Above the stop = bullish.
        atr_period = int(strat.get("market_trend_vstop_atr_period", 10))
        multiplier = float(strat.get("market_trend_vstop_multiplier", 2.0))
        # Use the checked-in official OHLC cache first. Yahoo remains a fallback
        # until the initial backfill has completed or if an index file is missing.
        ohlc = index_history_data.load_index_ohlc(ticker)
        if not ohlc.empty:
            ohlc = ohlc.set_index("Date")
        else:
            ohlc = yf.download(ticker, period="5y", interval="1d", auto_adjust=True, progress=False)
        if ohlc.empty:
            return False
        if isinstance(ohlc.columns, pd.MultiIndex):
            if "Close" in ohlc.columns.get_level_values(0):
                close = ohlc["Close"][ticker] if ticker in ohlc["Close"].columns else ohlc["Close"].iloc[:, 0]
            else:
                close = ohlc.xs("Close", axis=1, level=1).iloc[:, 0]
            high = ohlc.xs("High", axis=1, level=0).iloc[:, 0] if "High" in ohlc.columns.get_level_values(0) else close
            low = ohlc.xs("Low", axis=1, level=0).iloc[:, 0] if "Low" in ohlc.columns.get_level_values(0) else close
        else:
            close = ohlc["Close"]
            high = ohlc["High"] if "High" in ohlc.columns else close
            low = ohlc["Low"] if "Low" in ohlc.columns else close
        frame = pd.DataFrame({"close": close, "high": high, "low": low}).dropna()
        if as_of_date is not None:
            frame = frame.loc[:pd.Timestamp(as_of_date)]
        if len(frame) < atr_period + 2:
            return False
        previous_close = frame["close"].shift(1)
        true_range = pd.concat([
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = true_range.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()
        long_stop = frame["close"].rolling(atr_period).max() - multiplier * atr
        # Use an ATR trailing stop approximation for the trend regime.
        stop = long_stop.copy()
        for i in range(1, len(stop)):
            if pd.isna(stop.iloc[i]) or pd.isna(stop.iloc[i - 1]):
                continue
            if frame["close"].iloc[i - 1] > stop.iloc[i - 1]:
                stop.iloc[i] = max(stop.iloc[i], stop.iloc[i - 1])
        return bool(frame["close"].iloc[-1] > stop.iloc[-1])
    except Exception:
        return False


def run_strategy_stock_scanner(strat, price_subset=None, current_holdings=None, as_of_date=None, membership_calendar=None):
    selected_groups = strat.get("groups", ["Nifty 500"])

    use_relative_momentum = bool(strat.get("use_relative_momentum_filter", False))
    relative_index_name = strat.get("relative_momentum_index", "Nifty 50")
    relative_index_ticker = MAJOR_MARKET_INDICES.get(relative_index_name, "^NSEI")

    if price_subset is None:
        group_tickers = get_trusted_tickers_by_group(selected_groups)
        fetch_tickers = list(group_tickers)
        if use_relative_momentum and relative_index_ticker not in fetch_tickers:
            fetch_tickers.append(relative_index_ticker)
        prices_df = fetch_market_data(fetch_tickers, period="2y")
        candidate_tickers = set(group_tickers)
    else:
        prices_df = price_subset
        candidate_tickers = None

    if prices_df.empty:
        return []

    latest_prices = prices_df.iloc[-1]
    filtered_df = prices_df.copy()

    # For a live scan, use only the selected main group as the stock universe.
    # The benchmark is fetched separately and must never be ranked as a stock.
    if use_relative_momentum and candidate_tickers is not None:
        filtered_df = filtered_df.loc[:, [c for c in filtered_df.columns if c in candidate_tickers]]

    trend_bullish = _market_trend_is_bullish(strat, as_of_date=as_of_date)
    if strat.get("use_market_trend_filter", False) and not trend_bullish:
        # Bearish regime: do not open new positions. Existing holdings may be
        # liquidated at the backtest rebalance if the exit rule is enabled.
        if strat.get("market_trend_exit_on_bearish", True):
            current_holdings = []
        return []

    if use_relative_momentum:
        if relative_index_ticker not in prices_df.columns:
            # Fail closed: don't silently admit stocks if benchmark history is missing.
            return []
        benchmark = prices_df[relative_index_ticker].dropna()
        if as_of_date is not None:
            benchmark = benchmark.loc[:pd.Timestamp(as_of_date)]
        if benchmark.empty:
            return []

        # Restrict the numerator to the configured main group. In point-in-time
        # stock backtests, retain the historical archive names and let the
        # membership calendar below decide which ones were eligible on each date.
        if candidate_tickers is None:
            selected_etf_tickers = get_selected_etf_tickers(selected_groups)
            has_equity_group = any(g in selected_groups for g in ("Nifty 500", "Nifty 200"))
            if has_equity_group and membership_calendar is not None:
                candidate_cols = [
                    c for c in filtered_df.columns
                    if not c.startswith("^")
                    and (
                        c not in ALWAYS_FETCH_OVERLAY_TICKERS
                        and c != GSEC_REGIME_TICKER
                        or c in selected_etf_tickers
                    )
                ]
            else:
                main_group_tickers = set(get_trusted_tickers_by_group(selected_groups))
                candidate_cols = [c for c in filtered_df.columns if c in main_group_tickers]
            filtered_df = filtered_df.loc[:, candidate_cols]
        else:
            candidate_cols = [c for c in filtered_df.columns if c in candidate_tickers]

        # RS is the stock's price divided by the selected index level. Scaling
        # the ratio by 100 makes it easier to read but doesn't change the MA test.
        candidate_prices = prices_df.reindex(columns=candidate_cols)
        aligned_benchmark = benchmark.reindex(candidate_prices.index).ffill()
        rs_ratio = candidate_prices.div(aligned_benchmark.replace(0, np.nan), axis=0) * 100.0
        rs_type = strat.get("relative_momentum_ma_type", "SMA")
        rs_period = max(2, int(strat.get("relative_momentum_ma_period", 200)))
        if rs_type == "EMA":
            rs_average = rs_ratio.ewm(span=rs_period, adjust=False, min_periods=rs_period).mean()
        else:
            rs_average = rs_ratio.rolling(window=rs_period, min_periods=rs_period).mean()

        current_rs = rs_ratio.iloc[-1]
        current_rs_average = rs_average.iloc[-1]
        passes_rs = (current_rs > current_rs_average) & current_rs.notna() & current_rs_average.notna()
        filtered_df = filtered_df.loc[:, [c for c in filtered_df.columns if c in passes_rs.index and bool(passes_rs.get(c, False))]]
        if filtered_df.empty:
            return []

    ma_config = strat.get("moving_average", "200 EMA")
    if ma_config != "None":
        period = 200 if "200" in ma_config else (100 if "100" in ma_config else 50)
        is_ema = "EMA" in ma_config
        if is_ema:
            ma_vals = prices_df.ewm(span=period, adjust=False).mean().iloc[-1]
        else:
            ma_vals = prices_df.rolling(window=period).mean().iloc[-1]
        filtered_df = filtered_df.loc[:, latest_prices > ma_vals]

    pct_high = strat.get("pct_from_high", 15.0)
    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = ((period_high - latest_prices) / period_high) * 100
        filtered_df = filtered_df.loc[:, pct_diff <= pct_high]

    min_price = strat.get("min_price", 0.0)
    if min_price > 0:
        filtered_df = filtered_df.loc[:, latest_prices >= min_price]

    if as_of_date is not None and membership_calendar is not None and not membership_calendar.empty:
        selected_etf_tickers = get_selected_etf_tickers(selected_groups)
        eligible_cols = [
            c for c in filtered_df.columns
            if c in selected_etf_tickers
            or momentum_data.was_member(c, as_of_date, membership_calendar)
        ]
        filtered_df = filtered_df[eligible_cols]

    if strat.get("use_rs", True):
        if NIFTY_REGIME_TICKER in prices_df.columns and GSEC_REGIME_TICKER in prices_df.columns:
            rs_ratio = prices_df[NIFTY_REGIME_TICKER] / prices_df[GSEC_REGIME_TICKER]
            rs_sma = rs_ratio.rolling(window=50).mean()
            if rs_ratio.iloc[-1] < rs_sma.iloc[-1]:
                return [ASSET_GROUPS_MAPPING["Gov Bond"][0]]

    periods = strat.get("period_days", [252, 120, 60])
    weights = strat.get("period_weights", [0.5, 0.3, 0.2])
    skip_days = strat.get("skip_days", 0)

    if len(weights) < len(periods):
        weights = [1.0 / len(periods)] * len(periods)

    weights = np.array(weights[:len(periods)], dtype=float)
    weights /= weights.sum()

    end_idx = -1 - skip_days
    rank_components = []
    for p in periods:
        start_idx = end_idx - p
        if len(filtered_df) > abs(start_idx):
            returns = (filtered_df.iloc[end_idx] / filtered_df.iloc[start_idx] - 1).dropna()
            rank_components.append(returns.rank(ascending=False, method="min"))

    entry_n = strat.get("entry_rank", 10)

    if not rank_components:
        return list(filtered_df.columns[:entry_n])

    composite_rank = pd.concat(rank_components, axis=1).mul(weights, axis=1).sum(axis=1).sort_values()

    ordinal_rank = pd.Series(range(1, len(composite_rank) + 1), index=composite_rank.index)
    exit_n = max(strat.get("exit_rank", entry_n), entry_n)
    held = set(current_holdings or [])

    retained = [s for s in ordinal_rank.index if s in held and ordinal_rank[s] <= exit_n]
    slots_needed = max(entry_n - len(retained), 0)
    new_entries = [s for s in ordinal_rank.index if s not in retained][:slots_needed]

    return retained + new_entries


def run_backtest_simulation(strat_config, initial_capital, start_date, end_date, use_point_in_time=True):
    selected_groups = strat_config.get("groups", ["Nifty 500"])
    apply_point_in_time = use_point_in_time and any(
        group in selected_groups for group in ("Nifty 500", "Nifty 200")
    )

    if apply_point_in_time:
        # Keep the historical stock archive for point-in-time membership and
        # add the selected ETF groups so they can be ranked in the same strategy.
        tickers = list(dict.fromkeys(
            momentum_data.load_all_archive_symbols()
            + get_trusted_tickers_by_group(selected_groups)
        ))
        # Load historical membership for the selected equity index universe.
        # Nifty 500 retains the app's existing calendar (the primary source
        # already used by this project); Nifty 200 uses the broader historical
        # index ledger. If both are selected, Nifty 500 is the superset.
        membership_frames = []
        if "Nifty 500" in selected_groups:
            try:
                legacy_membership = momentum_data.load_membership_calendar()
                if legacy_membership is not None and not legacy_membership.empty:
                    membership_frames.append(legacy_membership[["symbol", "start", "end"]])
            except Exception:
                pass
            if not membership_frames:
                fallback_membership = index_history_data.get_membership_calendar("Nifty 500")
                if not fallback_membership.empty:
                    membership_frames.append(fallback_membership)
        elif "Nifty 200" in selected_groups:
            nifty200_membership = index_history_data.get_membership_calendar("Nifty 200")
            if not nifty200_membership.empty:
                membership_frames.append(nifty200_membership)

        membership_calendar = (
            pd.concat(membership_frames, ignore_index=True).drop_duplicates()
            if membership_frames else None
        )
        # Request price history for all historical members as well as the
        # 970-symbol archive and current index constituents. This enables
        # members that later exited the index to be considered on earlier dates.
        if membership_calendar is not None and not membership_calendar.empty:
            historic_members = (
                membership_calendar["symbol"].dropna().astype(str).str.upper().tolist()
            )
            historic_members = [
                symbol if symbol.endswith(".NS") else f"{symbol}.NS"
                for symbol in historic_members
            ]
            tickers = list(dict.fromkeys(tickers + historic_members))
    else:
        # ETF-only strategies don't need index membership restrictions.
        tickers = get_trusted_tickers_by_group(selected_groups)
        membership_calendar = None

    if strat_config.get("use_relative_momentum_filter", False):
        relative_index_ticker = MAJOR_MARKET_INDICES.get(
            strat_config.get("relative_momentum_index", "Nifty 50"), "^NSEI"
        )
        if relative_index_ticker not in tickers:
            tickers.append(relative_index_ticker)

    prices_df = fetch_market_data(tickers, period="5y", full_history=True)
    prices_df = prices_df.loc[start_date:end_date]
    if len(prices_df) < 252:
        return None, None, None

    try:
        rebalance_dates = prices_df.resample('ME').first().index
    except ValueError:
        rebalance_dates = prices_df.resample('MS').first().index

    portfolio_history = []
    holdings_history = []
    current_cash = initial_capital
    current_holdings = {}
    multiplier = strat_config.get("allocation_multiplier", 1.0)

    for i in range(len(rebalance_dates) - 1):
        dt = rebalance_dates[i]
        historical_sub_df = prices_df.loc[:dt]

        if len(historical_sub_df) < 252:
            continue

        selected_stocks = run_strategy_stock_scanner(
            strat_config, price_subset=historical_sub_df, current_holdings=list(current_holdings.keys()),
            as_of_date=dt, membership_calendar=membership_calendar,
        )
        holdings_history.append((dt, list(selected_stocks)))

        total_val = current_cash
        for sym, qty in current_holdings.items():
            if sym in historical_sub_df.columns:
                total_val += qty * historical_sub_df[sym].iloc[-1]

        if selected_stocks:
            effective_pool = total_val * multiplier
            alloc_per_stock = effective_pool / len(selected_stocks)
            current_holdings = {}
            current_cash = total_val

            for sym in selected_stocks:
                if sym in historical_sub_df.columns:
                    stk_price = historical_sub_df[sym].iloc[-1]
                    if stk_price > 0:
                        qty = int(alloc_per_stock // stk_price)
                        if qty > 0:
                            current_holdings[sym] = qty
                            current_cash -= (qty * stk_price)
        elif (
            strat_config.get("use_market_trend_filter", False)
            and strat_config.get("market_trend_exit_on_bearish", True)
            and not _market_trend_is_bullish(strat_config, as_of_date=dt)
        ):
            # Sell all holdings into cash at this rebalance when the chosen
            # index trend is bearish; the next bullish rebalance may re-enter.
            current_holdings = {}
            current_cash = total_val

        portfolio_history.append({"Date": dt, "Portfolio Value": total_val})

    if not portfolio_history:
        return None, None, None

    df_res = pd.DataFrame(portfolio_history).set_index("Date")
    return df_res, holdings_history, prices_df

# ==========================================
# NAVIGATION HEADER
# ==========================================
nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([1.2, 1.8, 1.3, 2.0])
with nav_col1:
    if st.button("💻 DASHBOARD", use_container_width=True, type="primary" if st.session_state.navigation_tab == "DASHBOARD" else "secondary"):
        st.session_state.navigation_tab = "DASHBOARD"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col2:
    if st.button("➕ INVESTING STRATEGY", use_container_width=True, type="primary" if st.session_state.navigation_tab == "STRATEGY_BUILDER" else "secondary"):
        st.session_state.navigation_tab = "STRATEGY_BUILDER"
        st.session_state.editing_strategy_name = None
        st.rerun()

with nav_col3:
    if st.button("📈 BACKTEST ENGINE", use_container_width=True, type="primary" if st.session_state.navigation_tab == "BACKTEST" else "secondary"):
        st.session_state.navigation_tab = "BACKTEST"
        st.rerun()

with nav_col4:
    if st.button("🧾 INDEX CONSTITUENTS", use_container_width=True, type="primary" if st.session_state.navigation_tab == "INDEX_CONSTITUENTS" else "secondary"):
        st.session_state.navigation_tab = "INDEX_CONSTITUENTS"
        st.session_state.active_strategy_view = None
        st.rerun()

st.markdown("---")

render_fyers_connection_panel()

with st.expander("💾 Backups & Downloads"):
    bkp_col1, bkp_col2 = st.columns(2)
    with bkp_col1:
        export_json = json.dumps(st.session_state.strategies, indent=2, default=str)
        st.download_button("📥 Download strategies backup (JSON)", export_json,
                            "strategies_backup.json", "application/json")
        uploaded = st.file_uploader("📤 Restore strategies from a backup file", type="json")
        if uploaded:
            try:
                restored = json.load(uploaded)
                st.session_state.strategies.update(restored)
                st.success(f"Restored {len(restored)} strategy(ies). Switch pages to see them.")
            except Exception as e:
                st.error(f"Couldn't read that file: {e}")
    with bkp_col2:
        if momentum_data.PARQUET_PATH.exists():
            prices_for_download = momentum_data.load_archive_prices("Close")
            st.download_button("📥 Download cached price history (CSV)",
                                prices_for_download.to_csv().encode("utf-8"),
                                "nifty500_price_archive.csv", "text/csv")
        else:
            st.caption("Price archive not loaded yet — run a scan or backtest first.")

# ==========================================
# PAGE 1: PORTFOLIO DASHBOARD & DRILL-DOWN
# ==========================================
if st.session_state.navigation_tab == "DASHBOARD":

    if st.session_state.active_strategy_view is not None:
        strat_name = st.session_state.active_strategy_view
        strat = st.session_state.strategies.get(strat_name)

        if not strat:
            st.session_state.active_strategy_view = None
            st.rerun()

        metrics = calculate_strategy_metrics(strat)
        days_left = calculate_days_to_rebalance(strat.get("rebalance_day", 1))

        top_col1, top_col2, top_col3 = st.columns([2, 5, 3])
        with top_col1:
            if st.button("❮ Back to Dashboard"):
                st.session_state.active_strategy_view = None
                st.rerun()
        with top_col3:
            current_status = strat["status"]
            btn_label = "⏸️ Pause Strategy" if current_status == "Active" else "▶️ Start Strategy"
            if st.button(btn_label):
                strat["status"] = "Paused" if current_status == "Active" else "Active"
                st.rerun()

        st.subheader(f"Strategy: {strat_name}")
        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
        st.markdown(f"Status: <span class='{status_class}'>{strat['status']}</span> | **Groups:** {', '.join(strat.get('groups', ['Nifty 500']))} | **Multiplier:** {strat.get('allocation_multiplier', 1.0)}x", unsafe_allow_html=True)

        fyers_status = st.session_state.get("_fyers_status")
        if fyers_status == "live":
            st.caption("🟢 Prices below are live from Fyers where available.")
        elif fyers_status:
            st.caption(f"🟡 Fyers live prices {fyers_status} — showing cached closes instead.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Allocated Capital", f"₹{metrics['allocated']:,.2f}")
        m2.metric("Uninvested Cash", f"₹{metrics['cash_reserve']:,.2f}")
        m3.metric("Unrealized P&L", f"₹{metrics['unrealized_pnl']:,.2f}")
        m4.metric("Total P&L & Returns", f"₹{metrics['total_pnl']:,.2f}", delta=f"{metrics['returns_pct']:.2f}%")

        st.markdown("---")
        st.subheader("🔍 Live Stock Scanner & Rebalance Engine")

        scan_col1, scan_col2 = st.columns([2, 3])
        with scan_col1:
            run_scan = st.button("🚀 Run Live Stock Scan for this Strategy", type="primary", use_container_width=True)

        if run_scan:
            with st.spinner("Executing strategy parameters against selected asset groups..."):
                current_symbols = [p["Symbol"] for p in strat.get("positions", [])]
                scanned_tickers = run_strategy_stock_scanner(strat, current_holdings=current_symbols, as_of_date=pd.Timestamp.today())
                st.session_state[f"scanned_results_{strat_name}"] = scanned_tickers

        scanned_results = st.session_state.get(f"scanned_results_{strat_name}", None)

        if scanned_results is not None:
            st.success(f"Scan Complete! Found **{len(scanned_results)}** target portfolio stocks across selected groups based on strategy rules.")

            sc_col1, sc_col2 = st.columns(2)
            with sc_col1:
                st.markdown("#### 📌 Current Holding Stocks")
                if metrics["positions_data"]:
                    st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
                else:
                    st.info("No active holdings currently allocated.")

            with sc_col2:
                st.markdown("#### 🔄 Updated / Scanned Portfolio Stocks")
                prices_df = fetch_market_data(scanned_results, period="5d")
                live_or_close = apply_live_price_overlay(prices_df, scanned_results)
                held_symbols = {p["Symbol"] for p in strat.get("positions", [])}
                scanned_rows = []
                for ticker in scanned_results:
                    cmp = live_or_close.get(ticker, 0.0)
                    scanned_rows.append({
                        "Symbol": ticker.replace(".NS", ""),
                        "CMP": f"₹{cmp:,.2f}",
                        "Status": "Held (in buffer)" if ticker in held_symbols else "Entry Target"
                    })
                st.dataframe(pd.DataFrame(scanned_rows), use_container_width=True)

            if st.button("⚡ Execute Rebalance & Reinvest Capital", type="primary", use_container_width=True):
                existing_positions = {p["Symbol"]: p for p in strat.get("positions", [])}
                retained_symbols = [s for s in scanned_results if s in existing_positions]
                new_symbols = [s for s in scanned_results if s not in existing_positions]
                exited_symbols = [s for s in existing_positions if s not in scanned_results]

                price_lookup_df = fetch_market_data(scanned_results + exited_symbols, period="5d")
                live_or_close = apply_live_price_overlay(price_lookup_df, scanned_results + exited_symbols)

                freed_cash = 0.0
                newly_realized = 0.0
                for sym in exited_symbols:
                    pos = existing_positions[sym]
                    exit_price = live_or_close.get(sym, pos["Buy Price"])
                    proceeds = exit_price * pos["Buy Qty"]
                    freed_cash += proceeds
                    newly_realized += proceeds - (pos["Buy Price"] * pos["Buy Qty"])

                new_positions = [existing_positions[s] for s in retained_symbols]

                investable_for_new = (metrics["cash_reserve"] + freed_cash) * strat.get("allocation_multiplier", 1.0)
                per_new_alloc = investable_for_new / len(new_symbols) if new_symbols else 0.0

                for ticker in new_symbols:
                    cmp = live_or_close.get(ticker)
                    if not cmp or cmp <= 0:
                        continue
                    qty = int(per_new_alloc // cmp)
                    if qty > 0:
                        new_positions.append({
                            "Symbol": ticker,
                            "Buy Qty": qty,
                            "Buy Price": cmp,
                            "Entry Date": datetime.now().strftime("%Y-%m-%d")
                        })

                strat["positions"] = new_positions
                strat["realized_pnl"] = strat.get("realized_pnl", 0.0) + newly_realized
                st.session_state[f"scanned_results_{strat_name}"] = None
                st.success(f"Rebalanced '{strat_name}': kept {len(retained_symbols)} in the buffer, exited {len(exited_symbols)}, entered {len(new_symbols)} new.")
                st.rerun()

        else:
            st.markdown("#### 📋 Current Portfolio Holdings")
            if metrics["positions_data"]:
                st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
            else:
                st.info("No active scanned holdings for this strategy. Click 'Run Live Stock Scan' above.")

    else:
        st.title("MY PORTFOLIO")

        tot_allocated = 0.0
        tot_holding_val = 0.0
        tot_realized_pnl = 0.0
        tot_unrealized_pnl = 0.0

        for name, strat in st.session_state.strategies.items():
            m = calculate_strategy_metrics(strat)
            tot_allocated += m["allocated"]
            tot_holding_val += m["current_holding_val"]
            tot_realized_pnl += m["realized_pnl"]
            tot_unrealized_pnl += m["unrealized_pnl"]

        tot_pnl = tot_realized_pnl + tot_unrealized_pnl
        tot_returns_pct = (tot_pnl / tot_allocated * 100) if tot_allocated > 0 else 0.0
        tot_portfolio_val = tot_allocated + tot_pnl

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Allocated Capital", f"₹{tot_allocated:,.2f}")
        kpi2.metric("Total Realized P&L", f"₹{tot_realized_pnl:,.2f}")
        kpi3.metric("Total Portfolio P&L", f"₹{tot_pnl:,.2f}", delta=f"{tot_returns_pct:.2f}%")

        kpi4, kpi5, kpi6 = st.columns(3)
        kpi4.metric("Current Holdings Value", f"₹{tot_holding_val:,.2f}")
        kpi5.metric("Total Unrealized P&L", f"₹{tot_unrealized_pnl:,.2f}")
        kpi6.metric("Total Portfolio Balance", f"₹{tot_portfolio_val:,.2f}")

        st.markdown("---")
        st.subheader("Real Strategies")

        strat_cols = st.columns(3)
        keys = list(st.session_state.strategies.keys())

        for i, name in enumerate(keys):
            strat = st.session_state.strategies[name]
            sm = calculate_strategy_metrics(strat)
            days_left = calculate_days_to_rebalance(strat.get("rebalance_day", 1))
            col = strat_cols[i % 3]

            with col:
                with st.container(border=True):
                    c_head1, c_head2 = st.columns([3, 2])
                    with c_head1:
                        st.markdown(f"### **{name}**")
                        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
                        st.markdown(f"<span class='{status_class}'>{strat['status']}</span>", unsafe_allow_html=True)

                    with c_head2:
                        act_col1, act_col2, act_col3 = st.columns(3)
                        is_active = strat["status"] == "Active"

                        if act_col1.button("⏸️" if is_active else "▶️", key=f"toggle_{name}", help="Start/Stop Strategy"):
                            strat["status"] = "Paused" if is_active else "Active"
                            st.rerun()

                        if act_col2.button("✏️", key=f"edit_{name}", help="Edit Strategy Config"):
                            st.session_state.editing_strategy_name = name
                            st.session_state.navigation_tab = "STRATEGY_BUILDER"
                            st.rerun()

                        if act_col3.button("🗑️", key=f"del_{name}", help="Delete Strategy"):
                            del st.session_state.strategies[name]
                            st.rerun()

                    st.caption(f"Groups: {', '.join(strat.get('groups', ['Nifty 500']))} | Multiplier: {strat.get('allocation_multiplier', 1.0)}x")
                    st.caption(f"Rebalance Date: Day {strat.get('rebalance_day', 1)} | {days_left} days left")
                    st.markdown(f"**Allocated:** ₹{sm['allocated']:,.2f}")
                    st.markdown(f"**Realized P&L:** ₹{sm['realized_pnl']:,.2f}")
                    st.markdown(f"**Unrealized P&L:** ₹{sm['unrealized_pnl']:,.2f}")

                    pnl_color = "green" if sm['total_pnl'] >= 0 else "red"
                    st.markdown(f"**P&L / Returns:** <span style='color:{pnl_color}; font-weight:bold;'>₹{sm['total_pnl']:,.2f} ({sm['returns_pct']:.2f}%)</span>", unsafe_allow_html=True)

                    if st.button("🔍 Open Scanner & Details", key=f"view_detail_{name}", use_container_width=True):
                        st.session_state.active_strategy_view = name
                        st.rerun()

# ==========================================
# PAGE 2: STRATEGY BUILDER & CONFIG
# ==========================================
elif st.session_state.navigation_tab == "STRATEGY_BUILDER":
    edit_mode = st.session_state.editing_strategy_name is not None
    edit_strat = st.session_state.strategies.get(st.session_state.editing_strategy_name, {}) if edit_mode else {}

    st.subheader("✏️ EDIT STRATEGY ENGINE" if edit_mode else "➕ CREATE STRATEGY ENGINE")

    st.markdown("#### Investment & Capital Details")

    inv_r1, inv_r2, inv_r3 = st.columns(3)
    with inv_r1:
        st_type = st.radio("Strategy Type :", ["Real", "Virtual"], index=0 if edit_strat.get("type") == "Real" else 1, horizontal=True)
        default_name = st.session_state.editing_strategy_name if edit_mode else "New Momentum Strategy"
        strat_name_input = st.text_input("Strategy Name :", value=default_name, disabled=edit_mode)
    with inv_r2:
        tot_alloc = st.number_input("Total Allocation Capital (₹) :", value=float(edit_strat.get("allocated", 100000.0)), step=10000.0)
        rebal_freq = st.selectbox("Rebalance Frequency :", ["Monthly", "Weekly", "Quarterly"])
    with inv_r3:
        rebal_day = st.number_input("Rebalance Day of Month (1-28) :", value=int(edit_strat.get("rebalance_day", 1)), min_value=1, max_value=28)
        alloc_multiplier = st.number_input("Allocation Multiplier (X times) :", value=float(edit_strat.get("allocation_multiplier", 1.0)), min_value=0.1, max_value=10.0, step=0.1)

    st.markdown("---")
    st.markdown("#### Asset Universe Selection (Groups Checkboxes)")

    existing_groups = edit_strat.get("groups", ["Nifty 500"])
    grp_c1, grp_c2, grp_c3 = st.columns(3)
    with grp_c1:
        grp_nifty500 = st.checkbox("Nifty 500 Universe", value=("Nifty 500" in existing_groups))
        grp_nifty200 = st.checkbox("Nifty 200 Universe", value=("Nifty 200" in existing_groups))
    with grp_c2:
        grp_all_etfs = st.checkbox("All ETFs Universe", value=("All ETFs" in existing_groups))
        grp_gold_etf = st.checkbox("Gold ETF Only", value=("Gold ETF" in existing_groups))
    with grp_c3:
        grp_liquidbees = st.checkbox("LiquidBEES Only", value=("LiquidBEES" in existing_groups))
        grp_gov_bond = st.checkbox("Gov Bond Only", value=("Gov Bond" in existing_groups))

    etf_universe = {"etfs": {}}
    try:
        etf_universe = fetch_etf_and_index_universe()
    except Exception:
        pass
    etf_categories = sorted(etf_universe["etfs"].keys())
    selected_etf_categories = []
    if etf_categories:
        total_etfs = sum(len(v) for v in etf_universe["etfs"].values())
        st.caption(f"Full ETF universe ({total_etfs} found via your broker) — add any categories:")
        selected_etf_categories = st.multiselect(
            "Additional ETF categories", etf_categories,
            default=[c for c in etf_categories if c in existing_groups],
        )
    else:
        st.caption("Full ETF universe not available yet (needs a working broker connection) — the checkboxes above still work.")

    st.markdown("#### ETF Groups from Your Uploaded Lists")
    st.caption(
        "These same curated groups are available in Index Constituents and can be used "
        "for strategy scanning, portfolio execution and backtesting."
    )
    selected_uploaded_etf_groups = st.multiselect(
        "Uploaded ETF groups",
        options=ETF_GROUP_NAMES,
        default=[g for g in ETF_GROUP_NAMES if g in existing_groups],
        key=f"strategy_uploaded_etf_groups_{st.session_state.editing_strategy_name or 'new'}",
    )

    st.markdown("---")
    st.markdown("#### Ranking, Entry & Exit Parameters")

    rank_c1, rank_c2 = st.columns(2)
    with rank_c1:
        entry_rank = st.number_input("Entry Rank Stocks (Top N Target) :", value=int(edit_strat.get("entry_rank", 10)), min_value=1, max_value=50)
    with rank_c2:
        exit_rank = st.number_input("Exit Rank Threshold :", value=int(edit_strat.get("exit_rank", 20)), min_value=1, max_value=100, help="A held stock is only sold once it ranks worse than this.")

    st.markdown("---")
    st.markdown("#### Technical Features & Indicator Rules")

    sd_r1, sd_r2, sd_r3 = st.columns(3)
    with sd_r1:
        st.caption("Lookback Periods (Days) :")
        existing_periods = edit_strat.get("period_days", [252, 120, 60])
        p252 = st.checkbox("252 Days (1 Year)", value=(252 in existing_periods))
        p120 = st.checkbox("120 Days (6 Months)", value=(120 in existing_periods))
        p90 = st.checkbox("90 Days (3 Months)", value=(90 in existing_periods))
        p60 = st.checkbox("60 Days (2 Months)", value=(60 in existing_periods))

    with sd_r2:
        st.caption("Proximity Filters (% from Period High/Low) :")
        pct_high = st.number_input("% Within Period High :", value=float(edit_strat.get("pct_from_high", 15.0)))
        pct_low = st.number_input("% Above Period Low :", value=float(edit_strat.get("pct_from_low", 0.0)))
        min_price = st.number_input("Minimum Price (₹) :", value=float(edit_strat.get("min_price", 10.0)), min_value=0.0)

    with sd_r3:
        ma_options = ["None", "200 EMA", "100 EMA", "200 SMA", "50 SMA"]
        ma_val = edit_strat.get("moving_average", "200 EMA")
        ma_idx = ma_options.index(ma_val) if ma_val in ma_options else 1
        selected_ma = st.selectbox("Moving Average Filter :", ma_options, index=ma_idx)

        use_rs = st.checkbox("Enable Relative Strength Ratio Filter", value=edit_strat.get("use_rs", True))
        rs_benchmark = st.selectbox("RS Benchmark Ratio :", ["Nifty 500 / G-Sec", "Nifty 50 / Liquid ETF"])

    st.markdown("#### Optional Relative Momentum Filter")
    use_relative_momentum = st.checkbox(
        "Enable Relative Momentum Filter",
        value=bool(edit_strat.get("use_relative_momentum_filter", False)),
        help="Compare each selected stock's price/index ratio against its own moving average. Only stocks with RS above the selected average remain eligible."
    )
    relative_index_options = list(MAJOR_MARKET_INDICES.keys())
    relative_index_saved = edit_strat.get("relative_momentum_index", "Nifty 50")
    relative_momentum_index = st.selectbox(
        "Relative Momentum Benchmark Index (Denominator)",
        relative_index_options,
        index=relative_index_options.index(relative_index_saved) if relative_index_saved in relative_index_options else 0,
        disabled=not use_relative_momentum,
        key="relative_momentum_index_select",
    )
    relative_ma_options = ["SMA", "EMA"]
    relative_ma_saved = edit_strat.get("relative_momentum_ma_type", "SMA")
    relative_momentum_ma_type = st.selectbox(
        "Relative Strength Indicator",
        relative_ma_options,
        index=relative_ma_options.index(relative_ma_saved) if relative_ma_saved in relative_ma_options else 0,
        disabled=not use_relative_momentum,
        key="relative_momentum_ma_type_select",
    )
    relative_momentum_ma_period = st.number_input(
        "Relative Strength MA Period (Days)",
        min_value=2,
        max_value=500,
        value=int(edit_strat.get("relative_momentum_ma_period", 200)),
        step=1,
        disabled=not use_relative_momentum,
        key="relative_momentum_ma_period_input",
    )

    st.markdown("#### Optional Market Trend Filter")
    use_market_trend = st.checkbox(
        "Enable Market Trend Filter",
        value=bool(edit_strat.get("use_market_trend_filter", False)),
        help="Use a major index regime to control whether the strategy opens positions."
    )
    trend_index_options = list(MAJOR_MARKET_INDICES.keys())
    trend_index_saved = edit_strat.get("market_trend_index", "Nifty 50")
    trend_index = st.selectbox(
        "Reference Index",
        trend_index_options,
        index=trend_index_options.index(trend_index_saved) if trend_index_saved in trend_index_options else 0,
        disabled=not use_market_trend,
    )
    trend_indicator_options = ["Moving Average", "VStop"]
    trend_indicator_saved = edit_strat.get("market_trend_indicator", "Moving Average")
    trend_indicator = st.selectbox(
        "Trend Indicator",
        trend_indicator_options,
        index=trend_indicator_options.index(trend_indicator_saved) if trend_indicator_saved in trend_indicator_options else 0,
        disabled=not use_market_trend,
    )
    trend_ma_type = "EMA"
    trend_ma_period = 200
    trend_vstop_atr = 10
    trend_vstop_multiplier = 2.0
    if use_market_trend and trend_indicator == "Moving Average":
        trend_ma_c1, trend_ma_c2 = st.columns(2)
        with trend_ma_c1:
            trend_ma_type = st.selectbox(
                "Moving Average Type", ["EMA", "SMA"],
                index=["EMA", "SMA"].index(edit_strat.get("market_trend_ma_type", "EMA"))
                if edit_strat.get("market_trend_ma_type", "EMA") in ["EMA", "SMA"] else 0,
            )
        with trend_ma_c2:
            trend_ma_period = st.number_input(
                "Moving Average Period", min_value=2, max_value=500,
                value=int(edit_strat.get("market_trend_ma_period", 200)), step=1,
            )
    elif use_market_trend and trend_indicator == "VStop":
        trend_vs_c1, trend_vs_c2 = st.columns(2)
        with trend_vs_c1:
            trend_vstop_atr = st.number_input(
                "VStop ATR Period", min_value=2, max_value=100,
                value=int(edit_strat.get("market_trend_vstop_atr_period", 10)), step=1,
            )
        with trend_vs_c2:
            trend_vstop_multiplier = st.number_input(
                "VStop ATR Multiplier", min_value=0.5, max_value=10.0,
                value=float(edit_strat.get("market_trend_vstop_multiplier", 2.0)), step=0.5,
            )
    market_trend_exit_on_bearish = st.checkbox(
        "Exit existing positions when index trend turns bearish",
        value=bool(edit_strat.get("market_trend_exit_on_bearish", True)),
        disabled=not use_market_trend,
        help="When enabled, a bearish index trend closes held positions at the next strategy rebalance.",
    )

    st.markdown("---")
    save_label = "💾 Update Strategy Config" if edit_mode else "💾 Deploy New Strategy Profile"
    if st.button(save_label, type="primary", use_container_width=True):
        selected_groups = []
        if grp_nifty500: selected_groups.append("Nifty 500")
        if grp_nifty200: selected_groups.append("Nifty 200")
        if grp_all_etfs: selected_groups.append("All ETFs")
        if grp_gold_etf: selected_groups.append("Gold ETF")
        if grp_liquidbees: selected_groups.append("LiquidBEES")
        if grp_gov_bond: selected_groups.append("Gov Bond")
        selected_groups.extend(selected_etf_categories)
        selected_groups.extend(selected_uploaded_etf_groups)
        selected_groups = list(dict.fromkeys(selected_groups))

        selected_periods = []
        weights = []
        if p252:
            selected_periods.append(252)
            weights.append(0.4)
        if p120:
            selected_periods.append(120)
            weights.append(0.3)
        if p90:
            selected_periods.append(90)
            weights.append(0.2)
        if p60:
            selected_periods.append(60)
            weights.append(0.1)

        target_name = st.session_state.editing_strategy_name if edit_mode else strat_name_input

        st.session_state.strategies[target_name] = {
            "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
            "status": edit_strat.get("status", "Active"),
            "type": st_type,
            "allocated": float(tot_alloc),
            "realized_pnl": edit_strat.get("realized_pnl", 0.0),
            "rebalance_freq": rebal_freq,
            "rebalance_day": rebal_day,
            "groups": selected_groups if selected_groups else ["Nifty 500"],
            "allocation_multiplier": float(alloc_multiplier),
            "entry_rank": entry_rank,
            "exit_rank": exit_rank,
            "period_days": selected_periods if selected_periods else [252],
            "period_weights": weights if weights else [1.0],
            "pct_from_high": pct_high,
            "pct_from_low": pct_low,
            "moving_average": selected_ma,
            "use_rs": use_rs,
            "rs_benchmark": rs_benchmark,
            "min_price": float(min_price),
            "use_relative_momentum_filter": bool(use_relative_momentum),
            "relative_momentum_index": relative_momentum_index,
            "relative_momentum_ma_type": relative_momentum_ma_type,
            "relative_momentum_ma_period": int(relative_momentum_ma_period),
            "use_market_trend_filter": bool(use_market_trend),
            "market_trend_index": trend_index,
            "market_trend_indicator": trend_indicator,
            "market_trend_ma_type": trend_ma_type,
            "market_trend_ma_period": int(trend_ma_period),
            "market_trend_vstop_atr_period": int(trend_vstop_atr),
            "market_trend_vstop_multiplier": float(trend_vstop_multiplier),
            "market_trend_exit_on_bearish": bool(market_trend_exit_on_bearish),
            "positions": edit_strat.get("positions", [])
        }

        st.session_state.editing_strategy_name = None
        st.session_state.navigation_tab = "DASHBOARD"
        st.rerun()

# ==========================================
# PAGE 3: BACKTEST ENGINE SECTION
# ==========================================
elif st.session_state.navigation_tab == "BACKTEST":
    st.title("📈 BACKTEST STUDIO")
    st.caption("Simulate historical momentum performance with periodic rebalancing, entry/exit rankings, moving average trend rules, asset groups, multiplier scaling, and RS regime filters.")

    strategy_names = list(st.session_state.strategies.keys())
    selected_backtest_strat = st.selectbox("Select Strategy to Backtest :", strategy_names)

    bt_strat_config = st.session_state.strategies[selected_backtest_strat]

    bt_c1, bt_c2, bt_c3 = st.columns(3)
    with bt_c1:
        initial_cap = st.number_input("Initial Backtest Capital (₹) :", value=100000.0, step=10000.0)
    with bt_c2:
        start_d = st.date_input("Start Date :", value=datetime.now() - timedelta(days=365*3))
    with bt_c3:
        end_d = st.date_input("End Date :", value=datetime.now())

    use_pit = st.checkbox(
        "Use point-in-time Nifty 500 membership (970 symbols incl. delisted names, avoids survivorship bias)",
        value=True,
        help="When on, a stock is only eligible to be ranked/held during the months it was actually a Nifty 500 constituent, using a calendar built from NSE's official log + Wayback Machine snapshots."
    )

    run_bt_btn = st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True)

    if run_bt_btn:
        with st.spinner("Running historical rebalance simulation against market data..."):
            bt_results, holdings_history, bt_prices_df = run_backtest_simulation(
                bt_strat_config, initial_cap, start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d"),
                use_point_in_time=use_pit,
            )

            if bt_results is not None and not bt_results.empty:
                st.session_state["_last_backtest"] = (bt_results, holdings_history, bt_prices_df)
                st.success("Backtest Completed Successfully!")

                final_val = float(bt_results["Portfolio Value"].iloc[-1])
                total_return_pct = ((final_val - initial_cap) / initial_cap) * 100
                peak = bt_results["Portfolio Value"].cummax()
                drawdown = (bt_results["Portfolio Value"] - peak) / peak
                max_drawdown_pct = drawdown.min() * 100

                res_m1, res_m2, res_m3 = st.columns(3)
                res_m1.metric("Final Portfolio Value", f"₹{final_val:,.2f}")
                res_m2.metric("Total Backtest Return", f"{total_return_pct:.2f}%")
                res_m3.metric("Max Drawdown", f"{max_drawdown_pct:.2f}%")

                st.markdown("#### Portfolio Value Equity Curve")
                st.line_chart(bt_results["Portfolio Value"])

                st.markdown("#### Historical Monthly Portfolio Curve Data")
                st.dataframe(bt_results, use_container_width=True)
            else:
                st.error("Insufficient market data for the selected timeframe. Try selecting a broader timeframe.")


# ==========================================
# PAGE 4: INDEX CONSTITUENTS
# ==========================================
elif st.session_state.navigation_tab == "INDEX_CONSTITUENTS":
    st.title("🧾 INDEX CONSTITUENTS")
    st.caption(
        "Choose an index to see its published constituent companies. "
        "This section is separate from the strategy scanner and backtesting engine."
    )

    selector_col1, selector_col2, action_col = st.columns([1, 1.5, 1])
    with selector_col1:
        index_category = st.selectbox(
            "Index Category",
            options=list(INDEX_CATEGORIES.keys()),
            key="constituent_index_category",
        )
    with selector_col2:
        selected_index = st.selectbox(
            "Select Index",
            options=list(INDEX_CATEGORIES[index_category].keys()),
            key=f"constituent_index_selector_{index_category}",
        )
    with action_col:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh Constituents", use_container_width=True):
            fetch_index_constituents.clear()
            if index_category == ALL_ETF_CATEGORY:
                fetch_all_etfs_and_bees.clear()
            st.rerun()

    st.caption(
        "Constituents are read from published Nifty Indices/NSE CSV files and cached "
        "for up to 6 hours. You do not need to connect FYERS to view this list."
    )

    try:
        with st.spinner(f"Loading {selected_index} constituents..."):
            all_constituents, source_url = fetch_index_constituents(
                index_category, selected_index
            )
    except Exception as exc:
        st.error(str(exc))
    else:
        industry_count = all_constituents["Industry"].replace("", pd.NA).nunique()
        search_query = st.text_input(
            "Search constituents",
            placeholder="Type a company name, symbol, industry or ISIN...",
            key="constituent_search_query",
        )

        visible_constituents = all_constituents.copy()
        if search_query.strip():
            search_mask = (
                visible_constituents.fillna("")
                .astype(str)
                .apply(
                    lambda column: column.str.contains(
                        search_query.strip(), case=False, regex=False
                    )
                )
                .any(axis=1)
            )
            visible_constituents = visible_constituents.loc[search_mask]

        metric1, metric2, metric3 = st.columns(3)
        metric1.metric(
            "Total ETFs & BEES" if index_category == ALL_ETF_CATEGORY else "Total Constituents",
            f"{len(all_constituents):,}",
        )
        metric2.metric("Matching Search", f"{len(visible_constituents):,}")
        if index_category == ALL_ETF_CATEGORY:
            metric3.metric("Universe", "All listed ETFs & BEES")
        else:
            metric3.metric("Industries Represented", f"{industry_count:,}")

        st.markdown(f"#### {selected_index} — Constituent List")
        st.dataframe(
            visible_constituents,
            use_container_width=True,
            hide_index=True,
            height=600,
        )

        safe_index_name = (
            selected_index.lower()
            .replace("&", "and")
            .replace(" ", "_")
            .replace("/", "_")
        )
        st.download_button(
            "📥 Download visible constituents (CSV)",
            data=visible_constituents.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{safe_index_name}_constituents.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown(f"Source: [Official constituent CSV]({source_url})")
