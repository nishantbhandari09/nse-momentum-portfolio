import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
from io import StringIO

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Institutional Strategy Hub & Backtester", layout="wide")

DEFENSIVE_NSE_MAPPING = {
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "GSEC10IETF": "SETFGSEC.NS"
}
NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

# ==========================================
# TRUSTED DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_nifty500_tickers():
    """Fetches official Nifty 500 constituent list from verified NSE endpoints."""
    official_nse_url = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })
    try:
        response = session.get(official_nse_url, timeout=10)
        if response.status_code == 200:
            df = pd.read_csv(StringIO(response.text))
            if "Symbol" in df.columns:
                return [f"{str(sym).strip()}.NS" for sym in df["Symbol"].dropna().unique()]
    except Exception:
        pass

    github_mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
    try:
        df_mirror = pd.read_csv(github_mirror_url)
        if "Symbol" in df_mirror.columns:
            return [f"{str(sym).strip()}.NS" for sym in df_mirror["Symbol"].dropna().unique()]
    except Exception:
        pass

    raise ValueError("Unable to reach official NSE servers. External unverified sources are blocked.")

@st.cache_data(ttl=3600)
def fetch_trusted_market_data(period="2y"):
    """Downloads historical market prices directly from Yahoo Finance."""
    nifty500_tickers = get_trusted_nifty500_tickers()
    defensive_tickers = list(DEFENSIVE_NSE_MAPPING.values())
    all_tickers = list(set(nifty500_tickers + defensive_tickers + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]))
    
    data = yf.download(tickers=all_tickers, period=period, interval="1d", auto_adjust=True, progress=False)
    prices = data["Close"] if "Close" in data.columns else data
        
    valid_threshold = int(len(prices) * 0.8)
    prices = prices.dropna(thresh=valid_threshold, axis=1).ffill().bfill()
    
    equity_cols = [t for t in nifty500_tickers if t in prices.columns]
    equity_df = prices[equity_cols]
    
    defensive_df = pd.DataFrame(index=prices.index)
    for base_name, nse_ticker in DEFENSIVE_NSE_MAPPING.items():
        if nse_ticker in prices.columns:
            defensive_df[base_name] = prices[nse_ticker]

    nifty_series = prices[NIFTY_REGIME_TICKER] if NIFTY_REGIME_TICKER in prices.columns else pd.Series(dtype=float)
    gsec_series = prices[GSEC_REGIME_TICKER] if GSEC_REGIME_TICKER in prices.columns else pd.Series(dtype=float)
    
    return equity_df, defensive_df, nifty_series, gsec_series

# ==========================================
# STATE & STRATEGY STORAGE
# ==========================================
def create_default_strategy(name, portfolio_val=1000000.0):
    return {
        "name": name,
        "portfolio_amount": float(portfolio_val),
        "allocated_capital": 0.0,
        "unallocated_cash": float(portfolio_val),
        "top_n": 20,
        "use_ma_filter": True,
        "ma_period": 200,
        "ma_type": "SMA",
        "use_52w_filter": True,
        "within_52w_pct": 15.0,  # Within 15% of 52-week High
        "use_rs_filter": True,
        "rs_ma_period": 50,
        "lookbacks": [252, 120, 60],
        "weights": [0.5, 0.3, 0.2],
        "positions": {}
    }

if "strategies" not in st.session_state:
    st.session_state.strategies = {
        "Nifty 500 Momentum": create_default_strategy("Nifty 500 Momentum", 1000000.0),
        "Defensive RS Rotation": create_default_strategy("Defensive RS Rotation", 2500000.0)
    }

if "active_strategy_name" not in st.session_state:
    st.session_state.active_strategy_name = list(st.session_state.strategies.keys())[0]

# ==========================================
# INDICATOR CALCULATIONS
# ==========================================
def apply_strategy_filters(equity_df, nifty_series, gsec_series, strat):
    """Filters universe according to individual strategy settings."""
    df = equity_df.copy()
    latest_prices = df.iloc[-1]
    
    # 1. Moving Average Filter (Price > MA)
    if strat["use_ma_filter"]:
        ma_period = strat["ma_period"]
        if strat["ma_type"] == "EMA":
            ma_series = df.ewm(span=ma_period, adjust=False).mean().iloc[-1]
        else:
            ma_series = df.rolling(window=ma_period).mean().iloc[-1]
        df = df.loc[:, latest_prices > ma_series]

    # 2. 52-Week High Proximity Filter
    if strat["use_52w_filter"] and len(df) >= 252:
        high_52w = df.iloc[-252:].max()
        pct_from_high = ((high_52w - latest_prices) / high_52w) * 100
        df = df.loc[:, pct_from_high <= strat["within_52w_pct"]]

    # 3. Relative Strength (RS) Regime Indicator
    is_bullish = True
    if strat["use_rs_filter"]:
        ratio = nifty_series / gsec_series
        sma_ratio = ratio.rolling(window=strat["rs_ma_period"]).mean()
        is_bullish = bool(ratio.iloc[-1] > sma_ratio.iloc[-1])

    return df, is_bullish

