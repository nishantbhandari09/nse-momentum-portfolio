import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import base64
from nacl.public import PublicKey, SealedBox
from io import StringIO
from datetime import datetime, timedelta

from fyers_auth import (
    get_login_url,
    exchange_code_for_tokens,
    get_authenticated_fyers,
    create_signed_state,
    validate_signed_state,
    FyersLoginError,
)
import momentum_data
import index_history_data
import fyers_history_data
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

    # Fetch each selected index's own official constituent list. Do not
    # approximate Nifty 200 by taking the first 200 rows of an alphabetically
    # sorted Nifty 500 file.
    equity_indices = [
        name for name in ("Nifty 500", "Nifty 200") if name in groups
    ]
    for index_name in equity_indices:
        try:
            constituents, _source_url = fetch_index_constituents("Broad Market", index_name)
            if "Yahoo Ticker" in constituents.columns:
                tickers.extend(
                    constituents["Yahoo Ticker"].dropna().astype(str).str.strip().tolist()
                )
            elif "Symbol" in constituents.columns:
                tickers.extend(
                    f"{str(symbol).strip()}.NS"
                    for symbol in constituents["Symbol"].dropna().unique()
                )
        except Exception:
            if index_name == "Nifty 500":
                # Retain the pre-existing mirror fallback for Nifty 500 if
                # official endpoints are temporarily unavailable.
                try:
                    mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
                    mirror = pd.read_csv(mirror_url)
                    if "Symbol" in mirror.columns:
                        tickers.extend(
                            f"{str(symbol).strip()}.NS"
                            for symbol in mirror["Symbol"].dropna().unique()
                        )
                except Exception:
                    tickers.extend([
                        "RELIANCE.NS", "TCS.NS", "INFY.NS",
                        "HDFCBANK.NS", "ICICIBANK.NS",
                    ])
            # If Nifty 200's list is unavailable, fail closed for that group
            # instead of silently using an unrelated alphabetical subset.

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
    """Load price history exclusively from the persisted FYERS OHLCV archive.

    The period arguments remain for compatibility with existing scanner and
    backtest callers. The archive contains the full history available from FYERS;
    no Yahoo/NSE price fallback is used, and no future values are backfilled.
    """
    ticker_values = list(tickers) if tickers is not None else []
    requested = list(dict.fromkeys(
        [str(t).strip() for t in ticker_values if str(t).strip()]
        + ALWAYS_FETCH_OVERLAY_TICKERS
        + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]
    ))
    prices = fyers_history_data.load_close_prices(requested)
    if prices is None or prices.empty:
        return pd.DataFrame()
    prices = prices.loc[:, ~prices.isna().all(axis=0)].sort_index()
    # Forward-fill only after an instrument has traded. This aligns valuations
    # across non-trading sessions without inventing any earlier prices.
    return prices.ffill()

# ==========================================
# FYERS - LIVE PRICE OVERLAY
# ==========================================
def get_fyers_client():
    if "fyers_access_token" not in st.session_state:
        raise RuntimeError("not connected yet this session — use the Fyers Connection panel above")
    return get_authenticated_fyers(st.session_state["fyers_client_id"], st.session_state["fyers_access_token"])


