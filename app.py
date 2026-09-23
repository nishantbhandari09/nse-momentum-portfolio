import io
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# -----------------------------------------------------------------------------
# APP CONFIGURATION
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="NSE Momentum Portfolio & Backtest Studio",
    page_icon="📈",
    layout="wide",
)

DATA_URL_PARQUET = "https://github.com/rishabhjain0295-web/nifty500-momentum-backtest/releases/download/data-v1/nifty500_historical.parquet"
DATA_URL_CSV = "https://github.com/rishabhjain0295-web/nifty500-momentum-backtest/releases/download/data-v1/nifty500_historical.csv.gz"

# -----------------------------------------------------------------------------
# 1. DATA LAYER (Release Data + Fallback)
# -----------------------------------------------------------------------------
@st.cache_data(ttl=86400)
def load_historical_dataset():
    """Fetches clean point-in-time dataset from release storage or falls back to yfinance."""
    try:
        df = pd.read_parquet(DATA_URL_PARQUET)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        try:
            df = pd.read_csv(DATA_URL_CSV, compression="gzip", index_col=0, parse_dates=True)
            return df
        except Exception:
            # Fallback to yfinance if release assets are unreachable
            benchmark = yf.download("^NSEI", period="5y", progress=False)["Close"]
            return pd.DataFrame({"^NSEI": benchmark})

@st.cache_data(ttl=86400)
def fetch_ticker_data(tickers, start_date, end_date):
    """Downloads prices via yfinance if ticker missing from offline dataset."""
    data = yf.download(tickers, start=start_date, end=end_date, progress=False)["Close"]
    return data

# -----------------------------------------------------------------------------
# 2. OPTIONAL SIGNAL LAYER
# -----------------------------------------------------------------------------
def calculate_simple_momentum(price_df, lookback_days=126):
    """Simple trailing percentage return."""
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)
    returns = (price_df.iloc[-1] / price_df.iloc[-lookback_days]) - 1.0
    return returns

def calculate_volatility_adjusted_momentum(price_df, lookback_days=126):
    """Volatility-Adjusted Momentum: (Return / Volatility) * R^2."""
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)

    subset = price_df.tail(lookback_days)
    daily_returns = subset.pct_change().dropna()
    total_return = (subset.iloc[-1] / subset.iloc[0]) - 1.0
    annualized_vol = daily_returns.std() * np.sqrt(252)

    # Calculate exponential trend fitting (R^2)
    scores = {}
    x = np.arange(len(subset))
    for col in subset.columns:
        y = np.log(subset[col].values)
        if np.isnan(y).any() or annualized_vol[col] == 0:
            scores[col] = 0.0
            continue
        corr = np.corrcoef(x, y)[0, 1]
        r_squared = corr ** 2 if not np.isnan(corr) else 0.0
        scores[col] = (total_return[col] / annualized_vol[col]) * r_squared

    return pd.Series(scores)

def get_momentum_scores(price_df, signal_type="Simple Momentum", lookback_days=126):
    """Routes score calculation based on user selection in Signal Layer."""
    if signal_type == "Volatility-Adjusted Momentum":
        return calculate_volatility_adjusted_momentum(price_df, lookback_days)
    else:
        return calculate_simple_momentum(price_df, lookback_days)

# -----------------------------------------------------------------------------
# 3. OPTIONAL EXECUTION LAYER & REGIME FILTERS
# -----------------------------------------------------------------------------
def is_regime_bullish(benchmark_series, current_date, sma_period=200):
    """Check if benchmark price is above its 200 SMA."""
    sub = benchmark_series.loc[:current_date].dropna()
    if len(sub) < sma_period:
        return True
    current_val = sub.iloc[-1]
    sma_val = sub.tail(sma_period).mean()
    return current_val >= sma_val

def apply_rank_buffering(current_holdings, ranked_candidates, target_portfolio_size, buffer_rank):
    """Keeps existing stock holdings if they haven't dropped past the buffer rank."""
    new_portfolio = []

    # 1. Retain existing holdings inside buffer rank limit
    for stock in current_holdings:
        if stock in ranked_candidates:
            stock_rank = ranked_candidates.index(stock) + 1  # 1-indexed
            if stock_rank <= buffer_rank and len(new_portfolio) < target_portfolio_size:
                new_portfolio.append(stock)

    # 2. Fill remaining slots with top unheld ranked candidates
    for stock in ranked_candidates:
        if len(new_portfolio) >= target_portfolio_size:
            break
        if stock not in new_portfolio:
            new_portfolio.append(stock)

    return new_portfolio

