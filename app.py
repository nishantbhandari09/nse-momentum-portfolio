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
import yfinance as yf

try:
    from kiteconnect import KiteConnect
except ImportError:
    KiteConnect = None

st.set_page_config(
    page_title="Nifty 500 Momentum Portfolio & Backtest",
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


def secret(name, default=None):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def normalize_symbols(values):
    result, seen = [], set()
    for value in values:
        symbol = str(value).strip().upper()
        symbol = symbol.replace("NSE:", "").replace(".NS", "")
        if symbol and symbol not in seen:
            result.append(symbol)
            seen.add(symbol)
    return result


def parse_int_list(text):
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if item:
            n = int(item)
            if n > 0:
                values.append(n)
    return values


def parse_float_list(text):
    values = []
    for item in str(text).replace(";", ",").split(","):
        item = item.strip()
        if item:
            values.append(float(item))
    return values


def safe_hash(payload):
    raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def fmt_pct(x):
    return "—" if x is None or pd.isna(x) else f"{x:.2f}%"


def latest_available_date(df, requested_date):
    if df.empty:
        return None
    idx = df.index[df.index <= pd.Timestamp(requested_date)]
    return idx[-1] if len(idx) else None


# -------------------- NIFTY 500 UNIVERSE --------------------
@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def load_nifty500_official():
    # Attempt 1: Fetch directly from NSE Live API
    nse_url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=headers, timeout=10)
        response = session.get(nse_url, headers=headers, timeout=10)
        if response.status_code == 200:
            stock_data = response.json().get("data", [])
            symbols = [item["symbol"] for item in stock_data if item.get("symbol") and item["symbol"] != "NIFTY 500"]
            symbols = normalize_symbols(symbols)
            if len(symbols) >= 400:
                return symbols, "Live NSE API (Nifty 500)"
    except Exception:
        pass  # Fall back to Nifty Indices CSV on connection error/block

    # Attempt 2: Fallback to official CSV index source
    r = requests.get(NIFTY500_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    cols = {str(c).strip().lower(): c for c in df.columns}
    symbol_col = next((cols[k] for k in ("symbol", "ticker", "tradingsymbol") if k in cols), df.columns[0])
    symbols = normalize_symbols(df[symbol_col].dropna().tolist())
    if len(symbols) < 400:
        raise RuntimeError(f"Nifty 500 source returned only {len(symbols)} symbols.")
    return symbols, "Official Nifty 500 CSV List"


def get_universe(uploaded_universe):
    if uploaded_universe is not None:
        df = pd.read_csv(uploaded_universe)
        cols = {str(c).strip().lower(): c for c in df.columns}
        symbol_col = next((cols[k] for k in ("symbol", "ticker", "tradingsymbol") if k in cols), df.columns[0])
        symbols = normalize_symbols(df[symbol_col].dropna().tolist())
        return symbols, "Uploaded Nifty 500 universe CSV"
    symbols, source_label = load_nifty500_official()
    return symbols, source_label


# -------------------- ZERODHA AUTH --------------------
def get_kite_session():
    if KiteConnect is None:
        return None, None, "kiteconnect is not installed."
    api_key = secret("KITE_API_KEY")
    api_secret = secret("KITE_API_SECRET")
    if not api_key or not api_secret:
        return None, None, "KITE_API_KEY / KITE_API_SECRET are missing from Streamlit Secrets."

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


# -------------------- YAHOO FINANCE DATA --------------------
@st.cache_data(ttl=12 * 60 * 60, show_spinner=False)
def fetch_yahoo_ohlcv(symbols, start_date, end_date):
    tickers = [f"{s}.NS" for s in symbols]
    data = yf.download(tickers, start=start_date, end=end_date, group_by="column", auto_adjust=True, threads=True)
    
    if data.empty:
        raise RuntimeError("Yahoo Finance returned no data.")

    def extract_field(field_name):
        df = data[field_name].copy() if field_name in data else pd.DataFrame()
        if isinstance(df, pd.Series):
            df = df.to_frame()
        df.columns = [c.replace(".NS", "") for c in df.columns]
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df.sort_index()

    closes = extract_field("Close")
    highs = extract_field("High")
    lows = extract_field("Low")

    return {"close": closes, "high": highs, "low": lows}, "Yahoo Finance OHLCV Engine", []


# -------------------- ZERODHA LIVE LTP --------------------
@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def get_nse_equity_instruments(api_key, access_token):
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    rows = kite.instruments("NSE")
    df = pd.DataFrame(rows)
    df["tradingsymbol"] = df["tradingsymbol"].astype(str).str.upper()
    eq = df[(df["segment"] == "NSE") & (df["instrument_type"] == "EQ")].copy()
    return eq[["instrument_token", "tradingsymbol", "name"]]


def fetch_zerodha_live_ltp(symbols, api_key, access_token):
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    keys = [f"NSE:{s}" for s in symbols]
    
    chunk_size = 250
    ltp_map = {}
    errors = []
    
    for i in range(0, len(keys), chunk_size):
        chunk = keys[i : i + chunk_size]
        try:
            res = kite.ltp(chunk)
            for k, v in res.items():
                sym = k.replace("NSE:", "")
                ltp_map[sym] = v.get("last_price")
        except Exception as exc:
            errors.append(f"Zerodha LTP chunk error: {exc}")

    return pd.Series(ltp_map), errors


# -------------------- GENERIC CSV / GOOGLE FINANCE PARSER --------------------
def _clean_columns(df):
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _find_col(columns, names):
    lower = {str(c).strip().lower(): c for c in columns}
    for name in names:
        if name in lower:
            return lower[name]
    return None


def parse_price_dataframe(raw_df, nifty_symbols):
    df = _clean_columns(raw_df)
    date_col = _find_col(df.columns, ["date", "datetime", "timestamp", "time"])
    symbol_col = _find_col(df.columns, ["symbol", "ticker", "tradingsymbol", "security"])
    close_col = _find_col(df.columns, ["close", "closing price", "ltp", "price", "last"])

    if date_col is None:
        date_col = df.columns[0]

    if symbol_col is not None and close_col is not None:
        dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        closes = pd.to_numeric(df[close_col].astype(str).str.replace(",", "", regex=False), errors="coerce")
        symbols = [normalize_symbols([v])[0] if normalize_symbols([v]) else "" for v in df[symbol_col]]
        tmp = pd.DataFrame({"Date": dates, "Symbol": symbols, "Close": closes})
        tmp = tmp.dropna(subset=["Date", "Symbol", "Close"])
        prices = tmp.pivot_table(index="Date", columns="Symbol", values="Close", aggfunc="last")
    else:
        dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)
        working = df.drop(columns=[date_col]).copy()
        kept = {}
        nifty_set = set(nifty_symbols)
        for col in working.columns:
            symbol = normalize_symbols([col])[0] if normalize_symbols([col]) else ""
            if symbol in nifty_set or symbol:
                vals = pd.to_numeric(working[col].astype(str).str.replace(",", "", regex=False), errors="coerce")
                if symbol:
                    kept[symbol] = vals
        prices = pd.DataFrame(kept, index=dates)
        prices = prices[~prices.index.isna()]

    prices.index = pd.to_datetime(prices.index).tz_localize(None)
    prices = prices.sort_index()
    prices = prices.loc[~prices.index.duplicated(keep="last")]
    prices.columns = normalize_symbols(prices.columns)

    keep = [c for c in prices.columns if c in set(nifty_symbols)]
    prices = prices[keep]
    prices = prices.apply(pd.to_numeric, errors="coerce")
    prices = prices.replace([np.inf, -np.inf], np.nan)
    prices = prices.dropna(axis=1, how="all")
    return {"close": prices, "high": prices, "low": prices}


