import hashlib
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
import yfinance as yf

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
            raise ValueError(f"Invalid lookback value: {item}")
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
    raw = json.dumps(
        {"tickers": sorted(tickers), "start": start, "end": end},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_prices_cached(tickers, start, end, batch_size, delay):
    """Fetch daily close prices in batches, with a 24-hour Streamlit cache."""
    key = cache_key(tickers, start, end)
    path = CACHE_DIR / f"prices_{key}.parquet"

    if path.exists():
        return pd.read_parquet(path), "local parquet cache"

    frames = []
    errors = []
    total_batches = (len(tickers) + batch_size - 1) // batch_size

    for batch_no, offset in enumerate(range(0, len(tickers), batch_size), start=1):
        batch = tickers[offset : offset + batch_size]
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
                    close_key = "Close" if "Close" in top_level else top_level[0]
                    frame = downloaded[close_key].copy()
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
    if weights_arr.sum() == 0:
        weights_arr = np.ones(len(weights_arr), dtype=float) / len(weights_arr)
    else:
        weights_arr = weights_arr / weights_arr.sum()

    rank_matrix = pd.concat(rank_frames, axis=1)
    composite_score = rank_matrix.mul(weights_arr, axis=1).sum(axis=1)
    return composite_score.rank(ascending=True, method="min").sort_values()


def rebalance(current_portfolio, ranks, target_n, exit_rank):
    if ranks.empty:
        return current_portfolio, [], []

    keep = [
        symbol
        for symbol in current_portfolio
        if symbol in ranks.index and ranks[symbol] < exit_rank
    ]
    exits = [symbol for symbol in current_portfolio if symbol not in keep]

    needed = max(0, target_n - len(keep))
    entries = []
    for symbol in ranks.index:
        if symbol not in keep:
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


def run_backtest(prices, lookbacks, weights, target_n, exit_rank, start_date):
    rebalance_dates = monthly_rebalance_dates(prices)
    max_lookback = max(lookbacks)
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
        new_portfolio, entries, exits = rebalance(
            portfolio, ranks, target_n, exit_rank
        )

        monthly_return = np.nan
        if previous_date is not None and previous_holdings:
            prev_prices = prices.loc[previous_date, previous_holdings]
            curr_prices = prices.loc[current_date, previous_holdings]
            aligned = pd.concat(
                [prev_prices.rename("prev"), curr_prices.rename("curr")], axis=1
            ).dropna()
            if not aligned.empty:
                monthly_return = float((aligned["curr"] / aligned["prev"] - 1).mean())
                equity *= 1 + monthly_return

        rows.append(
            {
                "Date": current_date,
                "Portfolio Size": len(new_portfolio),
                "Holdings": ", ".join(new_portfolio),
                "New Entries": ", ".join(entries),
                "Exits": ", ".join(exits),
                "Turnover": len(entries) + len(exits),
                "Portfolio Return": monthly_return,
                "Equity": equity,
            }
        )
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
        drawdown = equity / peak - 1
        max_dd = drawdown.min()

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
st.caption("Multi-period momentum ranking + monthly portfolio rebalancing + configurable exit buffer")

with st.sidebar:
    st.header("Strategy")
    target_n = st.number_input("Target holdings / Entry Top N", min_value=1, max_value=500, value=20, step=1)
    exit_rank = st.number_input("Exit when rank reaches", min_value=2, max_value=1000, value=41, step=1)
    lookback_text = st.text_input("Lookback periods (trading days)", "252,120,90,60")
    weight_text = st.text_input("Weights (blank = equal)", "")

    st.divider()
    st.header("Data")
    end_date = st.date_input("Data end date", value=date.today())
    start_date = st.date_input(
        "Data start date",
        value=end_date - timedelta(days=365 * 5),
    )
    batch_size = st.number_input("Stocks per data request", min_value=1, max_value=50, value=15, step=1)
    delay = st.number_input("Delay between requests (seconds)", min_value=0.0, max_value=10.0, value=1.5, step=0.5)
    refresh = st.checkbox("Force refresh / ignore 24-hour cache", value=False)

    st.divider()
    st.header("Universe")
    uploaded = st.file_uploader(
        "Upload a CSV containing Symbol or Ticker",
        type=["csv"],
        help="For the real system, use your maintained Nifty 500 universe CSV.",
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
            st.write("Downloading/caching daily closing prices in batches…")
            prices, source = fetch_prices_cached(
                tuple(universe), str(start_date), str(end_date), int(batch_size), float(delay)
            )
            st.write("Calculating monthly rankings and portfolio changes…")
            history = run_backtest(
                prices,
                lookbacks,
                weights,
                int(target_n),
                int(exit_rank),
                str(start_date),
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

metrics = portfolio_metrics(history)
latest_date = prices.index[-1].date()

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Latest data", latest_date.strftime("%d %b %Y"))
m2.metric("Stocks in universe", len(prices.columns))
m3.metric("CAGR", "—" if pd.isna(metrics["CAGR"]) else f"{metrics['CAGR']:.1%}")
m4.metric("Max drawdown", "—" if pd.isna(metrics["Max Drawdown"]) else f"{metrics['Max Drawdown']:.1%}")
m5.metric("Total turnover", f"{metrics['Turnover']}")

latest_rebalance = history.iloc[-1]
latest_ranks = rank_on_date(prices, latest_date, lookbacks, weights)
latest_rank_df = pd.DataFrame({
    "Symbol": latest_ranks.index,
    "Composite Rank": latest_ranks.values,
})
latest_rank_df["Zone"] = np.select(
    [
        latest_rank_df["Composite Rank"] <= target_n,
        latest_rank_df["Composite Rank"] < exit_rank,
    ],
    ["ENTRY / TOP HOLDINGS", "BUFFER / HOLD"],
    default="EXIT ZONE",
)

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

st.subheader("Current ranking")
ranking_view = latest_rank_df.head(max(int(exit_rank), int(target_n)) + 10).copy()
st.dataframe(ranking_view, use_container_width=True, hide_index=True)

st.subheader("Backtest / Rebalance history")
history_view = history.copy()
history_view["Date"] = pd.to_datetime(history_view["Date"]).dt.date
history_view["Portfolio Return"] = history_view["Portfolio Return"].apply(
    lambda x: None if pd.isna(x) else f"{x:.2%}"
)
history_view["Equity"] = history_view["Equity"].apply(lambda x: f"{x:.3f}")
st.dataframe(history_view, use_container_width=True, hide_index=True)

st.subheader("How the buffer works")
st.markdown(
    f"""
- **Enter:** stocks in the top **{target_n}** are eligible to fill open portfolio slots.
- **Hold:** an existing stock stays in the portfolio while its rank is **below {exit_rank}**.
- **Exit:** an existing stock is removed when its rank reaches **{exit_rank} or worse**.
- **Rebalance:** the strategy is checked at the **end of each month**.
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
    f"Data status: {source}. This dashboard uses Yahoo Finance via yfinance for the starter version. "
    "It is a research/backtesting tool; verify data quality and licensing before any commercial use."
)
