import hashlib
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

st.set_page_config(
    page_title="NSE Momentum Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = APP_DIR / "data_cache"
CACHE_DIR.mkdir(exist_ok=True)

DEFAULT_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC",
    "SBIN", "LT", "BAJFINANCE", "HINDUNILVR", "MARUTI", "SUNPHARMA",
    "TATASTEEL", "TATAMOTORS", "AXISBANK", "NTPC", "ONGC", "POWERGRID",
    "ADANIENT", "COALINDIA", "TITAN", "ULTRACEMCO", "WIPRO", "NESTLEIND",
    "GRASIM", "TECHM", "JSWSTEEL", "HCLTECH", "HEROMOTOCO", "DRREDDY",
    "CIPLA", "APOLLOHOSP", "BAJAJ-AUTO", "EICHERMOT", "BPCL", "DIVISLAB",
    "TATACONSUM", "BRITANNIA", "BEL", "HAL", "TRENT", "VBL"
]


def normalize_symbols(values):
    result = []
    seen = set()
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
        try:
            number = int(item)
            if number > 0:
                values.append(number)
        except ValueError:
            raise ValueError(f"Invalid value: {item}")
    return values


def parse_float_list(text):
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            values.append(float(item))
        except ValueError:
            raise ValueError(f"Invalid weight value: {item}")
    return values


def cache_key(tickers, start, end):
    raw = json.dumps({"tickers": sorted(tickers), "start": start, "end": end}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]



def get_secret(name, default=None):
    """Read a Streamlit secret safely without exposing it in the UI."""
    try:
        value = st.secrets[name] if name in st.secrets else default
    except Exception:
        value = default
    return value


def get_kite_session():
    """Create/restore today's Kite session using the official login redirect flow."""
    if KiteConnect is None:
        return None, None, "kiteconnect package is not installed. Add kiteconnect to requirements.txt."

    api_key = get_secret("KITE_API_KEY")
    api_secret = get_secret("KITE_API_SECRET")
    if not api_key or not api_secret:
        return None, None, "KITE_API_KEY and KITE_API_SECRET are missing from Streamlit secrets."

    kite = KiteConnect(api_key=api_key)

    # A fresh request_token is returned by Zerodha after the user completes login.
    try:
        request_token = st.query_params.get("request_token")
    except Exception:
        request_token = None

    if request_token and st.session_state.get("kite_request_token") != request_token:
        try:
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            st.session_state["kite_access_token"] = session_data["access_token"]
            st.session_state["kite_request_token"] = request_token
            # Remove the one-time request token from the browser URL after exchange.
            try:
                del st.query_params["request_token"]
            except Exception:
                pass
        except Exception as exc:
            return None, None, f"Zerodha login exchange failed: {exc}"

    access_token = st.session_state.get("kite_access_token") or get_secret("KITE_ACCESS_TOKEN")
    if not access_token:
        return kite, None, "Not logged in to Zerodha for today."

    kite.set_access_token(access_token)
    try:
        kite.profile()
    except Exception as exc:
        st.session_state.pop("kite_access_token", None)
        return kite, None, f"Zerodha access token is invalid/expired: {exc}"
    return kite, access_token, "Connected to Zerodha Kite Connect."


