import hashlib
import io
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None


# ============================================================
# APP CONFIG
# ============================================================
st.set_page_config(
    page_title="NSE Momentum Portfolio & Backtest",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_LOOKBACKS = [252, 120, 90, 60]
DEFAULT_EXIT_RANK = 41
NIFTY500_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"


# ============================================================
# SECRETS / AUTH
# ============================================================
def secret(name, default=None):
    try:
        value = st.secrets.get(name, default)
        return value
    except Exception:
        return default


def kite_ready():
    return KiteConnect is not None and bool(secret("KITE_API_KEY")) and bool(secret("KITE_API_SECRET"))


def get_kite():
    """Create Kite client and exchange today's request_token if present."""
    if KiteConnect is None:
        return None, None, "kiteconnect package is missing."

    api_key = secret("KITE_API_KEY")
    api_secret = secret("KITE_API_SECRET")
    if not api_key or not api_secret:
        return None, None, "KITE_API_KEY / KITE_API_SECRET are missing in Streamlit Secrets."

    kite = KiteConnect(api_key=api_key)

    request_token = None
    try:
        request_token = st.query_params.get("request_token")
    except Exception:
        pass

    if request_token and st.session_state.get("last_request_token") != request_token:
        try:
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            st.session_state["kite_access_token"] = session_data["access_token"]
            st.session_state["last_request_token"] = request_token
            try:
                del st.query_params["request_token"]
            except Exception:
                pass
        except Exception as exc:
            return kite, None, f"Zerodha login exchange failed: {exc}"

    access_token = st.session_state.get("kite_access_token")
    if not access_token:
        return kite, None, "Not connected to Zerodha for today's session."

    try:
        kite.set_access_token(access_token)
        kite.profile()
        return kite, access_token, "Connected to Zerodha for today."
    except Exception as exc:
        st.session_state.pop("kite_access_token", None)
        return kite, None, f"Zerodha access token is invalid/expired: {exc}"


# ============================================================
# GENERAL HELPERS
# ============================================================
def normalize_symbols(values):
    result, seen = [], set()
    for value in values:
        symbol = str(value).strip().upper().replace(".NS", "")
        if symbol and symbol not in seen:
            result.append(symbol)
            seen.add(symbol)
    return result


def parse_int_list(text):
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        n = int(item)
        if n > 0:
            values.append(n)
    return values


def parse_float_list(text):
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        values.append(float(item))
    return values


def safe_hash(payload):
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def fmt_pct(x):
    return "—" if pd.isna(x) else f"{x:.2f}%"


def current_or_previous_trading_day(prices, requested_date):
    available = prices.index[prices.index <= pd.Timestamp(requested_date)]
    return available[-1] if len(available) else None


# ============================================================
# NIFTY 500 UNIVERSE
# ============================================================
@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def load_nifty500_official():
    """Load the current Nifty 500 constituent list from NSE Indices/Nifty Indices."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/csv,text/plain,*/*",
        "Referer": "https://www.niftyindices.com/",
    }
    r = requests.get(NIFTY500_URL, headers=headers, timeout=30)
    r.raise_for_status()
    text = r.text
    df = pd.read_csv(io.StringIO(text))
    cols = {str(c).strip().lower(): c for c in df.columns}
    symbol_col = None
    for key in ("symbol", "ticker", "tradingsymbol"):
        if key in cols:
            symbol_col = cols[key]
            break
    if symbol_col is None:
        symbol_col = df.columns[0]
    symbols = normalize_symbols(df[symbol_col].dropna().tolist())
    if len(symbols) < 400:
        raise RuntimeError(f"Official Nifty 500 CSV returned only {len(symbols)} symbols.")
    return symbols, df


def get_universe(uploaded_file):
    if uploaded_file is not None:
        df = pd.read_csv(uploaded_file)
        cols = {str(c).strip().lower(): c for c in df.columns}
        source = cols.get("symbol") or cols.get("ticker") or cols.get("tradingsymbol") or df.columns[0]
        symbols = normalize_symbols(df[source].dropna().tolist())
        return symbols, "Uploaded CSV"

    try:
        symbols, _ = load_nifty500_official()
        return symbols, "Official Nifty 500 constituent list"
    except Exception as exc:
        st.warning(f"Could not refresh the official Nifty 500 list: {exc}. Using the small built-in fallback.")
        fallback = [
            "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC",
            "SBIN", "LT", "BAJFINANCE", "HINDUNILVR", "MARUTI", "SUNPHARMA", "TATASTEEL",
            "TATAMOTORS", "AXISBANK", "NTPC", "ONGC", "POWERGRID", "ADANIENT", "COALINDIA",
            "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND", "GRASIM", "TECHM", "JSWSTEEL",
            "HCLTECH", "HEROMOTOCO", "DRREDDY", "CIPLA", "APOLLOHOSP", "BAJAJ-AUTO",
            "EICHERMOT", "BPCL", "DIVISLAB", "TATACONSUM", "BRITANNIA", "BEL", "HAL",
            "TRENT", "VBL",
        ]
        return fallback, "Built-in fallback"


# ============================================================
# ZERODHA DATA
# ============================================================
@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def get_nse_instruments(api_key, access_token):
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    rows = kite.instruments("NSE")
    df = pd.DataFrame(rows)
    df["tradingsymbol"] = df["tradingsymbol"].astype(str).str.upper()
    eq = df[(df["segment"] == "NSE") & (df["instrument_type"] == "EQ")].copy()
    return eq[["instrument_token", "tradingsymbol", "name"]]


@st.cache_data(ttl=15 * 60, show_spinner=False)
def fetch_daily_history(symbols, start_date, end_date, api_key, access_token, delay=0.35):
    """Fetch daily close prices through Kite Connect with caching and rate-limit spacing."""
    if KiteConnect is None:
        raise RuntimeError("kiteconnect is not installed.")

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    instruments = get_nse_instruments(api_key, access_token)
    token_map = dict(zip(instruments["tradingsymbol"], instruments["instrument_token"]))

    frames = []
    errors = []
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    symbols = list(symbols)

    progress = st.progress(0, text="Downloading Zerodha daily candles…")
    for i, symbol in enumerate(symbols, start=1):
        token = token_map.get(symbol)
        if token is None:
            errors.append(f"{symbol}: no NSE equity instrument token")
        else:
            try:
                candles = kite.historical_data(token, start, end, "day", continuous=False, oi=False)
                if candles:
                    df = pd.DataFrame(candles)
                    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.tz_localize(None)
                    df = df.dropna(subset=["date"]).set_index("date")
                    close = pd.to_numeric(df["close"], errors="coerce").rename(symbol)
                    frames.append(close)
                else:
                    errors.append(f"{symbol}: no historical candles")
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")

        progress.progress(i / len(symbols), text=f"Zerodha data: {i}/{len(symbols)} stocks")
        if i < len(symbols):
            time.sleep(max(float(delay), 0.34))

    progress.empty()

    if not frames:
        raise RuntimeError("Zerodha returned no historical data. Check login, API plan and symbols.")

    prices = pd.concat(frames, axis=1).sort_index()
    prices = prices.loc[:, ~prices.columns.duplicated()]
    prices = prices.replace([np.inf, -np.inf], np.nan)

    cache_file = CACHE_DIR / f"zerodha_{safe_hash({'s': symbols, 'a': str(start), 'b': str(end)})}.parquet"
    prices.to_parquet(cache_file)
    status = f"Zerodha: {len(frames)}/{len(symbols)} symbols loaded"
    if errors:
        status += f"; {len(errors)} warning(s)"
    return prices, status, errors


# ============================================================
# INDICATORS / FILTERS
# ============================================================
def calculate_snapshot(prices, as_of_date, ema_periods, ema_direction, retracement_mode, retracement_threshold):
    actual_date = current_or_previous_trading_day(prices, as_of_date)
    if actual_date is None:
        return pd.DataFrame()

    idx = prices.index.get_loc(actual_date)
    current = prices.iloc[idx]
    window = prices.iloc[max(0, idx - 251): idx + 1]

    high_52 = window.max(skipna=True)
    low_52 = window.min(skipna=True)
    range_52 = high_52 - low_52

    out = pd.DataFrame({
        "Price": current,
        "52W High": high_52,
        "52W Low": low_52,
    })

    out["Distance from 52W High %"] = np.where(
        out["52W High"] != 0,
        (out["52W High"] - out["Price"]) / out["52W High"] * 100,
        np.nan,
    )
    out["Position from 52W Low %"] = np.where(
        range_52 != 0,
        (out["Price"] - out["52W Low"]) / range_52 * 100,
        np.nan,
    )

    # Single-number retracement rule — deliberately NOT a min/max range.
    if retracement_mode == "From 52W High (max correction %)":
        out["Retracement Metric %"] = out["Distance from 52W High %"]
        retr_pass = out["Retracement Metric %"] <= float(retracement_threshold)
    elif retracement_mode == "From 52W Low (minimum rise %)":
        out["Retracement Metric %"] = out["Position from 52W Low %"]
        retr_pass = out["Retracement Metric %"] >= float(retracement_threshold)
    else:
        out["Retracement Metric %"] = np.nan
        retr_pass = pd.Series(True, index=out.index)

    eligible = out["Price"].notna()

    for period in ema_periods:
        if idx + 1 < int(period):
            out[f"EMA {period}"] = np.nan
            eligible &= False
        else:
            ema = prices.iloc[: idx + 1].ewm(span=int(period), adjust=False, min_periods=int(period)).mean().iloc[-1]
            out[f"EMA {period}"] = ema
            if ema_direction == "Above EMA":
                eligible &= out["Price"] > ema
            else:
                eligible &= out["Price"] < ema

    eligible &= retr_pass
    out["Eligible"] = eligible.fillna(False)
    out["As Of"] = actual_date.date()
    return out.sort_index()


def rank_on_date(prices, as_of_date, lookbacks, weights):
    actual_date = current_or_previous_trading_day(prices, as_of_date)
    if actual_date is None:
        return pd.Series(dtype=float)

    idx = prices.index.get_loc(actual_date)
    components = []
    valid_weights = []

    for lookback, weight in zip(lookbacks, weights):
        if idx - int(lookback) < 0:
            continue
        start = prices.iloc[idx - int(lookback)]
        end = prices.iloc[idx]
        ret = (end / start - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if ret.empty:
            continue
        components.append(ret.rank(ascending=False, method="min"))
        valid_weights.append(float(weight))

    if not components:
        return pd.Series(dtype=float)

    w = np.asarray(valid_weights, dtype=float)
    if w.sum() == 0:
        w = np.ones(len(w)) / len(w)
    else:
        w = w / w.sum()

    matrix = pd.concat(components, axis=1)
    composite = matrix.mul(w, axis=1).sum(axis=1)
    return composite.rank(ascending=True, method="min").sort_values()


def build_live_table(prices, as_of_date, target_n, exit_rank, lookbacks, weights,
                     ema_periods, ema_direction, retracement_mode, retracement_threshold):
    snapshot = calculate_snapshot(
        prices, as_of_date, ema_periods, ema_direction,
        retracement_mode, retracement_threshold,
    )
    ranks = rank_on_date(prices, as_of_date, lookbacks, weights)

    if snapshot.empty or ranks.empty:
        return pd.DataFrame()

    df = snapshot.join(ranks.rename("Momentum Rank"), how="left")
    df = df.dropna(subset=["Momentum Rank"]).copy()
    df["Momentum Rank"] = df["Momentum Rank"].astype(int)

    def action(row):
        if not row["Eligible"]:
            return "NO ENTRY"
        if row["Momentum Rank"] <= target_n:
            return "ENTRY / TOP HOLDING"
        if row["Momentum Rank"] < exit_rank:
            return "HOLD / BUFFER"
        return "EXIT ZONE"

    df["Action"] = df.apply(action, axis=1)
    df = df.sort_values(["Eligible", "Momentum Rank"], ascending=[False, True])
    df.insert(0, "Symbol", df.index)
    return df.reset_index(drop=True)


# ============================================================
# BACKTEST ENGINE
# ============================================================
def rebalance_portfolio(current_holdings, ranks, eligible_symbols, target_n, exit_rank):
    if ranks.empty:
        return [], [], current_holdings

    current = list(current_holdings)
    kept = [s for s in current if s in ranks.index and int(ranks[s]) < int(exit_rank)]
    exits = [s for s in current if s not in kept]

    entries = []
    for symbol in ranks.index:
        if symbol in kept:
            continue
        if symbol not in eligible_symbols:
            continue
        entries.append(symbol)
        if len(kept) + len(entries) >= int(target_n):
            break

    return kept + entries, entries, exits


def month_end_trading_dates(prices, start_date, end_date):
    idx = prices.index[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))]
    if len(idx) == 0:
        return []
    grouped = pd.Series(idx, index=idx).groupby(idx.to_period("M")).max()
    return grouped.tolist()


def run_backtest(prices, start_date, end_date, target_n, exit_rank, lookbacks, weights,
                 ema_periods, ema_direction, retracement_mode, retracement_threshold,
                 rebalance_frequency="Monthly"):
    rebalance_dates = month_end_trading_dates(prices, start_date, end_date)
    max_history = max(max(lookbacks), max(ema_periods or [0]), 252)

    valid = []
    for d in rebalance_dates:
        pos = prices.index.get_loc(d)
        if pos >= max_history:
            valid.append(d)

    holdings = []
    rows = []
    equity = 1.0
    last_rebalance = None
    last_holdings = []

    for d in valid:
        ranks = rank_on_date(prices, d, lookbacks, weights)
        snap = calculate_snapshot(
            prices, d, ema_periods, ema_direction,
            retracement_mode, retracement_threshold,
        )
        eligible_symbols = set(snap.index[snap["Eligible"]]) if not snap.empty else set()

        new_holdings, entries, exits = rebalance_portfolio(
            holdings, ranks, eligible_symbols, target_n, exit_rank
        )

        period_return = np.nan
        if last_rebalance is not None and last_holdings:
            prev_prices = prices.loc[last_rebalance, last_holdings].dropna()
            curr_prices = prices.loc[d, last_holdings].dropna()
            aligned = pd.concat([prev_prices.rename("prev"), curr_prices.rename("curr")], axis=1).dropna()
            if not aligned.empty:
                # Equal-weight approximation for the holdings held during the period.
                period_return = float((aligned["curr"] / aligned["prev"] - 1).mean())
                equity *= 1.0 + period_return

        rows.append({
            "Date": d,
            "Portfolio Size": len(new_holdings),
            "Holdings": ", ".join(new_holdings),
            "New Entries": ", ".join(entries),
            "Exits": ", ".join(exits),
            "Eligible Candidates": len(eligible_symbols),
            "Turnover": len(entries) + len(exits),
            "Portfolio Return": period_return,
            "Equity": equity,
        })

        holdings = list(new_holdings)
        last_holdings = list(new_holdings)
        last_rebalance = d

    return pd.DataFrame(rows)


def backtest_metrics(history):
    if history.empty:
        return {}
    equity = history["Equity"].dropna()
    returns = history["Portfolio Return"].dropna()
    if equity.empty:
        return {}
    days = max((pd.Timestamp(history["Date"].iloc[-1]) - pd.Timestamp(history["Date"].iloc[0])).days, 1)
    years = days / 365.25
    cagr = equity.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    peak = equity.cummax()
    drawdown = equity / peak - 1
    return {
        "CAGR": cagr,
        "Max Drawdown": drawdown.min(),
        "Total Return": equity.iloc[-1] - 1,
        "Avg Period Return": returns.mean() if not returns.empty else np.nan,
        "Win Rate": (returns > 0).mean() if not returns.empty else np.nan,
        "Turnover": int(history["Turnover"].fillna(0).sum()),
    }


# ============================================================
# UI
# ============================================================
st.title("📈 NSE Momentum Portfolio & Backtesting Tool")
st.caption("Zerodha Kite Connect data • Nifty 500 universe • rule-based momentum, EMA, retracement and rank-buffer logic")

with st.sidebar:
    st.header("Portfolio Rules")
    target_n = st.number_input("Top N / Target holdings", 1, 500, 20, 1)
    exit_rank = st.number_input("Exit when momentum rank reaches", 2, 500, DEFAULT_EXIT_RANK, 1)
    lookback_text = st.text_input("Momentum lookbacks (trading days)", "252,120,90,60")
    weight_text = st.text_input("Weights (blank = equal)", "")

    st.divider()
    st.header("EMA Entry Filter")
    ema_enabled = st.checkbox("Enable EMA filter", value=True)
    if ema_enabled:
        ema_periods = st.multiselect(
            "EMA period(s) — ALL must pass",
            options=[10, 20, 50, 100, 150, 200],
            default=[50],
        )
        ema_direction = st.selectbox("Price condition", ["Above EMA", "Below EMA"])
    else:
        ema_periods = []
        ema_direction = "Above EMA"

    st.divider()
    st.header("52-Week Retracement")
    retracement_enabled = st.checkbox("Enable retracement filter", value=True)
    if retracement_enabled:
        retracement_mode = st.selectbox(
            "Single-number rule",
            [
                "From 52W High (max correction %)",
                "From 52W Low (minimum rise %)",
            ],
        )
        retracement_threshold = st.number_input(
            "Threshold %",
            min_value=0.0,
            max_value=100.0,
            value=40.0,
            step=1.0,
            help="High mode: stock must be no more than this % below its 52W high. Low mode: stock must be at least this % above its 52W low as a position in the 52W range.",
        )
    else:
        retracement_mode = "Disabled"
        retracement_threshold = 40.0

    st.divider()
    st.header("Universe")
    uploaded = st.file_uploader("Optional Nifty 500 CSV", type=["csv"])
    st.caption("Without a CSV, the app attempts to load the current official Nifty 500 constituent list.")

    st.divider()
    st.header("Zerodha")
    kite, access_token, kite_status = get_kite()
    if kite is not None and access_token:
        st.success(kite_status)
        if st.button("Clear today's Zerodha session"):
            st.session_state.pop("kite_access_token", None)
            st.rerun()
    elif kite is not None:
        st.warning(kite_status)
        try:
            st.link_button("🔐 Login / Refresh Zerodha", kite.login_url(), use_container_width=True)
        except Exception:
            st.error("Could not create Zerodha login URL.")
    else:
        st.error(kite_status)

    st.divider()
    st.header("Data")
    history_years = st.slider("Years of history to load", 1, 8, 5, 1)
    request_delay = st.number_input("Kite request delay (seconds)", 0.34, 2.0, 0.35, 0.01)
    force_refresh = st.checkbox("Force refresh downloaded data", value=False)


try:
    lookbacks = parse_int_list(lookback_text)
    if not lookbacks:
        raise ValueError("Enter at least one momentum lookback.")
    if weight_text.strip():
        weights = parse_float_list(weight_text)
        if len(weights) != len(lookbacks):
            raise ValueError("Weights count must match lookbacks count.")
    else:
        weights = [1 / len(lookbacks)] * len(lookbacks)
except Exception as exc:
    st.error(str(exc))
    st.stop()

if ema_enabled and not ema_periods:
    st.error("Select at least one EMA period or disable the EMA filter.")
    st.stop()

universe, universe_source = get_universe(uploaded)

# ============================================================
# TWO MAIN SECTIONS
# ============================================================
tab_portfolio, tab_backtest = st.tabs(["📊 Portfolio / Live Signals", "🧪 Backtesting"])

with tab_portfolio:
    st.subheader("Current Portfolio Inclusion & Entry / Exit Signals")
    col1, col2, col3 = st.columns(3)
    with col1:
        portfolio_date = st.date_input("Analysis date", value=date.today(), key="portfolio_date")
    with col2:
        portfolio_end = st.date_input("Latest history endpoint", value=date.today(), key="portfolio_end")
    with col3:
        st.metric("Universe", len(universe), universe_source)

    if st.button("▶ Fetch Zerodha Data & Build Portfolio", type="primary", use_container_width=True, key="portfolio_run"):
        if not access_token:
            st.error("Login to Zerodha first. Kite Connect access tokens are valid for the trading day.")
        else:
            start = date.today() - timedelta(days=365 * history_years)
            end = max(portfolio_end, portfolio_date)
            try:
                if force_refresh:
                    fetch_daily_history.clear()
                    get_nse_instruments.clear()
                with st.status("Building current portfolio signals…", expanded=True):
                    prices, source, warnings = fetch_daily_history(
                        tuple(universe), str(start), str(end),
                        secret("KITE_API_KEY"), access_token, float(request_delay),
                    )
                    live = build_live_table(
                        prices, portfolio_date, int(target_n), int(exit_rank),
                        lookbacks, weights,
                        ema_periods if ema_enabled else [], ema_direction,
                        retracement_mode if retracement_enabled else "Disabled",
                        retracement_threshold,
                    )
                    st.session_state["portfolio_prices"] = prices
                    st.session_state["portfolio_live"] = live
                    st.session_state["portfolio_source"] = source
                    st.session_state["portfolio_asof"] = portfolio_date
                    status_text = source
                    if warnings:
                        status_text += f" • {len(warnings)} warning(s)"
                    st.write(status_text)
                    if warnings:
                        with st.expander("Data warnings"):
                            st.write("\n".join(warnings[:100]))
                    st.success("Portfolio signals generated.")
            except Exception as exc:
                st.error(f"Portfolio data update failed: {exc}")

    if "portfolio_live" in st.session_state:
        live = st.session_state["portfolio_live"].copy()
        prices = st.session_state["portfolio_prices"]

        eligible = live[live["Eligible"]].sort_values("Momentum Rank").head(int(target_n)).copy()
        entry = live[live["Action"] == "ENTRY / TOP HOLDING"].copy()
        exits = live[live["Action"] == "EXIT ZONE"].copy()

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Data date", prices.index[-1].strftime("%d %b %Y"))
        m2.metric("Eligible", int(live["Eligible"].sum()))
        m3.metric("Top N", int(len(eligible)))
        m4.metric("Exit-zone stocks", int(len(exits)))

        if eligible.empty:
            st.error("🔴 NO ENTRY — no stock satisfies every enabled condition.")
        else:
            st.success(f"🟢 Portfolio inclusion: {len(eligible)} stock(s) satisfy all selected conditions and rank inside Top {target_n}.")

        st.markdown("### Portfolio inclusion / entry")
        entry_cols = [
            "Symbol", "Action", "Momentum Rank", "Price", "52W High", "52W Low",
            "Distance from 52W High %", "Position from 52W Low %", "Retracement Metric %",
        ] + [f"EMA {p}" for p in ema_periods if f"EMA {p}" in eligible.columns]
        entry_view = eligible[entry_cols].copy() if not eligible.empty else pd.DataFrame(columns=entry_cols)
        st.dataframe(entry_view, use_container_width=True, hide_index=True)

        st.markdown("### All scanner results")
        st.dataframe(live, use_container_width=True, hide_index=True)

        st.info(
            f"Retracement rule: {retracement_mode} = {retracement_threshold:g}%. "
            "It is a single threshold — not a minimum/maximum range."
        )

        csv = live.to_csv(index=False).encode("utf-8")
        st.download_button("Download current signals CSV", csv, "current_portfolio_signals.csv", "text/csv")

with tab_backtest:
    st.subheader("Historical Backtest")
    st.caption("Backtesting is independent from the current portfolio section and uses the dates selected below.")

    b1, b2, b3 = st.columns(3)
    with b1:
        bt_start = st.date_input("Backtest start date", value=date(2022, 1, 1), key="bt_start")
    with b2:
        bt_end = st.date_input("Backtest end date", value=date.today(), key="bt_end")
    with b3:
        st.selectbox("Rebalance frequency", ["Monthly"], index=0, key="bt_freq")

    st.warning(
        "This backtest uses the current Nifty 500 constituent universe unless you upload a dated/custom universe. "
        "That means historical results can contain survivorship bias."
    )

    if st.button("🧪 Run Backtest", type="primary", use_container_width=True, key="backtest_run"):
        if not access_token:
            st.error("Login to Zerodha first.")
        elif bt_start >= bt_end:
            st.error("Backtest start date must be before the end date.")
        else:
            required_start = pd.Timestamp(bt_start) - pd.Timedelta(days=max(max(lookbacks), max(ema_periods or [0]), 252) * 2)
            try:
                if force_refresh:
                    fetch_daily_history.clear()
                    get_nse_instruments.clear()
                with st.status("Running historical backtest…", expanded=True):
                    prices, source, warnings = fetch_daily_history(
                        tuple(universe), required_start.strftime("%Y-%m-%d"), str(bt_end),
                        secret("KITE_API_KEY"), access_token, float(request_delay),
                    )
                    history = run_backtest(
                        prices, bt_start, bt_end,
                        int(target_n), int(exit_rank), lookbacks, weights,
                        ema_periods if ema_enabled else [], ema_direction,
                        retracement_mode if retracement_enabled else "Disabled",
                        retracement_threshold,
                    )
                    st.session_state["backtest_history"] = history
                    st.session_state["backtest_prices"] = prices
                    st.session_state["backtest_source"] = source
                    st.session_state["backtest_warnings"] = warnings
                    st.success("Backtest completed.")
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")

    if "backtest_history" in st.session_state:
        history = st.session_state["backtest_history"].copy()
        metrics = backtest_metrics(history)

        a, b, c, d, e = st.columns(5)
        a.metric("CAGR", fmt_pct(metrics.get("CAGR", np.nan) * 100 if metrics else np.nan))
        b.metric("Total return", fmt_pct(metrics.get("Total Return", np.nan) * 100 if metrics else np.nan))
        c.metric("Max drawdown", fmt_pct(metrics.get("Max Drawdown", np.nan) * 100 if metrics else np.nan))
        d.metric("Win rate", fmt_pct(metrics.get("Win Rate", np.nan) * 100 if metrics else np.nan))
        e.metric("Turnover", int(metrics.get("Turnover", 0)) if metrics else 0)

        if not history.empty:
            chart = history.set_index("Date")["Equity"]
            st.markdown("### Equity curve")
            st.line_chart(chart)

            st.markdown("### Rebalance history")
            st.dataframe(history, use_container_width=True, hide_index=True)

            st.markdown("### Last backtest portfolio")
            latest = history.iloc[-1]
            x, y, z = st.columns(3)
            with x:
                st.write("**Holdings**")
                st.write(latest["Holdings"] or "No holdings")
            with y:
                st.write("**Entries**")
                st.write(latest["New Entries"] or "None")
            with z:
                st.write("**Exits**")
                st.write(latest["Exits"] or "None")

            csv = history.to_csv(index=False).encode("utf-8")
            st.download_button("Download backtest CSV", csv, "momentum_backtest.csv", "text/csv")

st.divider()
st.caption(
    "Research tool only. Zerodha historical candles use Kite Connect instrument tokens; the current instrument list is refreshed daily. "
    "Do not commit API secrets or access tokens to GitHub."
)
