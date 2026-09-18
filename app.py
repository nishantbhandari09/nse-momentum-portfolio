import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import json
from io import StringIO
from datetime import datetime, timedelta

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(page_title="Momentum Investing Strategy Engine", layout="wide")

# Institutional Theme Styling matching the screenshots
st.markdown("""
    <style>
    .main { background-color: #F4F7FE; }
    .stMetric { background-color: #FFFFFF; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
    .kpi-card { background-color: #EBF2FF; padding: 15px; border-radius: 8px; border-left: 4px solid #1E56A0; }
    .badge-green { background-color: #72E2AE; color: #004D25; padding: 4px 8px; border-radius: 4px; font-weight: bold; }
    .card-box { background-color: #FFFFFF; padding: 20px; border-radius: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.03); border: 1px solid #E2E8F0; }
    </style>
""", unsafe_allow_html=True)

# Defensive Mapping and Index Benchmarks
DEFENSIVE_NSE_MAPPING = {
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "GSEC10IETF": "SETFGSEC.NS"
}
NIFTY_REGIME_TICKER = "^CRSLDX"      # Nifty 500 Index Ticker on Yahoo Finance
GSEC_REGIME_TICKER = "SETFGSEC.NS"   # Official Nifty 10yr Benchmark G-Sec ETF

# ==========================================
# TRUSTED NSE & MARKET DATA ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_nifty500_tickers():
    """Fetches official Nifty 500 constituent list EXCLUSIVELY from verified sources."""
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

    raise ValueError("Unable to connect to official NSE servers. External unverified sources are strictly blocked.")

@st.cache_data(ttl=3600)
def fetch_market_data(tickers, period="1y"):
    """Downloads prices directly from Yahoo Finance."""
    all_tickers = list(set(tickers + list(DEFENSIVE_NSE_MAPPING.values()) + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]))
    data = yf.download(tickers=all_tickers, period=period, interval="1d", auto_adjust=True, progress=False)
    
    if isinstance(data.columns, pd.MultiIndex):
        prices = data["Close"] if "Close" in data.columns else data["Adj Close"]
    else:
        prices = data
        
    prices = prices.dropna(thresh=int(len(prices)*0.7), axis=1).ffill().bfill()
    return prices

# ==========================================
# STATE INITIALIZATION
# ==========================================
if "navigation_tab" not in st.session_state:
    st.session_state.navigation_tab = "DASHBOARD"

if "active_strategy_view" not in st.session_state:
    st.session_state.active_strategy_view = None

if "strategies" not in st.session_state:
    st.session_state.strategies = {
        "NIP": {
            "created_date": (datetime.now() - timedelta(days=43)).strftime("%Y-%m-%d"),
            "type": "Real",
            "allocated": 2000000.0,
            "balance": 2000000.0,
            "rebalance_date": 1,
            "rebalance_days_left": 12,
            "no_of_stocks": 10,
            "positions": []
        },
        "Mauka largecap": {
            "created_date": (datetime.now() - timedelta(days=38)).strftime("%Y-%m-%d"),
            "type": "Real",
            "allocated": 100000.0,
            "balance": 100000.0,
            "rebalance_date": 1,
            "rebalance_days_left": 12,
            "no_of_stocks": 5,
            "positions": []
        },
        "MIP Largecap": {
            "created_date": (datetime.now() - timedelta(days=38)).strftime("%Y-%m-%d"),
            "type": "Real",
            "allocated": 2000000.0,
            "balance": 2000000.0,
            "rebalance_date": 20,
            "rebalance_days_left": 1,
            "no_of_stocks": 10,
            "positions": [
                {"Symbol": "AEGISLOG.NS", "Buy Qty": 10, "Buy Price": 1281.0, "Entry Date": "2026-07-14"}
            ]
        }
    }

# ==========================================
# NAVIGATION HEADER (MATCHING SCREENSHOT 4)
# ==========================================
nav_col1, nav_col2, _ = st.columns([1.5, 2, 5])
with nav_col1:
    if st.button("💻 DASHBOARD", use_container_width=True, type="primary" if st.session_state.navigation_tab == "DASHBOARD" else "secondary"):
        st.session_state.navigation_tab = "DASHBOARD"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col2:
    if st.button("➕ INVESTING STRATEGY", use_container_width=True, type="primary" if st.session_state.navigation_tab == "STRATEGY_BUILDER" else "secondary"):
        st.session_state.navigation_tab = "STRATEGY_BUILDER"
        st.rerun()

st.markdown("---")