def kite_login_url(kite):
    try:
        return kite.login_url()
    except Exception:
        return None


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_prices_kite_cached(tickers, start, end, access_token, api_key, delay=0.35):
    """Fetch daily close candles from Kite Connect.

    Kite's historical API allows up to 2000 days per day-candle request and is
    rate limited; the small delay keeps requests below the documented 3 req/sec limit.
    """
    if KiteConnect is None:
        raise RuntimeError("kiteconnect is not installed. Add it to requirements.txt and redeploy.")
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    instrument_rows = kite.instruments("NSE")
    token_map = {
        str(row["tradingsymbol"]).upper(): int(row["instrument_token"])
        for row in instrument_rows
        if row.get("tradingsymbol")
    }

    frames = []
    errors = []
    start_ts = pd.Timestamp(start).date()
    end_ts = pd.Timestamp(end).date()

    for idx, symbol in enumerate(tickers, start=1):
        token = token_map.get(str(symbol).upper())
        if token is None:
            errors.append(f"{symbol}: NSE instrument token not found")
            continue
        try:
            candles = kite.historical_data(token, start_ts, end_ts, "day", continuous=False, oi=False)
            if candles:
                frame = pd.DataFrame(candles)
                frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None)
                frame = frame.set_index("date")
                series = pd.to_numeric(frame["close"], errors="coerce").rename(str(symbol).upper())
                frames.append(series)
            else:
                errors.append(f"{symbol}: no historical data")
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
        if idx < len(tickers):
            time.sleep(max(float(delay), 0.34))

    if not frames:
        raise RuntimeError("Kite Connect returned no market data. Check today's login/session and your symbols.")

    prices = pd.concat(frames, axis=1).sort_index()
    prices = prices.loc[:, ~prices.columns.duplicated()]
    prices = prices.replace([np.inf, -np.inf], np.nan)
    path_key = cache_key(tickers, start, end) + "_kite"
    path = CACHE_DIR / f"prices_{path_key}.parquet"
    prices.to_parquet(path)
    status = f"Kite Connect: downloaded {len(frames)} stocks"
    if errors:
        status += f"; {len(errors)} warning(s)"
    return prices, status


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_prices_cached(tickers, start, end, batch_size, delay):
    """Fetch daily adjusted close prices in batches."""
    key = cache_key(tickers, start, end)
    path = CACHE_DIR / f"prices_{key}.parquet"
    if path.exists():
        return pd.read_parquet(path), "local parquet cache"

    frames = []
    errors = []
    total_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_no, offset in enumerate(range(0, len(tickers), batch_size), start=1):
        batch = tickers[offset: offset + batch_size]
        symbols = [f"{symbol}.NS" for symbol in batch]
        try:
            downloaded = yf.download(
                symbols,
                start=start,
                end=(pd.to_datetime(end) + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="column",
                timeout=30,
            )
            if downloaded is None or downloaded.empty:
                errors.append(f"Batch {batch_no}: no data")
            else:
                if isinstance(downloaded.columns, pd.MultiIndex):
                    top_level = downloaded.columns.get_level_values(0)
                    if "Close" in top_level:
                        frame = downloaded["Close"].copy()
                    else:
                        frame = downloaded[top_level[0]].copy()
                else:
                    frame = downloaded[["Close"]].copy() if "Close" in downloaded.columns else downloaded.copy()
                if isinstance(frame, pd.Series):
                    frame = frame.to_frame()
                frame.columns = [str(c).replace(".NS", "").upper() for c in frame.columns]
                frames.append(frame)
        except Exception as exc:
            errors.append(f"Batch {batch_no}: {exc}")
        if batch_no < total_batches:
            time.sleep(max(float(delay), 0.0))

    if not frames:
        raise RuntimeError("No market data could be downloaded. Check the symbols or try again later.")

    prices = pd.concat(frames, axis=1)
    prices = prices.loc[:, ~prices.columns.duplicated()].sort_index()
    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    prices = prices.replace([np.inf, -np.inf], np.nan)
    prices.to_parquet(path)
    status = f"downloaded {len(frames)} batches"
    if errors:
        status += f"; {len(errors)} batch warning(s)"
    return prices, status


def rank_on_date(prices, as_of_date, lookbacks, weights):
    available = prices.index[prices.index <= pd.Timestamp(as_of_date)]
    if len(available) == 0:
        return pd.Series(dtype=float)

    actual_date = available[-1]
    idx = prices.index.get_loc(actual_date)
    rank_frames = []
    valid_weights = []

    for lookback, weight in zip(lookbacks, weights):
        if idx - lookback < 0:
            continue
        start = prices.iloc[idx - lookback]
        end = prices.iloc[idx]
        returns = (end / start) - 1
        returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
        if returns.empty:
            continue
        rank_frames.append(returns.rank(ascending=False, method="min"))
        valid_weights.append(weight)

    if not rank_frames:
        return pd.Series(dtype=float)

    weights_arr = np.asarray(valid_weights, dtype=float)
    weights_arr = (
        np.ones(len(weights_arr), dtype=float) / len(weights_arr)
        if weights_arr.sum() == 0
        else weights_arr / weights_arr.sum()
    )
    rank_matrix = pd.concat(rank_frames, axis=1)
    composite_score = rank_matrix.mul(weights_arr, axis=1).sum(axis=1)
    return composite_score.rank(ascending=True, method="min").sort_values()


def calculate_filters(prices, as_of_date, ema_periods=None, ema_direction="Above EMA",
                      retracement_enabled=False, retracement_reference="From 52W High",
                      retracement_min=0.0, retracement_max=100.0):
    """Return technical metrics and a boolean eligibility mask for the requested date.

    Retracement from 52W High = (52W High - price) / (52W High - 52W Low) * 100.
    Position from 52W Low = (price - 52W Low) / (52W High - 52W Low) * 100.
    """
    available = prices.index[prices.index <= pd.Timestamp(as_of_date)]
    if len(available) == 0:
        return pd.DataFrame(), pd.Series(dtype=bool)

    actual_date = available[-1]
    idx = prices.index.get_loc(actual_date)
    window_start = max(0, idx - 251)
    window = prices.iloc[window_start: idx + 1]
    current = prices.iloc[idx]

    high_52w = window.max(skipna=True)
    low_52w = window.min(skipna=True)
    range_52w = high_52w - low_52w

    metrics = pd.DataFrame({
        "Price": current,
        "52W High": high_52w,
        "52W Low": low_52w,
    })

    metrics["Distance from 52W High %"] = np.where(
        metrics["52W High"] != 0,
        (metrics["52W High"] - metrics["Price"]) / metrics["52W High"] * 100,
        np.nan,
    )
    metrics["52W Range Position %"] = np.where(
        range_52w != 0,
        (metrics["Price"] - metrics["52W Low"]) / range_52w * 100,
        np.nan,
    )
    # For this scanner, retracement from the high is the fraction of the 52W high-low move retraced.
    metrics["52W Retracement %"] = np.where(
        range_52w != 0,
        (metrics["52W High"] - metrics["Price"]) / range_52w * 100,
        np.nan,
    )

    eligible = metrics["Price"].notna()

    # EMA filters: ALL selected EMAs must pass.
    if ema_periods:
        for period in ema_periods:
            if idx + 1 < period:
                eligible &= False
                metrics[f"EMA {period}"] = np.nan
                continue
            ema = prices.iloc[: idx + 1].ewm(span=period, adjust=False, min_periods=period).mean().iloc[-1]
            metrics[f"EMA {period}"] = ema
            if ema_direction == "Above EMA":
                eligible &= metrics["Price"] > ema
            else:
                eligible &= metrics["Price"] < ema

    if retracement_enabled:
        if retracement_reference == "From 52W High":
            value = metrics["52W Retracement %"]
        else:
            value = metrics["52W Range Position %"]
        eligible &= value.ge(float(retracement_min)) & value.le(float(retracement_max))

    metrics["Eligible"] = eligible.fillna(False)
    return metrics.sort_index(), metrics["Eligible"].fillna(False)


def rebalance(current_portfolio, ranks, target_n, exit_rank, eligible_entries=None):
    if ranks.empty:
        return current_portfolio, [], []

    eligible_entries = set(eligible_entries if eligible_entries is not None else ranks.index)
    keep = [
        symbol for symbol in current_portfolio
        if symbol in ranks.index and ranks[symbol] < exit_rank
    ]
    exits = [symbol for symbol in current_portfolio if symbol not in keep]
    needed = max(0, target_n - len(keep))
    entries = []

    for symbol in ranks.index:
        if symbol not in keep and symbol in eligible_entries:
            entries.append(symbol)
            if len(entries) >= needed:
                break

    return keep + entries, entries, exits


def monthly_rebalance_dates(prices):
    month_ends = prices.resample("ME").last().index
    dates = []
    for month_end in month_ends:
        available = prices.index[prices.index <= month_end]
        if len(available):
            dates.append(available[-1])
    return sorted(set(dates))


def run_backtest(prices, lookbacks, weights, target_n, exit_rank, start_date,
                 ema_periods=None, ema_direction="Above EMA", retracement_enabled=False,
                 retracement_reference="From 52W High", retracement_min=0.0,
                 retracement_max=100.0):
    rebalance_dates = monthly_rebalance_dates(prices)
    max_lookback = max(max(lookbacks), max(ema_periods or [0]), 252 if retracement_enabled else 0)
    valid_dates = []
    for d in rebalance_dates:
        idx = prices.index.get_loc(d)
        if idx >= max_lookback and d >= pd.Timestamp(start_date):
            valid_dates.append(d)

    portfolio = []
    rows = []
    equity = 1.0
    previous_date = None
    previous_holdings = []

    for current_date in valid_dates:
        ranks = rank_on_date(prices, current_date, lookbacks, weights)
        filter_df, eligible = calculate_filters(
            prices, current_date, ema_periods, ema_direction,
            retracement_enabled, retracement_reference,
            retracement_min, retracement_max,
        )
        eligible_symbols = set(filter_df.index[eligible]) if not filter_df.empty else set()
        new_portfolio, entries, exits = rebalance(
            portfolio, ranks, target_n, exit_rank, eligible_symbols
        )

        monthly_return = np.nan
        if previous_date is not None and previous_holdings:
            prev_prices = prices.loc[previous_date, previous_holdings]
            curr_prices = prices.loc[current_date, previous_holdings]
            aligned = pd.concat([prev_prices.rename("prev"), curr_prices.rename("curr")], axis=1).dropna()
            if not aligned.empty:
                monthly_return = float((aligned["curr"] / aligned["prev"] - 1).mean())
                equity *= 1 + monthly_return

        rows.append({
            "Date": current_date,
            "Portfolio Size": len(new_portfolio),
            "Holdings": ", ".join(new_portfolio),
            "New Entries": ", ".join(entries),
            "Exits": ", ".join(exits),
            "Eligible Candidates": len(eligible_symbols),
            "Turnover": len(entries) + len(exits),
            "Portfolio Return": monthly_return,
            "Equity": equity,
        })
        portfolio = new_portfolio
        previous_date = current_date
        previous_holdings = list(new_portfolio)

    return pd.DataFrame(rows)


def portfolio_metrics(history):
    if history.empty:
        return {"CAGR": np.nan, "Max Drawdown": np.nan, "Avg Monthly Return": np.nan, "Turnover": 0}
    returns = history["Portfolio Return"].dropna()
    equity = history["Equity"].dropna()
    if equity.empty:
        cagr = np.nan
        max_dd = np.nan
    else:
        periods = max((history["Date"].iloc[-1] - history["Date"].iloc[0]).days / 365.25, 1 / 365.25)
        cagr = equity.iloc[-1] ** (1 / periods) - 1 if periods > 0 else np.nan
        peak = equity.cummax()
        max_dd = (equity / peak - 1).min()
    return {
        "CAGR": cagr,
        "Max Drawdown": max_dd,
        "Avg Monthly Return": returns.mean() if not returns.empty else np.nan,
        "Turnover": int(history["Turnover"].fillna(0).sum()),
    }


def load_uploaded_universe(uploaded_file):
    data = pd.read_csv(uploaded_file)
    columns = {str(column).lower().strip(): column for column in data.columns}
    source_column = columns.get("symbol") or columns.get("ticker") or data.columns[0]
    return normalize_symbols(data[source_column].dropna().tolist())


st.title("📈 NSE Momentum Portfolio Dashboard")
st.caption("Momentum ranking + EMA trend filter + 52-week retracement + strict NO-ENTRY logic + optional Zerodha Kite Connect data")

with st.sidebar:
    st.header("Strategy")
    target_n = st.number_input("Target holdings / Entry Top N", min_value=1, max_value=500, value=20, step=1)
    exit_rank = st.number_input("Exit when rank reaches", min_value=2, max_value=1000, value=41, step=1)
    lookback_text = st.text_input("Lookback periods (trading days)", "252,120,90,60")
    weight_text = st.text_input("Weights (blank = equal)", "")

    st.divider()
    st.header("EMA Entry Filter")
    ema_enabled = st.checkbox("Enable EMA filter", value=False)
    ema_periods = []
    ema_direction = "Above EMA"
    if ema_enabled:
        ema_periods = st.multiselect(
            "Select EMA period(s)",
            options=[10, 20, 50, 100, 150, 200],
            default=[200],
            help="ALL selected EMA conditions must be satisfied for a stock to be eligible for a new entry.",
        )
        custom_ema = st.number_input("Custom EMA period (0 = none)", min_value=0, max_value=1000, value=0, step=1)
        if custom_ema > 0:
            ema_periods = sorted(set(ema_periods + [int(custom_ema)]))
        ema_direction = st.selectbox("Price condition", ["Above EMA", "Below EMA"])

    st.divider()
    st.header("52-Week Retracement Entry Filter")
    retracement_enabled = st.checkbox("Enable retracement filter", value=False)
    retracement_reference = "From 52W High"
    retracement_min = 0.0
    retracement_max = 100.0
    if retracement_enabled:
        retracement_reference = st.selectbox(
            "Measure retracement / position from",
            ["From 52W High", "From 52W Low"],
            help="From 52W High measures correction from the high across the full 52-week high-low range. From 52W Low measures the current position up from the low.",
        )
        r1, r2 = st.columns(2)
        with r1:
            retracement_min = st.number_input("Minimum %", min_value=0.0, max_value=100.0, value=40.0, step=1.0)
        with r2:
            retracement_max = st.number_input("Maximum %", min_value=0.0, max_value=100.0, value=50.0, step=1.0)
        if retracement_min > retracement_max:
            st.error("Minimum retracement cannot be greater than maximum.")

    st.divider()
    st.header("Data")
    data_source = st.radio(
        "Market data source",
        ["Yahoo Finance", "Zerodha Kite Connect"],
        index=0,
        help="Use Kite Connect for your Zerodha daily market data. Yahoo remains available as a fallback.",
    )

    kite, kite_access_token, kite_status = get_kite_session()
    if data_source == "Zerodha Kite Connect":
        if KiteConnect is None:
            st.error("kiteconnect is not installed. Add it to requirements.txt.")
        elif kite is not None and kite_access_token:
            st.success("Zerodha: connected for today")
        elif kite is not None:
            login_url = kite_login_url(kite)
            if login_url:
                st.link_button("🔐 Login / Refresh Zerodha", login_url, use_container_width=True)
            st.info("Zerodha requires a fresh manual login each day. After login, return to this app and run the scanner.")
        else:
            st.warning(kite_status)

    end_date = st.date_input("Data end date", value=date.today())
    start_date = st.date_input("Data start date", value=end_date - timedelta(days=365 * 5))
    batch_size = st.number_input("Stocks per data request", min_value=1, max_value=50, value=15, step=1)
    delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=10.0, value=1.5, step=0.5)
    kite_delay = st.number_input("Kite request delay (seconds)", min_value=0.34, max_value=2.0, value=0.35, step=0.01, help="Kite historical API is limited to 3 requests/second.")
    refresh = st.checkbox("Force refresh / ignore 24-hour cache", value=False)

    st.divider()
    st.header("Universe")
    uploaded = st.file_uploader(
        "Upload a CSV containing Symbol or Ticker",
        type=["csv"],
        help="Use your maintained Nifty 500 universe CSV here to scan the full Nifty 500 universe.",
    )