@st.cache_data(ttl=60 * 60, show_spinner=False)
def load_price_csv_bytes(file_bytes, filename, nifty_symbols):
    raw = pd.read_csv(io.BytesIO(file_bytes))
    ohlcv = parse_price_dataframe(raw, list(nifty_symbols))
    if ohlcv["close"].empty:
        raise RuntimeError("No Nifty 500 price series could be identified in the CSV.")
    return ohlcv, f"CSV: {filename}"


@st.cache_data(ttl=60 * 60, show_spinner=False)
def load_google_sheet_csv(url, nifty_symbols):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    raw = pd.read_csv(io.StringIO(r.text))
    ohlcv = parse_price_dataframe(raw, list(nifty_symbols))
    if ohlcv["close"].empty:
        raise RuntimeError("The Google Sheet CSV did not contain usable Nifty 500 price columns.")
    return ohlcv, "Google Sheets / GOOGLEFINANCE CSV"


# -------------------- DATA SOURCE MANAGER --------------------
def get_data_source_prices(source, nifty_symbols, uploaded_price_file=None, google_url="", start=None, end=None):
    if source in ["Yahoo Finance", "Zerodha Live LTP + Yahoo Engine"]:
        return fetch_yahoo_ohlcv(tuple(nifty_symbols), str(start), str(end))

    if source == "NSE / CSV upload":
        if uploaded_price_file is None:
            raise RuntimeError("Upload your NSE/CSV price file first.")
        return load_price_csv_bytes(uploaded_price_file.getvalue(), uploaded_price_file.name, tuple(nifty_symbols)) + ([],)

    if source == "Google Finance / Google Sheets CSV":
        if not google_url.strip():
            raise RuntimeError("Paste a Google Sheets CSV export URL first.")
        return load_google_sheet_csv(google_url.strip(), tuple(nifty_symbols)) + ([],)

    raise RuntimeError("Unknown data source selected.")