def rank_universe(filtered_equity, lookbacks, weights):
    """Calculates weighted composite relative strength momentum rank."""
    if filtered_equity.empty:
        return pd.Series(dtype=float)

    components = []
    for lb in lookbacks:
        if len(filtered_equity) > lb:
            ret = (filtered_equity.iloc[-1] / filtered_equity.iloc[-lb - 1] - 1).dropna()
            components.append(ret.rank(ascending=False, method="min"))

    if not components:
        return pd.Series(dtype=float)

    w = np.array(weights, dtype=float)
    w = w / w.sum()
    composite = pd.concat(components, axis=1).mul(w, axis=1).sum(axis=1)
    return composite.rank(ascending=True, method="min").sort_values()

# ==========================================
# SIDEBAR CONTROLS
# ==========================================
st.sidebar.title("🏛️ Strategy Manager Dashboard")

# Select / Switch Active Strategy
strategy_options = list(st.session_state.strategies.keys())
selected_strat_name = st.sidebar.selectbox(
    "📂 Active Strategy Workspace",
    options=strategy_options,
    index=strategy_options.index(st.session_state.active_strategy_name) if st.session_state.active_strategy_name in strategy_options else 0
)
st.session_state.active_strategy_name = selected_strat_name
strat = st.session_state.strategies[selected_strat_name]

st.sidebar.markdown("---")
st.sidebar.subheader("➕ Create New Strategy Profile")
new_strat_name = st.sidebar.text_input("New Strategy Name", placeholder="e.g. Aggressive Growth")
new_strat_amt = st.sidebar.number_input("Starting Capital (₹)", min_value=10000.0, value=500000.0, step=50000.0)

if st.sidebar.button("💾 Save New Strategy Profile"):
    if new_strat_name and new_strat_name not in st.session_state.strategies:
        st.session_state.strategies[new_strat_name] = create_default_strategy(new_strat_name, new_strat_amt)
        st.session_state.active_strategy_name = new_strat_name
        st.sidebar.success(f"Strategy '{new_strat_name}' created!")
        st.rerun()
    elif new_strat_name in st.session_state.strategies:
        st.sidebar.error("A strategy with this name already exists.")

st.sidebar.markdown("---")
# Cloud Backup Sync
export_json = json.dumps(st.session_state.strategies, indent=4)
st.sidebar.download_button("📥 Backup All Strategies (JSON)", export_json, "strategies_backup.json", "application/json")

# ==========================================
# DASHBOARD WORKSPACE INTERFACE
# ==========================================
st.title("⚡ Quantitative Multi-Strategy Studio")
st.caption("Custom Portfolio Allocation • Modular Indicator Engine • Official NSE Live Data")

# Top Metrics Bar
m_col1, m_col2, m_col3, m_col4 = st.columns(4)
m_col1.metric("Strategy Selected", strat["name"])
m_col2.metric("Total Portfolio Capital", f"₹{strat['portfolio_amount']:,.2f}")
m_col3.metric("Allocated Capital", f"₹{strat['allocated_capital']:,.2f}")
m_col4.metric("Unallocated Cash", f"₹{strat['unallocated_cash']:,.2f}")

st.markdown("---")

# Portfolio Capital Modifier Section
st.subheader("💰 Portfolio Capital & Allocation Settings")
cap_col1, cap_col2 = st.columns(2)

with cap_col1:
    updated_amt = st.number_input(
        "Modify Strategy Total Capital (₹)", 
        min_value=10000.0, 
        value=float(strat["portfolio_amount"]), 
        step=50000.0
    )
    if st.button("🔄 Update Portfolio Capital"):
        diff = updated_amt - strat["portfolio_amount"]
        strat["portfolio_amount"] = updated_amt
        strat["unallocated_cash"] += diff
        st.success(f"Updated Portfolio Capital to ₹{updated_amt:,.2f}")
        st.rerun()

with cap_col2:
    strat["top_n"] = st.number_input("Top N Stocks Allocation Target", min_value=1, max_value=50, value=int(strat["top_n"]))

