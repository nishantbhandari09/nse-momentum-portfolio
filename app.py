import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
from io import StringIO
from datetime import datetime

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Quantitative Multi-Portfolio Engine", layout="wide")

# Static Base Definitions for Group 2 Defensive Assets
DEFENSIVE_BASE_UNIVERSE = {
    "GOLDBEES": [1.0],
    "LIQUIDCASE": [1.0, 2.0, 3.0],
    "GSEC10IETF": [1.0, 2.0, 3.0]
}

DEFENSIVE_NSE_MAPPING = {
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "GSEC10IETF": "SETFGSEC.NS"
}

NIFTY_REGIME_TICKER = "^CRSLDX"      # Official Nifty 500 Index Ticker on Yahoo Finance
GSEC_REGIME_TICKER = "SETFGSEC.NS"   # Official Nifty 10yr Benchmark G-Sec ETF

# ==========================================
# TRUSTED DATA SCRAPING & FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_nifty500_tickers():
    """
    Fetches official Nifty 500 constituent list EXCLUSIVELY from verified sources:
    1. Primary: Official NSE India (niftyindices.com)
    2. Secondary: Verified NSE Mirror Repository on GitHub
    """
    official_nse_url = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    })

    # 1. Primary Official Source
    try:
        response = session.get(official_nse_url, timeout=10)
        if response.status_code == 200:
            df = pd.read_csv(StringIO(response.text))
            if "Symbol" in df.columns:
                return [f"{str(sym).strip()}.NS" for sym in df["Symbol"].dropna().unique()]
    except Exception:
        pass

    # 2. Verified Backup Mirror
    github_mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
    try:
        df_mirror = pd.read_csv(github_mirror_url)
        if "Symbol" in df_mirror.columns:
            return [f"{str(sym).strip()}.NS" for sym in df_mirror["Symbol"].dropna().unique()]
    except Exception:
        pass

    raise ValueError("Unable to connect to official NSE data servers. Unverified sources are strictly blocked.")

@st.cache_data(ttl=3600)
def fetch_trusted_market_data(period="1y"):
    """
    Downloads historical close prices directly from Yahoo Finance for official tickers.
    Cleans incomplete, unverified, or non-trading symbols automatically.
    """
    nifty500_tickers = get_trusted_nifty500_tickers()
    defensive_tickers = list(DEFENSIVE_NSE_MAPPING.values())
    all_tickers = list(set(nifty500_tickers + defensive_tickers + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]))
    
    data = yf.download(
        tickers=all_tickers,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False
    )
    
    if isinstance(data.columns, pd.MultiIndex):
        prices = data["Close"] if "Close" in data.columns else data["Adj Close"]
    else:
        prices = data
        
    # Strict Verification: Keep symbols with at least 80% valid trading days
    valid_threshold = int(len(prices) * 0.8)
    prices = prices.dropna(thresh=valid_threshold, axis=1).ffill().bfill()
    
    equity_cols = [t for t in nifty500_tickers if t in prices.columns]
    equity_df = prices[equity_cols]
    
    # Map raw defensive tickers to base names
    defensive_df = pd.DataFrame(index=prices.index)
    for base_name, nse_ticker in DEFENSIVE_NSE_MAPPING.items():
        if nse_ticker in prices.columns:
            defensive_df[base_name] = prices[nse_ticker]

    nifty_series = prices[NIFTY_REGIME_TICKER] if NIFTY_REGIME_TICKER in prices.columns else pd.Series(dtype=float)
    gsec_series = prices[GSEC_REGIME_TICKER] if GSEC_REGIME_TICKER in prices.columns else pd.Series(dtype=float)
    
    return equity_df, defensive_df, nifty_series, gsec_series

# ==========================================
# STATE MANAGEMENT (10 PORTFOLIOS)
# ==========================================
def get_default_portfolios():
    portfolios = {}
    for i in range(1, 11):
        portfolios[f"Portfolio {i}"] = {
            "name": f"Portfolio {i}",
            "initial_capital": 1000000.0,
            "current_cash": 1000000.0,
            "allocated_capital": 0.0,
            "realized_profit": 0.0,
            "top_n_stocks": 20,
            "exit_rank": 40,
            "lookbacks": [252, 120, 60],
            "weights": [0.5, 0.3, 0.2],
            "rs_filter_enabled": True,
            "rs_ma_period": 50,
            "selected_defensives": [
                "GOLDBEES_1X",
                "LIQUIDCASE_1X", "LIQUIDCASE_2X", "LIQUIDCASE_3X",
                "GSEC10IETF_1X", "GSEC10IETF_2X", "GSEC10IETF_3X"
            ],
            "positions": {}
        }
    return portfolios

if "portfolios" not in st.session_state:
    st.session_state.portfolios = get_default_portfolios()