# -------------------- INDICATORS --------------------
def calculate_snapshot(ohlcv, as_of_date, ema_periods, ema_direction, retracement_mode, retracement_threshold, live_ltp_series=None):
    prices = ohlcv["close"]
    highs = ohlcv["high"]
    lows = ohlcv["low"]

    actual_date = latest_available_date(prices, as_of_date)
    if actual_date is None:
        return pd.DataFrame()

    idx = prices.index.get_loc(actual_date)
    current = prices.iloc[idx].copy()

    if live_ltp_series is not None and not live_ltp_series.empty:
        current.update(live_ltp_series)

    window_high = highs.iloc[max(0, idx - 251): idx + 1]
    window_low = lows.iloc[max(0, idx - 251): idx + 1]

    high_52 = window_high.max(skipna=True)
    low_52 = window_low.min(skipna=True)
    range_52 = high_52 - low_52

    out = pd.DataFrame({"Price": current, "52W High": high_52, "52W Low": low_52})
    out["Distance from 52W High %"] = np.where(
        out["52W High"] != 0, (out["52W High"] - out["Price"]) / out["52W High"] * 100, np.nan
    )
    out["Position from 52W Low %"] = np.where(
        range_52 != 0, (out["Price"] - out["52W Low"]) / range_52 * 100, np.nan
    )

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
        period = int(period)
        if idx + 1 < period:
            out[f"EMA {period}"] = np.nan
            eligible &= False
        else:
            ema = prices.iloc[: idx + 1].ewm(span=period, adjust=False, min_periods=period).mean().iloc[-1]
            out[f"EMA {period}"] = ema
            eligible &= out["Price"] > ema if ema_direction == "Above EMA" else out["Price"] < ema

    eligible &= retr_pass
    out["Eligible"] = eligible.fillna(False)
    out["As Of"] = actual_date.date()
    return out.sort_index()


def rank_on_date(prices, as_of_date, lookbacks, weights):
    actual_date = latest_available_date(prices, as_of_date)
    if actual_date is None:
        return pd.Series(dtype=float)
    idx = prices.index.get_loc(actual_date)
    components, valid_weights = [], []
    for lookback, weight in zip(lookbacks, weights):
        lookback = int(lookback)
        if idx - lookback < 0:
            continue
        ret = (prices.iloc[idx] / prices.iloc[idx - lookback] - 1).replace([np.inf, -np.inf], np.nan).dropna()
        if ret.empty:
            continue
        components.append(ret.rank(ascending=False, method="min"))
        valid_weights.append(float(weight))
    if not components:
        return pd.Series(dtype=float)
    w = np.asarray(valid_weights, dtype=float)
    w = np.ones(len(w)) / len(w) if w.sum() == 0 else w / w.sum()
    composite = pd.concat(components, axis=1).mul(w, axis=1).sum(axis=1)
    return composite.rank(ascending=True, method="min").sort_values()