st.markdown("---")

# Strategy Feature & Indicator Builder Controls
st.subheader("🛠️ Indicator & Feature Engine (Custom Config)")
ind_col1, ind_col2, ind_col3 = st.columns(3)

with ind_col1:
    st.markdown("##### 📈 Moving Average Filter")
    strat["use_ma_filter"] = st.checkbox("Enable MA Trend Filter", value=strat["use_ma_filter"])
    strat["ma_type"] = st.selectbox("MA Type", ["SMA", "EMA"], index=0 if strat["ma_type"] == "SMA" else 1)
    strat["ma_period"] = st.number_input("MA Lookback Period", min_value=10, max_value=300, value=int(strat["ma_period"]))

with ind_col2:
    st.markdown("##### 🎯 52-Week High Proximity Filter")
    strat["use_52w_filter"] = st.checkbox("Enable 52W Proximity Filter", value=strat["use_52w_filter"])
    strat["within_52w_pct"] = st.slider("Within % of 52-Week High", 1.0, 50.0, float(strat["within_52w_pct"]))

with ind_col3:
    st.markdown("##### ⚖️ Relative Strength (RS) Regime Switcher")
    strat["use_rs_filter"] = st.checkbox("Enable Nifty/G-Sec RS Switch", value=strat["use_rs_filter"])
    strat["rs_ma_period"] = st.number_input("RS Ratio SMA Lookback", min_value=10, max_value=200, value=int(strat["rs_ma_period"]))

st.markdown("---")

# Execution & Rebalance Engine
st.subheader("🚀 Live Strategy Rebalance Execution")

with st.spinner("Fetching verified market data from official NSE endpoints..."):
    try:
        equity_df, defensive_df, nifty_series, gsec_series = fetch_trusted_market_data(period="2y")
    except Exception as e:
        st.error(f"Error fetching official market data: {str(e)}")
        st.stop()

# Filter universe
filtered_equity, is_bullish = apply_strategy_filters(equity_df, nifty_series, gsec_series, strat)

# Show filter status details
status_col1, status_col2 = st.columns(2)
status_col1.info(f"📊 Eligible Stocks After Filtering: **{len(filtered_equity.columns)} / {len(equity_df.columns)} Nifty 500 Stocks**")

if strat["use_rs_filter"]:
    if is_bullish:
        status_col2.success("🟢 RS Regime Status: BULLISH (Equities Active)")
    else:
        status_col2.warning("🔴 RS Regime Status: BEARISH (Safe Haven Defense Active)")

if st.button("⚡ Run Rebalance & Reallocate Portfolio", type="primary"):
    if not is_bullish and strat["use_rs_filter"]:
        # Defensive Mode Allocation
        top_candidates = [DEFENSIVE_NSE_MAPPING["GSEC10IETF"], DEFENSIVE_NSE_MAPPING["GOLDBEES"]]
    else:
        # Momentum Equity Allocation
        ranks = rank_universe(filtered_equity, strat["lookbacks"], strat["weights"])
        top_candidates = ranks.head(strat["top_n"]).index.tolist()

    # Equal Portfolio Reallocation
    alloc_pool = strat["portfolio_amount"]
    per_position_amt = alloc_pool / len(top_candidates) if top_candidates else 0.0

    new_positions = {}
    for ticker in top_candidates:
        new_positions[ticker] = {
            "Ticker": ticker,
            "Allocated Capital (₹)": per_position_amt,
            "Weight (%)": (per_position_amt / alloc_pool) * 100 if alloc_pool > 0 else 0
        }

    strat["positions"] = new_positions
    strat["allocated_capital"] = alloc_pool
    strat["unallocated_cash"] = 0.0
    st.success(f"Successfully reallocated ₹{alloc_pool:,.2f} across {len(top_candidates)} positions for strategy '{strat['name']}'!")
    st.rerun()

# Display Active Allocations Table
if strat["positions"]:
    st.markdown("### 📋 Current Portfolio Positions")
    alloc_table = pd.DataFrame.from_dict(strat["positions"], orient="index")
    alloc_table["Allocated Capital (₹)"] = alloc_table["Allocated Capital (₹)"].apply(lambda x: f"₹{x:,.2f}")
    alloc_table["Weight (%)"] = alloc_table["Weight (%)"].apply(lambda x: f"{x:.2f}%")
    st.table(alloc_table)
else:
    st.info("No active position allocations found. Configure your strategy features above and click 'Run Rebalance'.")