# ==========================================
# REGIME FILTER & RANKING ENGINE
# ==========================================
def calculate_relative_strength_filter(nifty_prices, gsec_index_prices, ma_period=50):
    ratio = nifty_prices / gsec_index_prices
    sma = ratio.rolling(window=ma_period).mean()
    latest_ratio = float(ratio.iloc[-1])
    latest_sma = float(sma.iloc[-1])
    return (latest_ratio > latest_sma), latest_ratio, latest_sma

def rank_assets(equity_prices, defensive_prices, is_bull_market, lookbacks, weights, selected_defensives):
    if is_bull_market:
        combined_prices = equity_prices.copy()
    else:
        combined_prices = pd.DataFrame(index=defensive_prices.index)

    # Inject selected Group 2 defensive multipliers (1X, 2X, 3X)
    for def_symbol, multipliers in DEFENSIVE_BASE_UNIVERSE.items():
        if def_symbol in defensive_prices.columns:
            base_col = defensive_prices[def_symbol]
            for m in multipliers:
                scaled_name = f"{def_symbol}_{int(m)}X"
                if scaled_name in selected_defensives:
                    if m == 1.0:
                        combined_prices[scaled_name] = base_col
                    else:
                        combined_prices[scaled_name] = base_col * (1 + (base_col.pct_change() * m))

    if combined_prices.empty:
        return pd.Series(dtype=float)

    components = []
    for lb in lookbacks:
        if len(combined_prices) > lb:
            ret = (combined_prices.iloc[-1] / combined_prices.iloc[-lb - 1] - 1).dropna()
            components.append(ret.rank(ascending=False, method="min"))

    if not components:
        return pd.Series(dtype=float)

    w = np.array(weights, dtype=float)
    w = w / w.sum()
    composite = pd.concat(components, axis=1).mul(w, axis=1).sum(axis=1)
    return composite.rank(ascending=True, method="min").sort_values()

# ==========================================
# SIDEBAR CONTROLS & STRATEGY SETUP
# ==========================================
st.sidebar.title("🛠️ Multi-Portfolio Manager")

portfolio_keys = list(st.session_state.portfolios.keys())
selected_key = st.sidebar.selectbox("📂 Select Active Portfolio Slot", portfolio_keys)
p = st.session_state.portfolios[selected_key]

st.sidebar.markdown("---")
st.sidebar.subheader("⚙️ Portfolio Settings")
p["name"] = st.sidebar.text_input("Custom Strategy Name", value=p["name"])
p["top_n_stocks"] = st.sidebar.number_input("Top N Target Allocations", min_value=1, max_value=50, value=p["top_n_stocks"])
p["exit_rank"] = st.sidebar.number_input("Exit Buffer Rank Threshold", min_value=5, max_value=100, value=p["exit_rank"])

st.sidebar.markdown("---")
st.sidebar.subheader("📈 Group 1 / Group 2 RS Filter")
p["rs_filter_enabled"] = st.sidebar.checkbox("Enable RS Regime Switcher", value=p["rs_filter_enabled"])
p["rs_ma_period"] = st.sidebar.number_input("RS Ratio SMA Period", min_value=10, max_value=200, value=int(p["rs_ma_period"]))

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ Group 2 Defensive Selection")

all_defensive_options = [
    "GOLDBEES_1X",
    "LIQUIDCASE_1X", "LIQUIDCASE_2X", "LIQUIDCASE_3X",
    "GSEC10IETF_1X", "GSEC10IETF_2X", "GSEC10IETF_3X"
]

user_def_selection = []
for item in all_defensive_options:
    if st.sidebar.checkbox(item, value=(item in p["selected_defensives"])):
        user_def_selection.append(item)
p["selected_defensives"] = user_def_selection

# Cloud Backup & Sync Controls
st.sidebar.markdown("---")
st.sidebar.subheader("💾 Cloud Backup & Sync")
export_data = json.dumps(st.session_state.portfolios, indent=4)
st.sidebar.download_button(
    label="📥 Download Portfolios Backup (JSON)",
    data=export_data,
    file_name="portfolios_backup.json",
    mime="application/json"
)

uploaded_file = st.sidebar.file_uploader("📤 Restore Portfolios Backup", type=["json"])
if uploaded_file is not None:
    try:
        st.session_state.portfolios = json.load(uploaded_file)
        st.sidebar.success("Portfolios restored successfully!")
        st.rerun()
    except Exception:
        st.sidebar.error("Invalid JSON file uploaded.")

# ==========================================
# MAIN DASHBOARD INTERFACE
# ==========================================
st.title(f"📊 {p['name']}")
st.caption("Streamlit Cloud Ready • Trusted NSE/Yahoo Data • Multi-Portfolio Strategy Engine")