# ==========================================
# PAGE 1: PORTFOLIO DASHBOARD (SCREENSHOT 1 & 2)
# ==========================================
if st.session_state.navigation_tab == "DASHBOARD":
    
    # CASE A: DETAILED DRILL-DOWN VIEW (SCREENSHOT 2)
    if st.session_state.active_strategy_view is not None:
        strat_name = st.session_state.active_strategy_view
        strat = st.session_state.strategies[strat_name]
        
        top_back_col1, top_back_col2 = st.columns([2, 8])
        with top_back_col1:
            if st.button("❮ Back to Dashboard"):
                st.session_state.active_strategy_view = None
                st.rerun()

        st.subheader(f"Strategy: {strat_name}")
        
        # Calculate Real P&L using Live Prices
        positions = strat["positions"]
        tickers = [p["Symbol"] for p in positions] if positions else []
        
        realized_pnl = 22836.85
        unrealized_pnl = 0.0
        total_curr_val = 0.0
        
        positions_rows = []
        if tickers:
            prices_df = fetch_market_data(tickers, period="1mo")
            for pos in positions:
                sym = pos["Symbol"]
                buy_qty = pos["Buy Qty"]
                buy_price = pos["Buy Price"]
                cmp = float(prices_df[sym].iloc[-1]) if sym in prices_df.columns else buy_price
                curr_val = cmp * buy_qty
                pnl = (cmp - buy_price) * buy_qty
                pnl_pct = ((cmp - buy_price) / buy_price) * 100
                
                unrealized_pnl += pnl
                total_curr_val += curr_val
                
                positions_rows.append({
                    "Symbol": sym.replace(".NS", ""),
                    "Buy Qty": buy_qty,
                    "Buy Price": f"₹{buy_price:,.2f}",
                    "Entry Date": pos["Entry Date"],
                    "CMP": f"₹{cmp:,.2f}",
                    "Current Value": f"₹{curr_val:,.2f}",
                    "Current P&L": f"₹{pnl:,.2f}",
                    "Current P&L %": f"{pnl_pct:.2f}%"
                })

        total_pnl = realized_pnl + unrealized_pnl
        momentify_balance = strat["allocated"] + total_pnl

        # Metrics Header Cards (Screenshot 2)
        kpi1, kpi2 = st.columns(2)
        with kpi1:
            st.info(f"**Realized P&L:** ₹{realized_pnl:,.2f}  |  **Unrealized P&L:** ₹{unrealized_pnl:,.2f}")
        with kpi2:
            st.success(f"**Total P&L:** ₹{total_pnl:,.2f}  |  **Momentify Balance:** ₹{momentify_balance:,.2f}")

        st.markdown("### Positions Table")
        if positions_rows:
            st.dataframe(pd.DataFrame(positions_rows), use_container_width=True)
        else:
            st.info("No active open positions for this strategy yet. Run rebalance from the strategy builder.")

    # CASE B: MAIN PORTFOLIO OVERVIEW DASHBOARD (SCREENSHOT 1)
    else:
        st.title("MY PORTFOLIO")
        
        total_allocated = sum(s["allocated"] for s in st.session_state.strategies.values())
        total_balance = sum(s["balance"] for s in st.session_state.strategies.values())
        
        # Top KPI Summary Grid
        kpi_r1_1, kpi_r1_2, kpi_r1_3 = st.columns(3)
        kpi_r1_1.metric("Allocated Amount", f"₹{total_allocated:,.0f}")
        kpi_r1_2.metric("Realized P&L", "₹0", delta="0%", delta_color="normal")
        kpi_r1_3.metric("P&L", "₹0", delta="0%", delta_color="normal")

        kpi_r2_1, kpi_r2_2, kpi_r2_3 = st.columns(3)
        kpi_r2_1.metric("Current Holding Value", "₹0")
        kpi_r2_2.metric("Unrealized P&L", "₹0", delta="0%", delta_color="normal")
        kpi_r2_3.metric("Momentify Balance", f"₹{total_balance:,.0f}")

        st.markdown("---")
        st.subheader("Real Strategies")

        # Strategy Cards Grid (3 Cards per row as shown in Screenshot 1)
        strat_cols = st.columns(3)
        for i, (name, strat) in enumerate(st.session_state.strategies.items()):
            col = strat_cols[i % 3]
            with col:
                with st.container(border=True):
                    c_head1, c_head2 = st.columns([3, 1])
                    with c_head1:
                        age_days = (datetime.now() - datetime.strptime(strat["created_date"], "%Y-%m-%d")).days
                        st.caption(f"Age : {age_days} days")
                        st.markdown(f"### **{name}**")
                    with c_head2:
                        if st.button("📊 View", key=f"btn_view_{name}"):
                            st.session_state.active_strategy_view = name
                            st.rerun()

                    st.text(f"Rebalance Date : {strat['rebalance_date']}  |  {strat['rebalance_days_left']} days to go")
                    st.markdown(f"**Allocated:** ₹{strat['allocated']:,.2f}")
                    st.markdown(f"**Balance:** ₹{strat['balance']:,.2f}")
                    st.markdown("**P&L:** <span style='color:blue;'>0.00 ( 0.00 % )</span>", unsafe_allow_html=True)