try:
    lookbacks = parse_int_list(lookback_text)
    if not lookbacks:
        raise ValueError("Enter at least one lookback period.")
    weights = parse_float_list(weight_text) if weight_text.strip() else [1 / len(lookbacks)] * len(lookbacks)
    if len(weights) != len(lookbacks):
        raise ValueError("Number of weights must match number of lookback periods.")
except ValueError as exc:
    st.error(str(exc))
    st.stop()

if exit_rank <= target_n:
    st.warning("Your exit rank should normally be greater than the target holdings to create a buffer.")
if retracement_enabled and retracement_min > retracement_max:
    st.stop()
if ema_enabled and not ema_periods:
    st.error("Select at least one EMA period or disable the EMA filter.")
    st.stop()

universe = load_uploaded_universe(uploaded) if uploaded else DEFAULT_UNIVERSE
universe = normalize_symbols(universe)

st.info(
    f"Universe: **{len(universe)} stocks** · Strategy: **Top {target_n}** · Exit buffer: **Rank {exit_rank}+** · "
    f"Lookbacks: **{', '.join(map(str, lookbacks))} trading days**"
)

if st.button("▶ Run / Update Data", type="primary", use_container_width=True):
    try:
        if pd.Timestamp(start_date) >= pd.Timestamp(end_date):
            st.error("Data start date must be before the data end date.")
            st.stop()
        if refresh:
            fetch_prices_cached.clear()
        with st.status("Updating data and running strategy…", expanded=True) as status:
            st.write("Downloading/caching daily closing prices…")
            if data_source == "Zerodha Kite Connect":
                if not kite_access_token:
                    st.error("Please login to Zerodha first. The Kite access token is valid for one day.")
                    st.stop()
                if refresh:
                    fetch_prices_kite_cached.clear()
                prices, source = fetch_prices_kite_cached(
                    tuple(universe), str(start_date), str(end_date), kite_access_token,
                    get_secret("KITE_API_KEY"), float(kite_delay)
                )
            else:
                prices, source = fetch_prices_cached(
                    tuple(universe), str(start_date), str(end_date), int(batch_size), float(delay)
                )
            st.write("Applying EMA/retracement filters and calculating rankings…")
            history = run_backtest(
                prices, lookbacks, weights, int(target_n), int(exit_rank), str(start_date),
                ema_periods if ema_enabled else [],
                ema_direction,
                retracement_enabled,
                retracement_reference,
                float(retracement_min),
                float(retracement_max),
            )
            st.session_state.update(
                prices=prices,
                history=history,
                source=source,
                universe=universe,
                last_config={
                    "target_n": int(target_n),
                    "exit_rank": int(exit_rank),
                    "lookbacks": lookbacks,
                    "weights": weights,
                    "ema_enabled": ema_enabled,
                    "ema_periods": ema_periods,
                    "ema_direction": ema_direction,
                    "retracement_enabled": retracement_enabled,
                    "retracement_reference": retracement_reference,
                    "retracement_min": float(retracement_min),
                    "retracement_max": float(retracement_max),
                    "data_source": data_source,
                },
            )
            status.update(label="Done", state="complete", expanded=False)
    except Exception as exc:
        st.error(f"The data update failed: {exc}")
        st.stop()