def build_signal_table(ohlcv, as_of_date, target_n, exit_rank, lookbacks, weights,
                        ema_periods, ema_direction, retracement_mode, retracement_threshold, live_ltp_series=None):
    snap = calculate_snapshot(ohlcv, as_of_date, ema_periods, ema_direction, retracement_mode, retracement_threshold, live_ltp_series)
    ranks = rank_on_date(ohlcv["close"], as_of_date, lookbacks, weights)
    if snap.empty or ranks.empty:
        return pd.DataFrame()
    df = snap.join(ranks.rename("Momentum Rank"), how="left").dropna(subset=["Momentum Rank"]).copy()
    df["Momentum Rank"] = df["Momentum Rank"].astype(int)
    df["Action"] = np.where(
        ~df["Eligible"], "NO ENTRY",
        np.where(df["Momentum Rank"] <= int(target_n), "ENTRY / TOP HOLDING",
                 np.where(df["Momentum Rank"] < int(exit_rank), "HOLD / BUFFER", "EXIT ZONE"))
    )
    df = df.sort_values(["Eligible", "Momentum Rank"], ascending=[False, True])
    df.insert(0, "Symbol", df.index)
    return df.reset_index(drop=True)


# -------------------- BACKTEST --------------------
def rebalance_portfolio(holdings, ranks, eligible_symbols, target_n, exit_rank):
    if ranks.empty:
        return [], [], list(holdings)
    kept = [s for s in holdings if s in ranks.index and int(ranks[s]) < int(exit_rank)]
    exits = [s for s in holdings if s not in kept]
    entries = []
    for symbol in ranks.index:
        if symbol in kept or symbol not in eligible_symbols:
            continue
        entries.append(symbol)
        if len(kept) + len(entries) >= int(target_n):
            break
    return kept + entries, entries, exits


def month_end_trading_dates(prices, start_date, end_date):
    idx = prices.index[(prices.index >= pd.Timestamp(start_date)) & (prices.index <= pd.Timestamp(end_date))]
    if len(idx) == 0:
        return []
    return pd.Series(idx, index=idx).groupby(idx.to_period("M")).max().tolist()


def run_backtest(ohlcv, start_date, end_date, target_n, exit_rank, lookbacks, weights,
                 ema_periods, ema_direction, retracement_mode, retracement_threshold):
    prices = ohlcv["close"]
    dates = month_end_trading_dates(prices, start_date, end_date)
    max_history = max(max(lookbacks), max(ema_periods or [0]), 252)
    valid_dates = [d for d in dates if prices.index.get_loc(d) >= max_history]
    holdings, rows = [], []
    equity = 1.0
    previous_date = None
    previous_holdings = []

    for d in valid_dates:
        ranks = rank_on_date(prices, d, lookbacks, weights)
        snap = calculate_snapshot(ohlcv, d, ema_periods, ema_direction, retracement_mode, retracement_threshold)
        eligible = set(snap.index[snap["Eligible"]]) if not snap.empty else set()
        new_holdings, entries, exits = rebalance_portfolio(holdings, ranks, eligible, target_n, exit_rank)
        period_return = np.nan
        if previous_date is not None and previous_holdings:
            prev = prices.loc[previous_date, previous_holdings].dropna()
            curr = prices.loc[d, previous_holdings].dropna()
            aligned = pd.concat([prev.rename("prev"), curr.rename("curr")], axis=1).dropna()
            if not aligned.empty:
                period_return = float((aligned["curr"] / aligned["prev"] - 1).mean())
                equity *= 1 + period_return
        rows.append({
            "Date": d,
            "Portfolio Size": len(new_holdings),
            "Holdings": ", ".join(new_holdings),
            "New Entries": ", ".join(entries),
            "Exits": ", ".join(exits),
            "Eligible Candidates": len(eligible),
            "Turnover": len(entries) + len(exits),
            "Portfolio Return": period_return,
            "Equity": equity,
        })
        holdings = list(new_holdings)
        previous_holdings = list(new_holdings)
        previous_date = d
    return pd.DataFrame(rows)