# Capital Allocation Ledger Metrics
col1, col2, col3, col4 = st.columns(4)
total_portfolio_val = p["current_cash"] + p["allocated_capital"] + p["realized_profit"]
col1.metric("Total Portfolio Equity", f"₹{total_portfolio_val:,.2f}")
col2.metric("Unallocated Cash", f"₹{p['current_cash']:,.2f}")
col3.metric("Currently Allocated Capital", f"₹{p['allocated_capital']:,.2f}")
col4.metric("Realized Profits Booked", f"₹{p['realized_profit']:,.2f}")

st.markdown("---")
st.subheader("💵 Capital Management (Deposit & Withdraw)")
cap_col1, cap_col2 = st.columns(2)

with cap_col1:
    add_amt = st.number_input("Add Capital (₹)", min_value=0.0, step=10000.0, key="add_cap")
    if st.button("➕ Deposit Capital"):
        p["current_cash"] += add_amt
        p["initial_capital"] += add_amt
        st.success(f"Deposited ₹{add_amt:,.2f} into unallocated cash.")
        st.rerun()

with cap_col2:
    withdraw_amt = st.number_input("Withdraw Capital (₹)", min_value=0.0, step=10000.0, key="wd_cap")
    if st.button("➖ Withdraw Capital"):
        if withdraw_amt <= p["current_cash"]:
            p["current_cash"] -= withdraw_amt
            p["initial_capital"] -= withdraw_amt
            st.success(f"Withdrew ₹{withdraw_amt:,.2f} from cash.")
            st.rerun()
        else:
            st.error("Withdrawal amount exceeds available unallocated cash!")

# Rebalance & Live Market Data Engine
st.markdown("---")
st.subheader("⚡ Live Rebalance & Equal Reallocation Engine")

with st.spinner("Fetching verified market data from official sources..."):
    try:
        equity_df, defensive_df, nifty_index, gsec_index = fetch_trusted_market_data(period="1y")
        st.sidebar.info(f"🟢 Active Stock Universe: **{len(equity_df.columns)} Nifty 500 Stocks** Loaded")
    except Exception as e:
        st.error(f"Market Data Error: {str(e)}")
        st.stop()

# Evaluate Relative Strength Regime Filter
is_bullish, ratio_val, sma_val = calculate_relative_strength_filter(
    nifty_index, gsec_index, ma_period=int(p["rs_ma_period"])
)

rs_col1, rs_col2 = st.columns([1, 2])
with rs_col1:
    if is_bullish or not p["rs_filter_enabled"]:
        st.success("🟢 REGIME: BULLISH (Group 1 Equities Active)")
    else:
        st.warning("🔴 REGIME: BEARISH (Group 2 Defensive Assets Only)")

with rs_col2:
    st.info(f"Nifty/GSec RS Ratio: **{ratio_val:.4f}** | {p['rs_ma_period']}-SMA Target: **{sma_val:.4f}**")

# Rebalance Trigger Button
if st.button("🚀 Execute Rebalance & Equal Reallocation", type="primary"):
    ranks = rank_assets(
        equity_prices=equity_df,
        defensive_prices=defensive_df,
        is_bull_market=(is_bullish or not p["rs_filter_enabled"]),
        lookbacks=p["lookbacks"],
        weights=p["weights"],
        selected_defensives=p["selected_defensives"]
    )

    top_candidates = ranks.head(p["top_n_stocks"]).index.tolist()
    
    # Capital Reallocation: Combine Cash + Existing Capital & Divide Equally
    total_reallocate_pool = p["current_cash"] + p["allocated_capital"]
    per_position_amount = total_reallocate_pool / len(top_candidates) if top_candidates else 0.0
    
    new_positions = {}
    for sym in top_candidates:
        clean_name = sym.split("_")[0]
        real_nse_symbol = DEFENSIVE_NSE_MAPPING.get(clean_name, sym)
        new_positions[sym] = {
            "Allocated Capital (₹)": per_position_amount,
            "Execution Ticker": real_nse_symbol
        }

    p["positions"] = new_positions
    p["allocated_capital"] = total_reallocate_pool
    p["current_cash"] = 0.0
    
    st.success(f"Rebalance complete! Reallocated ₹{total_reallocate_pool:,.2f} equally across {len(top_candidates)} assets.")
    st.rerun()

# Allocation Table Display
if p["positions"]:
    st.markdown("### 📋 Active Portfolio Positions")
    alloc_df = pd.DataFrame.from_dict(p["positions"], orient="index")
    alloc_df["Allocated Capital (₹)"] = alloc_df["Allocated Capital (₹)"].apply(lambda x: f"₹{x:,.2f}")
    st.table(alloc_df)
else:
    st.info("No active position allocations found. Click the Rebalance button above to compute allocations.")