if "history" not in st.session_state:
    st.warning("Choose your strategy settings and click **Run / Update Data** to build the dashboard.")
    st.stop()

prices = st.session_state["prices"]
history = st.session_state["history"]
source = st.session_state.get("source", "")
config = st.session_state.get("last_config", {})

metrics = portfolio_metrics(history)
latest_date = prices.index[-1].date()
latest_filter_df, latest_eligible = calculate_filters(
    prices,
    latest_date,
    config.get("ema_periods", []) if config.get("ema_enabled", False) else [],
    config.get("ema_direction", "Above EMA"),
    config.get("retracement_enabled", False),
    config.get("retracement_reference", "From 52W High"),
    config.get("retracement_min", 0.0),
    config.get("retracement_max", 100.0),
)
latest_ranks = rank_on_date(prices, latest_date, lookbacks, weights)

# Apply technical filters to the ranking so the final selection universe is genuinely small.
eligible_symbols = set(latest_filter_df.index[latest_eligible]) if not latest_filter_df.empty else set(latest_ranks.index)
filtered_ranks = latest_ranks[latest_ranks.index.isin(eligible_symbols)].sort_values()

latest_rank_df = pd.DataFrame({
    "Symbol": filtered_ranks.index,
    "Composite Rank": filtered_ranks.values,
})
latest_rank_df["Zone"] = np.select(
    [
        latest_rank_df["Composite Rank"] <= target_n,
        latest_rank_df["Composite Rank"] < exit_rank,
    ],
    ["ENTRY / TOP HOLDINGS", "BUFFER / HOLD"],
    default="EXIT ZONE",
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Latest data", latest_date.strftime("%d %b %Y"))
m2.metric("Stocks in universe", len(prices.columns))
m3.metric("Eligible after filters", len(filtered_ranks))
m4.metric("CAGR", "—" if pd.isna(metrics["CAGR"]) else f"{metrics['CAGR']:.1%}")
m5.metric("Max drawdown", "—" if pd.isna(metrics["Max Drawdown"]) else f"{metrics['Max Drawdown']:.1%}")

# Strict no-entry state.
if len(filtered_ranks) == 0:
    st.error("🔴 NO ENTRY — No stock satisfies ALL selected entry conditions.")
    st.info("The scanner will not force a selection or relax your filters. Wait until at least one stock meets every enabled condition.")
else:
    top_entries = filtered_ranks.head(int(target_n))
    if len(top_entries) > 0:
        st.success(f"🟢 ENTRY UNIVERSE — {len(top_entries)} stock(s) currently satisfy ALL selected entry conditions.")

latest_rebalance = history.iloc[-1]
st.subheader(f"Latest Monthly Rebalance — {pd.Timestamp(latest_rebalance['Date']).date()}")
left, mid, right = st.columns(3)
with left:
    st.markdown("**Current holdings**")
    holdings = latest_rebalance["Holdings"].split(", ") if latest_rebalance["Holdings"] else []
    st.dataframe(pd.DataFrame({"Holding": holdings}), use_container_width=True, hide_index=True)
with mid:
    st.markdown("**New entries**")
    entries = latest_rebalance["New Entries"].split(", ") if latest_rebalance["New Entries"] else []
    st.dataframe(pd.DataFrame({"New entry": entries}), use_container_width=True, hide_index=True)
with right:
    st.markdown("**Exits**")
    exits = latest_rebalance["Exits"].split(", ") if latest_rebalance["Exits"] else []
    st.dataframe(pd.DataFrame({"Exit": exits}), use_container_width=True, hide_index=True)

st.subheader("Filtered current selection")
if not filtered_ranks.empty:
    display_df = latest_filter_df.loc[latest_filter_df.index.intersection(filtered_ranks.index)].copy()
    display_df["Composite Rank"] = filtered_ranks.reindex(display_df.index)
    display_df = display_df.sort_values("Composite Rank")
    st.dataframe(display_df.head(max(int(exit_rank), int(target_n)) + 10), use_container_width=True)
else:
    st.dataframe(pd.DataFrame(columns=["Price", "52W High", "52W Low", "52W Retracement %", "Eligible"]), use_container_width=True)

st.subheader("Current ranking")
ranking_view = latest_rank_df.head(max(int(exit_rank), int(target_n)) + 10).copy()
st.dataframe(ranking_view, use_container_width=True, hide_index=True)

st.subheader("Backtest / Rebalance history")
history_view = history.copy()
history_view["Date"] = pd.to_datetime(history_view["Date"]).dt.date
history_view["Portfolio Return"] = history_view["Portfolio Return"].apply(lambda x: None if pd.isna(x) else f"{x:.2%}")
history_view["Equity"] = history_view["Equity"].apply(lambda x: f"{x:.3f}")
st.dataframe(history_view, use_container_width=True, hide_index=True)

st.subheader("How the entry filter works")
ema_text = "disabled"
if config.get("ema_enabled"):
    direction = config.get("ema_direction", "Above EMA")
    ema_text = f"{direction}: {', '.join(map(str, config.get('ema_periods', [])))} EMA"
retr_text = "disabled"
if config.get("retracement_enabled"):
    retr_text = f"{config.get('retracement_reference')} between {config.get('retracement_min', 0):g}% and {config.get('retracement_max', 100):g}%"
st.markdown(
    f"""
- **EMA filter:** {ema_text}. All selected EMA conditions must pass.
- **52-week filter:** {retr_text}.
- **Entry:** a stock must pass the enabled technical filters **AND** qualify in the momentum ranking.
- **No entry:** if zero stocks pass all enabled filters, the scanner displays **NO ENTRY** and does not force a stock into the portfolio.
- **Existing holdings:** the EMA/retracement rules are used to control **new entries**; the existing rank-based exit buffer remains unchanged.
- **Rebalance:** the strategy is checked at the end of each month for the backtest.
"""
)

csv_history = history.to_csv(index=False).encode("utf-8")
st.download_button(
    "Download rebalance history CSV",
    data=csv_history,
    file_name="momentum_rebalance_history.csv",
    mime="text/csv",
)

st.caption(
    f"Data status: {source}. "
    "For Zerodha, daily candles are fetched through Kite Connect; the access token is valid for one day and requires the normal daily login flow. "
    "This is a research/backtesting scanner; verify data quality and licensing before live trading."
)