def backtest_metrics(history):
    if history.empty:
        return {}
    equity = history["Equity"].dropna()
    ret = history["Portfolio Return"].dropna()
    if equity.empty:
        return {}
    days = max((pd.Timestamp(history["Date"].iloc[-1]) - pd.Timestamp(history["Date"].iloc[0])).days, 1)
    years = days / 365.25
    cagr = equity.iloc[-1] ** (1 / years) - 1
    dd = (equity / equity.cummax() - 1).min()
    return {
        "CAGR": cagr,
        "Total Return": equity.iloc[-1] - 1,
        "Max Drawdown": dd,
        "Win Rate": (ret > 0).mean() if not ret.empty else np.nan,
        "Turnover": int(history["Turnover"].fillna(0).sum()),
    }


# -------------------- UI --------------------
st.title("📈 Nifty 500 Momentum Portfolio & Backtesting Tool")
st.caption("One app • Nifty 500 universe • Fast Yahoo Finance Engine + Zerodha Live LTP option")

with st.sidebar:
    st.header("Universe")
    universe_upload = st.file_uploader("Optional Nifty 500 universe CSV", type=["csv"], key="universe_upload")
    try:
        universe, universe_source = get_universe(universe_upload)
    except Exception as exc:
        st.error(f"Could not load Nifty 500 universe: {exc}")
        st.stop()
    st.success(f"Nifty 500 universe: {len(universe)} symbols")
    st.caption(universe_source)

    st.divider()
    st.header("Data source")
    data_source = st.radio(
        "Run the scanner using",
        [
            "Zerodha Live LTP + Yahoo Engine",
            "Yahoo Finance",
            "NSE / CSV upload",
            "Google Finance / Google Sheets CSV",
        ],
        index=0,
    )

    price_upload = None
    google_url = ""
    if data_source == "NSE / CSV upload":
        price_upload = st.file_uploader(
            "Upload NSE price CSV",
            type=["csv"],
            key="price_upload",
            help="Accepted formats: Date + stock columns, or Date + Symbol + Close.",
        )
    elif data_source == "Google Finance / Google Sheets CSV":
        google_url = st.text_input(
            "Google Sheets CSV export URL",
            placeholder="https://docs.google.com/spreadsheets/d/.../export?format=csv",
            help="Your Google Sheet can use GOOGLEFINANCE formulas, then expose the sheet as CSV.",
        )

    st.divider()
    st.header("Portfolio rules")
    target_n = st.number_input("Top N / target holdings", 1, 500, 20, 1)
    exit_rank = st.number_input("Exit when momentum rank reaches", 2, 500, DEFAULT_EXIT_RANK, 1)
    lookback_text = st.text_input("Momentum lookbacks (trading days)", "252,120,90,60")
    weight_text = st.text_input("Weights (blank = equal)", "")

    st.divider()
    st.header("EMA entry filter")
    ema_enabled = st.checkbox("Enable EMA filter", value=True)
    if ema_enabled:
        ema_periods = st.multiselect(
            "EMA period(s) — ALL must pass",
            [10, 20, 50, 100, 150, 200],
            default=[50],
        )
        ema_direction = st.selectbox("Price condition", ["Above EMA", "Below EMA"])
    else:
        ema_periods = []
        ema_direction = "Above EMA"

    st.divider()
    st.header("52-week retracement")
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
            "One threshold %",
            min_value=0.0,
            max_value=100.0,
            value=40.0,
            step=1.0,
            help="High mode: price can be at most this % below its 52W high. Low mode: price must be at least this % up from 52W low.",
        )
    else:
        retracement_mode = "Disabled"
        retracement_threshold = 40.0

    st.divider()
    st.header("Data settings")
    history_years = st.slider("History to load (years)", 1, 8, 5, 1)
    force_refresh = st.checkbox("Force refresh downloaded data", False)

    st.divider()
    st.header("Zerodha connection")
    kite, access_token, kite_status = get_kite_session()
    if data_source == "Zerodha Live LTP + Yahoo Engine":
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
                st.error("Could not create the Zerodha login URL.")
        else:
            st.error(kite_status)
    else:
        st.caption("Zerodha login is optional for this data source.")

    if force_refresh:
        load_nifty500_official.clear()
        load_price_csv_bytes.clear()
        load_google_sheet_csv.clear()
        fetch_yahoo_ohlcv.clear()
        get_nse_equity_instruments.clear()

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