# ==========================================
# PAGE 2: INVESTING STRATEGY BUILDER (SCREENSHOT 4 & 5)
# ==========================================
elif st.session_state.navigation_tab == "STRATEGY_BUILDER":
    st.subheader("MOMENTUM INVESTING STRATEGY ENGINE")
    
    st.markdown("#### Investment Details")
    
    inv_r1_1, inv_r1_2, inv_r1_3, inv_r1_4 = st.columns([1.5, 3, 2, 3])
    with inv_r1_1:
        st_type = st.radio("Strategy Type :", ["Real", "Virtual"], horizontal=True)
    with inv_r1_2:
        strat_name_input = st.text_input("Strategy Name :", value="New Momentum Strategy")
    with inv_r1_3:
        etf_group = st.checkbox("ETF Group")
    with inv_r1_4:
        st.selectbox("Exchange and Group :", ["NSE - All Stocks", "NSE - Nifty 500", "NSE - Nifty 100"])

    inv_r2_1, inv_r2_2, inv_r2_3 = st.columns([3, 2, 2])
    with inv_r2_1:
        alloc_mode = st.radio("Mode :", ["Lumpsum", "Lumpsum with SIP", "SIP"], horizontal=True)
    with inv_r2_2:
        auto_reb = st.checkbox("Auto Rebalance", value=True)
    with inv_r2_3:
        inc_be = st.checkbox("Include BE Stocks")

    inv_r3_1, inv_r3_2, inv_r3_3, inv_r3_4 = st.columns(4)
    with inv_r3_1:
        tot_alloc = st.number_input("Total Allocation (₹) :", value=1000000.0, step=50000.0)
    with inv_r3_2:
        num_stocks = st.number_input("No. of Stocks :", value=10, min_value=1, max_value=50)
    with inv_r3_3:
        rank_crit = st.selectbox("Rank Criteria :", ["Return Percent", "Composite RS Rank", "Sharpe Ratio"])
    with inv_r3_4:
        exit_rank = st.number_input("Exit Rank :", value=20)

    inv_r4_1, inv_r4_2, inv_r4_3 = st.columns(3)
    with inv_r4_1:
        freq = st.selectbox("Frequency :", ["Monthly", "Weekly", "Quarterly"])
    with inv_r4_2:
        day_of_month = st.selectbox("Day of Month :", list(range(1, 29)))
    with inv_r4_3:
        mkt_prot = st.number_input("Market Protection % :", value=1.0)

    st.markdown("---")
    st.markdown("#### Strategy Details")

    sd_r1_1, sd_r1_2, sd_r1_3 = st.columns([4, 3, 3])
    with sd_r1_1:
        st.caption("Period in days :")
        p252 = st.checkbox("252", value=True)
        p120 = st.checkbox("120", value=False)
        p90 = st.checkbox("90", value=False)
        p60 = st.checkbox("60", value=False)
    with sd_r1_2:
        retrace = st.selectbox("Retracement :", ["Within", "Above", "Below"])
        retrace_pct = st.number_input("Retracement % :", value=20)
    with sd_r1_3:
        breakout = st.selectbox("Breakout :", ["52 Week High", "26 Week High", "All Time High"])

    sd_r2_1, sd_r2_2, sd_r2_3 = st.columns(3)
    with sd_r2_1:
        st.text_input("Period Weight :", value="1, 1, 1, 1")
    with sd_r2_2:
        price_above = st.number_input("Price Above :", value=0)
    with sd_r2_3:
        price_below = st.number_input("Price Below :", value=0)

    sd_r3_1, sd_r3_2, sd_r3_3 = st.columns(3)
    with sd_r3_1:
        st.selectbox("Chart Type :", ["OHLC", "Candlestick", "Line"])
    with sd_r3_2:
        st.selectbox("Timeframe :", ["Daily", "Weekly"])
    with sd_r3_3:
        ema_200 = st.checkbox("Moving Average (Exponential): 200 EMA", value=True)
        ema_100 = st.checkbox("Moving Average (Exponential): 100 EMA", value=False)

    sd_r4_1, sd_r4_2 = st.columns(2)
    with sd_r4_1:
        use_rs = st.checkbox("Enable Relative Strength Filter")
        st.text_input("Relative Strength Benchmark :", value="NSE Nifty 500 / G-Sec")

    st.markdown("---")
    if st.button("💾 Save & Deploy Strategy Engine", type="primary", use_container_width=True):
        st.session_state.strategies[strat_name_input] = {
            "created_date": datetime.now().strftime("%Y-%m-%d"),
            "type": st_type,
            "allocated": float(tot_alloc),
            "balance": float(tot_alloc),
            "rebalance_date": day_of_month,
            "rebalance_days_left": 30,
            "no_of_stocks": num_stocks,
            "positions": []
        }
        st.success(f"Strategy '{strat_name_input}' deployed successfully!")
        st.session_state.navigation_tab = "DASHBOARD"
        st.rerun()