# -----------------------------------------------------------------------------
# 4. BACKTEST ENGINE
# -----------------------------------------------------------------------------
def run_backtest_simulation(
    price_df,
    benchmark_series,
    start_date,
    end_date,
    initial_capital=100000.0,
    top_n=10,
    signal_type="Simple Momentum",
    use_regime_filter=True,
    use_rank_buffer=True,
    buffer_threshold=20,
    allocation_multiplier=1.0,
):
    sub_prices = price_df.loc[start_date:end_date].copy()
    if len(sub_prices) < 252:
        return None

    # Resample using updated Pandas 'ME' (Month End) frequency
    try:
        rebalance_dates = sub_prices.resample("ME").first().index
    except ValueError:
        rebalance_dates = sub_prices.resample("MS").first().index

    portfolio_history = []
    current_cash = initial_capital
    current_holdings = {}

    for i in range(len(rebalance_dates) - 1):
        dt = rebalance_dates[i]
        hist_df = sub_prices.loc[:dt]
        if len(hist_df) < 126:
            continue

        # Check Market Regime Filter (Optional Execution Layer)
        regime_ok = True
        if use_regime_filter and benchmark_series is not None:
            regime_ok = is_regime_bullish(benchmark_series, dt, sma_period=200)

        # Mark-to-market valuation
        total_val = current_cash
        for sym, qty in current_holdings.items():
            if sym in hist_df.columns and not np.isnan(hist_df[sym].iloc[-1]):
                total_val += qty * hist_df[sym].iloc[-1]

        # Calculate momentum signals (Optional Signal Layer)
        scores = get_momentum_scores(hist_df, signal_type=signal_type, lookback_days=126)
        ranked_stocks = scores.sort_values(ascending=False).dropna().index.tolist()

        if not regime_ok:
            # Move 100% to cash during bearish market regimes
            current_holdings = {}
            current_cash = total_val
        else:
            # Apply Rank Buffer Rule (Optional Execution Layer)
            if use_rank_buffer and current_holdings:
                target_stocks = apply_rank_buffering(
                    list(current_holdings.keys()), ranked_stocks, top_n, buffer_threshold
                )
            else:
                target_stocks = ranked_stocks[:top_n]

            # Portfolio Allocation logic
            if target_stocks:
                effective_pool = total_val * allocation_multiplier
                alloc_per_stock = effective_pool / len(target_stocks)
                current_holdings = {}
                current_cash = total_val

                for sym in target_stocks:
                    if sym in hist_df.columns:
                        stk_price = hist_df[sym].iloc[-1]
                        if stk_price > 0:
                            qty = int(alloc_per_stock // stk_price)
                            if qty > 0:
                                current_holdings[sym] = qty
                                current_cash -= qty * stk_price

        portfolio_history.append({"Date": dt, "Portfolio Value": total_val})

    if not portfolio_history:
        return None

    res_df = pd.DataFrame(portfolio_history).set_index("Date")
    return res_df

# -----------------------------------------------------------------------------
# 5. STREAMLIT UI LAYOUT & SIDEBAR OPTIONS
# -----------------------------------------------------------------------------
st.title("📈 Backtest Studio — Momentum Strategy")
st.caption("Simulate historical performance with flexible signal modes and execution filters.")

# Sidebar Configuration
st.sidebar.header("⚙️ General Settings")
initial_cap = st.sidebar.number_input("Initial Capital (₹)", value=100000, step=10000)
top_n = st.sidebar.slider("Number of Top Holdings", min_value=5, max_value=30, value=10)

st.sidebar.markdown("---")
st.sidebar.header("🧠 1. Signal Layer (Optional)")
signal_type = st.sidebar.selectbox(
    "Choose Momentum Model",
    options=["Simple Momentum", "Volatility-Adjusted Momentum"],
    help="Select how stocks are ranked.",
)

st.sidebar.markdown("---")
st.sidebar.header("🛡️ 2. Execution Layer (Optional)")
use_regime_filter = st.sidebar.checkbox(
    "Enable Market Regime Filter (200 SMA)",
    value=True,
    help="Move portfolio to cash when Nifty is below its 200-day moving average.",
)

use_rank_buffer = st.sidebar.checkbox(
    "Enable Rank Buffer Rule (Reduce Churn)",
    value=True,
    help="Avoid selling existing holdings unless their rank falls below the buffer threshold.",
)

buffer_threshold = 20
if use_rank_buffer:
    buffer_threshold = st.sidebar.number_input(
        "Buffer Rank Exit Cutoff", min_value=top_n, max_value=100, value=20
    )

alloc_multiplier = st.sidebar.slider("Allocation Multiplier", 0.5, 1.5, 1.0, step=0.1)

# App Data Loading
with st.spinner("Loading release historical dataset..."):
    dataset = load_historical_dataset()

# Split stock universe and benchmark
if "^NSEI" in dataset.columns:
    benchmark_series = dataset["^NSEI"]
    stock_df = dataset.drop(columns=["^NSEI"])
else:
    stock_df = dataset
    benchmark_series = dataset.iloc[:, 0] if not dataset.empty else None

# Execution Trigger
if st.button("🚀 Run Backtest Simulation", type="primary"):
    st.info(f"Running simulation with Signal: **{signal_type}**")

    res_df = run_backtest_simulation(
        price_df=stock_df,
        benchmark_series=benchmark_series,
        start_date="2020-01-01",
        end_date="2026-09-18",
        initial_capital=initial_cap,
        top_n=top_n,
        signal_type=signal_type,
        use_regime_filter=use_regime_filter,
        use_rank_buffer=use_rank_buffer,
        buffer_threshold=buffer_threshold,
        allocation_multiplier=alloc_multiplier,
    )

    if res_df is not None:
        st.success("Backtest simulation completed!")
        st.line_chart(res_df["Portfolio Value"])

        # Summary Metrics
        tot_ret = (res_df["Portfolio Value"].iloc[-1] - initial_cap) / initial_cap * 100
        c1, c2 = st.columns(2)
        c1.metric("Final Portfolio Value", f"₹{res_df['Portfolio Value'].iloc[-1]:,.2f}")
        c2.metric("Total Strategy Return", f"{tot_ret:.2f}%")
    else:
        st.error("Insufficient price history available for the selected date range.")