if exit_rank <= target_n:
    st.warning("For a rank buffer, Exit Rank should normally be higher than Top N.")


# -------------------- COMMON DATA LOAD FUNCTION --------------------
def load_for_run(start, end):
    ohlcv, source, warnings = get_data_source_prices(
        data_source,
        universe,
        uploaded_price_file=price_upload,
        google_url=google_url,
        start=start,
        end=end,
    )
    if ohlcv["close"].empty:
        raise RuntimeError("No price data returned.")
    if len(ohlcv["close"].columns) < 400:
        st.warning(
            f"Only {len(ohlcv['close'].columns)} of the Nifty 500 symbols are available in this data source."
        )
    return ohlcv, source, warnings


# -------------------- TABS --------------------
tab_portfolio, tab_backtest = st.tabs(["📊 Portfolio / Live Signals", "🧪 Backtesting"])

with tab_portfolio:
    st.subheader("Current portfolio inclusion, entry, hold and exit signals")
    c1, c2 = st.columns(2)
    with c1:
        portfolio_date = st.date_input("Analysis date", date.today(), key="portfolio_date")
    with c2:
        portfolio_end = st.date_input("Data end date", date.today(), key="portfolio_end")

    if st.button("▶ Build Current Portfolio", type="primary", use_container_width=True, key="portfolio_run"):
        start = pd.Timestamp(portfolio_date) - pd.Timedelta(days=365 * history_years)
        end = max(pd.Timestamp(portfolio_end), pd.Timestamp(portfolio_date))
        try:
            with st.status(f"Loading {data_source} data…", expanded=True):
                ohlcv, source, warnings = load_for_run(start.date(), end.date())
                
                live_ltp = None
                if data_source == "Zerodha Live LTP + Yahoo Engine" and access_token:
                    live_ltp, zerodha_errs = fetch_zerodha_live_ltp(universe, secret("KITE_API_KEY"), access_token)
                    warnings.extend(zerodha_errs)

                live = build_signal_table(
                    ohlcv,
                    portfolio_date,
                    int(target_n),
                    int(exit_rank),
                    lookbacks,
                    weights,
                    ema_periods if ema_enabled else [],
                    ema_direction,
                    retracement_mode if retracement_enabled else "Disabled",
                    retracement_threshold,
                    live_ltp_series=live_ltp,
                )
                if live.empty:
                    raise RuntimeError("Insufficient historical data to calculate strategy on this date.")
                st.session_state["portfolio_prices"] = ohlcv["close"]
                st.session_state["portfolio_live"] = live
                st.session_state["portfolio_source"] = source
                st.session_state["portfolio_warnings"] = warnings
                st.success(f"Portfolio built using: {source}")
        except Exception as exc:
            st.error(f"Portfolio update failed: {exc}")

    if "portfolio_live" in st.session_state:
        live = st.session_state["portfolio_live"].copy()
        prices = st.session_state["portfolio_prices"]
        eligible = live[live["Eligible"]].sort_values("Momentum Rank").head(int(target_n)).copy()
        exits = live[live["Action"] == "EXIT ZONE"]

        a, b, c, d = st.columns(4)
        a.metric("Data date", prices.index[-1].strftime("%d %b %Y"))
        b.metric("Nifty 500 symbols available", len(prices.columns))
        c.metric("Eligible stocks", int(live["Eligible"].sum()))
        d.metric("Exit-zone", len(exits))

        if eligible.empty:
            st.error("🔴 NO ENTRY — no stock satisfies all selected conditions and rank rules.")
        else:
            st.success(f"🟢 {len(eligible)} stocks qualify for portfolio inclusion / Top {target_n}.")

        st.markdown("### Portfolio inclusion / entry")
        wanted = [
            "Symbol", "Action", "Momentum Rank", "Price", "52W High", "52W Low",
            "Distance from 52W High %", "Position from 52W Low %", "Retracement Metric %",
        ] + [f"EMA {p}" for p in ema_periods if f"EMA {p}" in live.columns]
        st.dataframe(eligible[wanted] if not eligible.empty else pd.DataFrame(columns=wanted), use_container_width=True, hide_index=True)

        st.markdown("### Full Nifty 500 scan")
        st.dataframe(live, use_container_width=True, hide_index=True)

        if st.session_state.get("portfolio_warnings"):
            with st.expander("Data warnings"):
                st.write("\n".join(st.session_state["portfolio_warnings"][:100]))

        st.download_button(
            "Download current signals CSV",
            live.to_csv(index=False).encode("utf-8"),
            "nifty500_current_signals.csv",
            "text/csv",
        )