def trigger_fyers_history_update_on_login(access_token: str, app_id: str):
    """Securely hand the fresh login token to GitHub Actions and start a history refresh.

    The token is encrypted with GitHub's repository public key before it is
    uploaded as an Actions secret. It is never placed in a workflow input,
    repository file, log message or client payload.
    """
    # This Streamlit app is publicly reachable, so only the intended FYERS
    # account may update the repository's credentials and historical database.
    allowed_user_id = str(st.secrets.get("FYERS_SYNC_ALLOWED_USER_ID") or "").strip().upper()
    try:
        profile_response = get_authenticated_fyers(app_id, access_token).get_profile()
        profile_data = profile_response.get("data", {}) if isinstance(profile_response, dict) else {}
        logged_in_user_id = str(
            profile_data.get("fy_id")
            or profile_data.get("fyId")
            or profile_data.get("user_id")
            or ""
        ).strip().upper()
    except Exception:
        logged_in_user_id = ""

    if not logged_in_user_id:
        return (
            False,
            "FYERS login succeeded, but the app could not verify the logged-in FYERS account ID. "
            "No historical update was started.",
        )
    if not allowed_user_id:
        return (
            False,
            f"Your FYERS account ID is {logged_in_user_id}. Add it as FYERS_SYNC_ALLOWED_USER_ID "
            "in Streamlit Secrets to authorize this account for automatic history updates, then log in again.",
        )
    if logged_in_user_id != allowed_user_id:
        return (
            False,
            "FYERS is connected, but this account is not the one authorized to update the saved history. "
            "No GitHub credentials were changed.",
        )

    github_token = str(
        st.secrets.get("GITHUB_ACTIONS_TOKEN")
        or st.secrets.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if not github_token:
        return (
            False,
            "Your FYERS account is verified, but automatic historical-data saving is not enabled yet. "
            "Add GITHUB_ACTIONS_TOKEN to Streamlit Secrets once; this setup does not require your FYERS PIN."
        )

    owner, repo = "nishantbhandari09", "nse-momentum-portfolio"
    api_root = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        key_response = requests.get(
            f"{api_root}/actions/secrets/public-key",
            headers=headers,
            timeout=20,
        )
        key_response.raise_for_status()
        key_data = key_response.json()
        public_key = PublicKey(base64.b64decode(key_data["key"]))
        sealed_box = SealedBox(public_key)
        # Store the same App ID used by this completed login and its fresh
        # session token. GitHub receives encrypted values only.
        for secret_name, secret_value in (
            ("FYERS_APP_ID", app_id),
            ("FYERS_ACCESS_TOKEN", access_token),
        ):
            encrypted_value = base64.b64encode(
                sealed_box.encrypt(str(secret_value).encode("utf-8"))
            ).decode("ascii")
            secret_response = requests.put(
                f"{api_root}/actions/secrets/{secret_name}",
                headers=headers,
                json={
                    "encrypted_value": encrypted_value,
                    "key_id": key_data["key_id"],
                },
                timeout=20,
            )
            secret_response.raise_for_status()

        dispatch_response = requests.post(
            f"{api_root}/dispatches",
            headers=headers,
            json={
                "event_type": "fyers-login",
                "client_payload": {"source": "streamlit-login"},
            },
            timeout=20,
        )
        dispatch_response.raise_for_status()
        return (
            True,
            "Historical-data refresh has been triggered in GitHub Actions using this login. "
            "The workflow will save its progress back to the repository when finished.",
        )
    except Exception as exc:
        # Do not surface request bodies or any credential values in the app.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"GitHub returned HTTP {status}." if status else "The GitHub request could not be completed."
        return (
            False,
            "FYERS login succeeded, but automatic historical-data refresh could not be started. "
            f"{detail} Check that GITHUB_ACTIONS_TOKEN has repository Secrets read/write and Contents write permission.",
        )


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
                token_bundle = exchange_code_for_tokens(app_id, secret_key, redirect_uri, callback_code)
            st.session_state["fyers_access_token"] = token_bundle["access_token"]
            if token_bundle.get("refresh_token"):
                st.session_state["fyers_refresh_token"] = token_bundle["refresh_token"]
            st.session_state["fyers_client_id"] = app_id
            st.session_state["fyers_connected_at"] = datetime.now().isoformat(timespec="seconds")
            update_ok, update_message = trigger_fyers_history_update_on_login(
                token_bundle["access_token"], app_id
            )
            st.session_state["_fyers_history_update_status"] = {
                "ok": update_ok,
                "message": update_message,
            }
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
            st.success("Connected to FYERS for this session. Live quotes are fetched from this session when you scan or value holdings.")
            update_status = st.session_state.get("_fyers_history_update_status")
            if update_status:
                if update_status.get("ok"):
                    st.success(update_status.get("message", "Historical-data refresh triggered."))
                else:
                    st.warning(update_status.get("message", "Automatic historical-data refresh is not configured."))
            if not (
                st.secrets.get("GITHUB_ACTIONS_TOKEN")
                or st.secrets.get("GITHUB_TOKEN")
            ):
                st.info(
                    "One-time setup for saved history: add GITHUB_ACTIONS_TOKEN in Streamlit Secrets "
                    "using a fine-grained token limited to this repository (Secrets read/write and Contents "
                    "read/write). Also set FYERS_SYNC_ALLOWED_USER_ID to the verified FYERS account ID shown "
                    "after your first login. After setup, each login from that account triggers a history "
                    "refresh; your FYERS PIN is not needed."
                )
            connected_at = st.session_state.get("fyers_connected_at")
            if connected_at:
                st.caption(f"Connected at {connected_at}")
            st.caption(
                "Historical downloads are triggered automatically after login once "
                "GITHUB_ACTIONS_TOKEN has been configured in Streamlit Secrets. No FYERS PIN is required."
            )
            if st.button("Disconnect FYERS"):
                st.session_state.pop("fyers_access_token", None)
                st.session_state.pop("fyers_refresh_token", None)
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
        # VStop uses only the persisted FYERS OHLC series. If the archive is
        # missing, fail closed instead of substituting another price provider.
        ohlc = index_history_data.load_index_ohlc(ticker)
        if ohlc.empty:
            return False
        ohlc = ohlc.set_index("Date")
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

    # Backtest calls pass a historical price matrix that also contains benchmark
    # indices and always-fetched overlays. Keep ETF-only backtests inside the
    # exact selected ETF groups; equity universes are filtered by point-in-time
    # membership below. Neither benchmarks nor unselected ETFs are candidates.
    has_equity_group = any(g in selected_groups for g in ("Nifty 500", "Nifty 200"))
    if candidate_tickers is not None:
        filtered_df = filtered_df.loc[:, [c for c in filtered_df.columns if c in candidate_tickers]]
    elif has_equity_group and (membership_calendar is None or membership_calendar.empty):
        # Without a usable PIT ledger, the documented fallback is today's chosen
        # index universe applied historically—not every cached ticker/benchmark.
        selected_assets = set(get_trusted_tickers_by_group(selected_groups))
        selected_assets.update(get_selected_etf_tickers(selected_groups))
        filtered_df = filtered_df.loc[:, [
            c for c in filtered_df.columns if c in selected_assets
        ]]
    elif not has_equity_group:
        selected_assets = get_selected_etf_tickers(selected_groups)
        filtered_df = filtered_df.loc[:, [
            c for c in filtered_df.columns if c in selected_assets
        ]]

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
        # A historical price matrix includes indices and every cached ETF. Only
        # instruments explicitly selected by the strategy, or symbols represented
        # in this index's membership ledger and active on the signal date, may rank.
        membership_symbols = {
            str(symbol).strip().upper()
            for symbol in membership_calendar["symbol"].dropna().tolist()
        }
        eligible_cols = [
            c for c in filtered_df.columns
            if not c.startswith("^")
            and (
                c in selected_etf_tickers
                or (
                    c.upper() in membership_symbols
                    and momentum_data.was_member(c, as_of_date, membership_calendar)
                )
            )
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
            fyers_history_data.load_all_symbols()
            + get_trusted_tickers_by_group(selected_groups)
        ))
        # Prefer the committed reconstructed Nifty 500 membership intervals. The
        # public interval source does not provide an exact Nifty 200 series, so
        # Nifty 200 backtests currently use the broader Nifty 500 calendar as a
        # proxy. The app keeps this limitation explicit rather than inventing
        # an exact historical Nifty 200 membership list.
        membership_target = "Nifty 500" if "Nifty 500" in selected_groups else "Nifty 200"
        membership_calendar = index_history_data.get_membership_calendar(membership_target)
        nifty200_membership_is_approximate = (
            membership_target == "Nifty 200" and membership_calendar.empty
        )

        # The validated interval source begins in 2014 for Nifty 500. Preserve the
        # older calendar before that cutoff instead of leaving early backtest dates
        # with no eligible stocks. After the cutoff, prefer the checked-in interval
        # ledger, which has a better constituent count than the older legacy calendar.
        if nifty200_membership_is_approximate:
            membership_calendar = index_history_data.get_membership_calendar("Nifty 500")

        try:
            legacy_membership = momentum_data.load_membership_calendar()
        except Exception:
            legacy_membership = pd.DataFrame(columns=["symbol", "start", "end"])

        if not membership_calendar.empty and legacy_membership is not None and not legacy_membership.empty:
            cutoff = pd.to_datetime(membership_calendar["start"], errors="coerce").min()
            if pd.notna(cutoff):
                legacy_prefix = legacy_membership[["symbol", "start", "end"]].copy()
                legacy_prefix["start"] = pd.to_datetime(legacy_prefix["start"], errors="coerce")
                legacy_prefix["end"] = pd.to_datetime(legacy_prefix["end"], errors="coerce")
                cutoff_previous_day = cutoff - pd.Timedelta(days=1)
                legacy_prefix = legacy_prefix[
                    legacy_prefix["start"].notna() & (legacy_prefix["start"] < cutoff)
                ].copy()
                legacy_prefix["end"] = legacy_prefix["end"].fillna(cutoff_previous_day)
                legacy_prefix.loc[
                    legacy_prefix["end"] > cutoff_previous_day, "end"
                ] = cutoff_previous_day
                legacy_prefix = legacy_prefix[
                    legacy_prefix["end"].notna()
                    & (legacy_prefix["end"] >= legacy_prefix["start"])
                ]
                membership_calendar = pd.concat(
                    [legacy_prefix, membership_calendar], ignore_index=True
                ).drop_duplicates()
        elif membership_calendar.empty and legacy_membership is not None and not legacy_membership.empty:
            membership_calendar = legacy_membership[["symbol", "start", "end"]].copy()

        if membership_calendar.empty:
            st.error(
                "Historical constituent membership could not be loaded. "
                "The point-in-time backtest has been stopped rather than silently "
                "using today's index constituents for past dates."
            )
            return None, None, None

        if nifty200_membership_is_approximate:
            st.warning(
                "Nifty 200 point-in-time membership is not available in the stored source. "
                "This run uses historical Nifty 500 membership as a broader proxy, so "
                "results are approximate and may differ from a true Nifty 200 backtest."
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

    # Keep pre-start prices for lookback indicators, but stop at the requested end.
    # fetch_market_data reads only FYERS-stamped local price files.
    prices_df = fetch_market_data(tickers, period="5y", full_history=True)
    if prices_df is None or prices_df.empty:
        return None, None, None
    prices_df.index = pd.to_datetime(prices_df.index, errors="coerce").tz_localize(None).normalize()
    prices_df = prices_df.loc[~prices_df.index.isna()].sort_index()
    start_ts = pd.Timestamp(start_date).normalize()
    end_ts = pd.Timestamp(end_date).normalize()
    prices_df = prices_df.loc[:end_ts]
    simulation_dates = prices_df.index[(prices_df.index >= start_ts) & (prices_df.index <= end_ts)]
    if simulation_dates.empty:
        return None, None, None

    # Require enough pre-start rows to calculate momentum, proximity, MA and RS
    # signals without borrowing future values or starting with a warmed-up curve.
    lookback_periods = [int(p) for p in strat_config.get("period_days", [252, 120, 60]) if int(p) > 0]
    required_lookback = max(lookback_periods or [252])
    ma_config = strat_config.get("moving_average", "200 EMA")
    if ma_config != "None":
        required_lookback = max(
            required_lookback,
            200 if "200" in ma_config else (100 if "100" in ma_config else 50),
        )
    if strat_config.get("pct_from_high", 15.0) > 0:
        required_lookback = max(required_lookback, 252)
    if strat_config.get("use_relative_momentum_filter", False):
        required_lookback = max(
            required_lookback,
            int(strat_config.get("relative_momentum_ma_period", 200)),
        )
    required_lookback += int(strat_config.get("skip_days", 0)) + 2
    previous_dates = prices_df.index[prices_df.index < simulation_dates[0]]
    if len(previous_dates) < required_lookback:
        return None, None, None

    # Mark the portfolio to market every trading day. Momentum signals are
    # computed using the previous session's close and changes take effect at
    # the next session's close. This avoids trading on a close that was needed
    # to calculate that same day's signal and lets MDD see intra-month declines.
    frequency = str(strat_config.get("rebalance_freq", "Monthly")).strip().lower()
    rebalance_day = max(1, min(28, int(strat_config.get("rebalance_day", 1))))
    scheduled_dates = {simulation_dates[0]}

    if frequency == "weekly":
        period_keys = simulation_dates.to_period("W-FRI")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            if len(period_dates):
                scheduled_dates.add(period_dates[0])
    elif frequency == "quarterly":
        period_keys = simulation_dates.to_period("Q")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            anchor_month = key.start_time.month
            candidates = period_dates[
                (period_dates.month == anchor_month) & (period_dates.day >= rebalance_day)
            ]
            if len(candidates):
                scheduled_dates.add(candidates[0])
    else:
        period_keys = simulation_dates.to_period("M")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            candidates = period_dates[period_dates.day >= rebalance_day]
            if len(candidates):
                scheduled_dates.add(candidates[0])

    portfolio_history = []
    holdings_history = []
    current_cash = float(initial_capital)
    current_holdings = {}
    multiplier = float(strat_config.get("allocation_multiplier", 1.0))
    all_dates = prices_df.index

    for dt in simulation_dates:
        close_row = prices_df.loc[dt]
        # Daily valuation before trades: prior positions earn the close-to-close
        # move up to this date. The same close is then used to execute today's
        # target, which was generated from the prior session's data only.
        daily_value = float(current_cash)
        for sym, qty in current_holdings.items():
            price = close_row.get(sym, np.nan)
            if pd.notna(price) and float(price) > 0:
                daily_value += int(qty) * float(price)

        if dt in scheduled_dates:
            prior = all_dates[all_dates < dt]
            if len(prior):
                signal_dt = prior[-1]
                historical_sub_df = prices_df.loc[:signal_dt]
                selected_stocks = run_strategy_stock_scanner(
                    strat_config,
                    price_subset=historical_sub_df,
                    current_holdings=list(current_holdings.keys()),
                    as_of_date=signal_dt,
                    membership_calendar=membership_calendar,
                )
                holdings_history.append((dt, list(selected_stocks)))

                if selected_stocks:
                    # Never buy a symbol without a real FYERS close on the
                    # execution date. If the target universe is temporarily
                    # unpriceable, keep the existing portfolio instead.
                    tradable = [
                        sym for sym in selected_stocks
                        if sym in prices_df.columns
                        and pd.notna(close_row.get(sym, np.nan))
                        and float(close_row.get(sym, 0.0)) > 0
                    ]
                    if tradable:
                        current_cash = daily_value
                        current_holdings = {}
                        effective_pool = daily_value * multiplier
                        allocation_per_stock = effective_pool / len(tradable)
                        for sym in tradable:
                            price = float(close_row[sym])
                            qty = int(allocation_per_stock // price)
                            if qty > 0:
                                current_holdings[sym] = qty
                                current_cash -= qty * price
                elif (
                    strat_config.get("use_market_trend_filter", False)
                    and strat_config.get("market_trend_exit_on_bearish", True)
                    and not _market_trend_is_bullish(strat_config, as_of_date=signal_dt)
                ):
                    current_holdings = {}
                    current_cash = daily_value

        # Re-value at today's close after any rebalance. Record every trading day,
        # not only rebalance dates, so maximum drawdown and daily risk metrics
        # are calculated from a daily equity curve.
        marked_value = float(current_cash)
        for sym, qty in current_holdings.items():
            price = close_row.get(sym, np.nan)
            if pd.notna(price) and float(price) > 0:
                marked_value += int(qty) * float(price)
        portfolio_history.append({"Date": dt, "Portfolio Value": marked_value})

    if not portfolio_history:
        return None, None, None

    df_res = pd.DataFrame(portfolio_history).set_index("Date")
    df_res["Daily Return"] = df_res["Portfolio Value"].pct_change().fillna(0.0)
    df_res["Peak Value"] = df_res["Portfolio Value"].cummax()
    df_res["Drawdown"] = df_res["Portfolio Value"] / df_res["Peak Value"] - 1.0
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

with st.expander("📚 FYERS Historical Data Status", expanded=False):
    history_summary = fyers_history_data.get_data_summary()
    if history_summary["symbols"]:
        hist_cols = st.columns(4)
        hist_cols[0].metric("Symbols with History", f'{history_summary["symbols"]:,}')
        hist_cols[1].metric("Daily Candles Stored", f'{history_summary["rows"]:,}')
        hist_cols[2].metric("Oldest Stored Date", history_summary["oldest_date"] or "—")
        hist_cols[3].metric("Backfills Pending", f'{history_summary["backfill_pending"]:,}')
        st.caption(
            f'FYERS source coverage through {history_summary["latest_date"] or "—"}. '
            "Backtests now read this stored dataset only; the updater resumes old-history downloads "
            "in later runs until the oldest available FYERS data is reached."
        )
    else:
        st.warning(
            "No FYERS history files are available in this deployment yet. Connect to FYERS from this app "
            "and configure GITHUB_ACTIONS_TOKEN in Streamlit Secrets once so the login-triggered workflow "
            "can download and commit the historical files."
        )
    st.link_button(
        "Open FYERS Historical Data Workflow",
        "https://github.com/nishantbhandari09/nse-momentum-portfolio/actions/workflows/update-index-history.yml",
        use_container_width=True,
    )

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
        history_summary = fyers_history_data.get_data_summary()
        if history_summary["symbols"]:
            st.markdown("**FYERS historical price database**")
            st.caption(
                f'{history_summary["symbols"]:,} symbols with data; '
                f'{history_summary["rows"]:,} daily candles; '
                f'coverage {history_summary["oldest_date"] or "—"} to '
                f'{history_summary["latest_date"] or "—"}.'
            )
            if fyers_history_data.MANIFEST_PATH.exists():
                st.download_button(
                    "📥 Download FYERS history coverage manifest",
                    fyers_history_data.MANIFEST_PATH.read_bytes(),
                    "fyers_history_manifest.csv",
                    "text/csv",
                )
        else:
            st.warning(
                "The FYERS historical OHLCV archive has not been downloaded to this deployment yet. "
                "The scanner and backtester will not substitute Yahoo Finance prices. Configure the "
                "connect to FYERS and configure GITHUB_ACTIONS_TOKEN in Streamlit Secrets once."
            )

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
        save_notice = st.session_state.pop("strategy_save_notice", None)
        if save_notice:
            st.success(save_notice)

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
        real_names = [
            name for name, strat in st.session_state.strategies.items()
            if str(strat.get("type", "Virtual")).strip().lower() == "real"
        ]
        virtual_names = [
            name for name, strat in st.session_state.strategies.items()
            if str(strat.get("type", "Virtual")).strip().lower() != "real"
        ]

        def render_strategy_cards(keys, empty_message):
            if not keys:
                st.info(empty_message)
                return

            strat_cols = st.columns(3)
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

        real_tab, virtual_tab = st.tabs(["💹 Real Strategies", "🧪 Virtual Strategies"])
        with real_tab:
            st.caption("Strategies categorized as Real. This section is separated from virtual/paper strategies.")
            render_strategy_cards(real_names, "No real strategies yet. Create a strategy and set its type to Real.")

        with virtual_tab:
            st.caption("Virtual strategies are for simulated portfolio tracking and do not place broker orders.")
            render_strategy_cards(virtual_names, "No virtual strategies yet. Create a strategy and set its type to Virtual.")

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
        # Validate/normalise inputs first, then update session state atomically.
        # A failed save stays on this page and reports the precise exception.
        target_name = (
            str(st.session_state.editing_strategy_name or "").strip()
            if edit_mode else str(strat_name_input or "").strip()
        )

        try:
            if not target_name:
                raise ValueError("Please enter a name for this strategy.")
            if not edit_mode and target_name in st.session_state.strategies:
                raise ValueError(
                    f"A strategy named '{target_name}' already exists. Choose a different name."
                )
            if float(tot_alloc) <= 0:
                raise ValueError("Allocated capital must be greater than ₹0.")

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
            if not selected_groups:
                selected_groups = ["Nifty 500"]

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
            if not selected_periods:
                selected_periods, weights = [252], [1.0]
            else:
                total_weight = sum(weights)
                weights = [weight / total_weight for weight in weights]

            # Build the complete record before assigning it to the strategies map.
            saved_config = {
                "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
                "status": edit_strat.get("status", "Active"),
                "type": st_type,
                "allocated": float(tot_alloc),
                "realized_pnl": float(edit_strat.get("realized_pnl", 0.0)),
                "rebalance_freq": rebal_freq,
                "rebalance_day": int(rebal_day),
                "groups": selected_groups,
                "allocation_multiplier": float(alloc_multiplier),
                "entry_rank": int(entry_rank),
                "exit_rank": int(exit_rank),
                "period_days": selected_periods,
                "period_weights": weights,
                "pct_from_high": float(pct_high),
                "pct_from_low": float(pct_low),
                "moving_average": selected_ma,
                "use_rs": bool(use_rs),
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
                "positions": edit_strat.get("positions", []),
            }
            st.session_state.strategies[target_name] = saved_config
            st.session_state.strategy_save_notice = f"Strategy '{target_name}' saved successfully."
        except Exception as exc:
            st.error(
                f"Strategy was not saved ({type(exc).__name__}): {exc}. "
                "Your existing strategies have not been intentionally overwritten."
            )
        else:
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
        "Use point-in-time index membership for backtesting",
        value=True,
        help="Historical eligibility is reconstructed from published index changes. This reduces survivorship bias but does not eliminate every data gap."
    )
    if use_pit and any(group in bt_strat_config.get("groups", []) for group in ("Nifty 500", "Nifty 200")):
        st.caption(
            "Data quality note: daily OHLC history for major Nifty indices is stored in this repository. "
            "Historical constituent membership is reconstructed, not a certified daily official ledger; "
            "coverage/cardinality gaps are more likely before 2018. Exact historical Nifty 200 membership "
            "is not available in the stored source, so a Nifty 200 backtest uses a broader Nifty 500 "
            "membership proxy and displays a warning."
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
                daily_returns = bt_results["Portfolio Value"].pct_change().dropna()
                peak = bt_results["Portfolio Value"].cummax()
                drawdown = bt_results["Portfolio Value"] / peak - 1.0
                max_drawdown_pct = float(drawdown.min() * 100)
                elapsed_days = max(
                    1,
                    (pd.Timestamp(bt_results.index[-1]) - pd.Timestamp(bt_results.index[0])).days,
                )
                elapsed_years = elapsed_days / 365.25
                cagr_pct = (
                    ((final_val / float(initial_cap)) ** (1.0 / elapsed_years) - 1.0) * 100
                    if initial_cap > 0 and final_val > 0 else float("nan")
                )
                daily_vol = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
                annual_vol_pct = daily_vol * np.sqrt(252) * 100
                sharpe_daily = (
                    float(daily_returns.mean() / daily_vol * np.sqrt(252))
                    if daily_vol > 0 else 0.0
                )

                res_m1, res_m2, res_m3 = st.columns(3)
                res_m1.metric("Final Portfolio Value", f"₹{final_val:,.2f}")
                res_m2.metric("Total Backtest Return", f"{total_return_pct:.2f}%")
                res_m3.metric("CAGR", f"{cagr_pct:.2f}%")

                res_m4, res_m5, res_m6 = st.columns(3)
                res_m4.metric("Maximum Drawdown (Daily)", f"{max_drawdown_pct:.2f}%")
                res_m5.metric("Annualized Volatility", f"{annual_vol_pct:.2f}%")
                res_m6.metric("Daily Sharpe Ratio (RF = 0)", f"{sharpe_daily:.2f}")

                st.caption(
                    "Equity is marked to market every trading day. Signals use data through the previous "
                    "session and are applied at the next session's close. Sharpe assumes a zero risk-free "
                    "rate; trading costs, taxes, slippage and dividend cash flows are not included."
                )
                st.markdown("#### Daily Portfolio Value Equity Curve")
                st.line_chart(bt_results["Portfolio Value"])

                st.markdown("#### Daily Backtest Data")
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