with tab_backtest:
    st.subheader("Historical backtesting")
    b1, b2 = st.columns(2)
    with b1:
        bt_start = st.date_input("Backtest start date", date(2022, 1, 1), key="bt_start")
    with b2:
        bt_end = st.date_input("Backtest end date", date.today(), key="bt_end")

    if st.button("🧪 Run Backtest", type="primary", use_container_width=True, key="backtest_run"):
        if bt_start >= bt_end:
            st.error("Backtest start date must be before the end date.")
        else:
            history_pad = max(max(lookbacks), max(ema_periods or [0]), 252) * 2
            required_start = pd.Timestamp(bt_start) - pd.Timedelta(days=history_pad)
            try:
                with st.status("Loading data and running backtest…", expanded=True):
                    ohlcv, source, warnings = load_for_run(required_start.date(), bt_end)
                    history = run_backtest(
                        ohlcv,
                        bt_start,
                        bt_end,
                        int(target_n),
                        int(exit_rank),
                        lookbacks,
                        weights,
                        ema_periods if ema_enabled else [],
                        ema_direction,
                        retracement_mode if retracement_enabled else "Disabled",
                        retracement_threshold,
                    )
                    st.session_state["backtest_history"] = history
                    st.session_state["backtest_prices"] = ohlcv["close"]
                    st.session_state["backtest_source"] = source
                    st.session_state["backtest_warnings"] = warnings
                    st.success(f"Backtest completed from: {source}")
            except Exception as exc:
                st.error(f"Backtest failed: {exc}")

    if "backtest_history" in st.session_state:
        history = st.session_state["backtest_history"].copy()
        metrics = backtest_metrics(history)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("CAGR", fmt_pct(metrics.get("CAGR", np.nan) * 100))
        m2.metric("Total return", fmt_pct(metrics.get("Total Return", np.nan) * 100))
        m3.metric("Max drawdown", fmt_pct(metrics.get("Max Drawdown", np.nan) * 100))
        m4.metric("Win rate", fmt_pct(metrics.get("Win Rate", np.nan) * 100))
        m5.metric("Turnover", metrics.get("Turnover", 0))

        if not history.empty:
            st.markdown("### Equity curve")
            st.line_chart(history.set_index("Date")["Equity"])
            st.markdown("### Rebalance / entry / exit history")
            st.dataframe(history, use_container_width=True, hide_index=True)

            st.download_button(
                "Download backtest CSV",
                history.to_csv(index=False).encode("utf-8"),
                "nifty500_backtest_results.csv",
                "text/csv",
            )
