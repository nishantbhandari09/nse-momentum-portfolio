import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import requests
from io import StringIO
from datetime import datetime, timedelta

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(page_title="Momentum Investing Strategy Engine", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #F4F7FE; }
    .stMetric { background-color: #FFFFFF; padding: 15px; border-radius: 10px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
    .status-active { color: #00875A; font-weight: bold; background-color: #E3FCEF; padding: 2px 8px; border-radius: 4px; }
    .status-paused { color: #DE350B; font-weight: bold; background-color: #FFEBE6; padding: 2px 8px; border-radius: 4px; }
    </style>
""", unsafe_allow_html=True)

DEFENSIVE_NSE_MAPPING = {
    "GOLDBEES": "GOLDBEES.NS",
    "LIQUIDCASE": "LIQUIDCASE.NS",
    "GSEC10IETF": "SETFGSEC.NS"
}
NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

# ==========================================
# DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_nifty500_tickers():
    official_nse_url = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
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

    return ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

@st.cache_data(ttl=3600)
def fetch_market_data(tickers, period="1y"):
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

if "editing_strategy_name" not in st.session_state:
    st.session_state.editing_strategy_name = None

if "strategies" not in st.session_state:
    st.session_state.strategies = {
        "NIP": {
            "created_date": (datetime.now() - timedelta(days=43)).strftime("%Y-%m-%d"),
            "status": "Active",
            "type": "Real",
            "allocated": 2000000.0,
            "balance": 2000000.0,
            "realized_pnl": 15000.0,
            "rebalance_date": 1,
            "rebalance_days_left": 12,
            "no_of_stocks": 10,
            "period_days": [252],
            "moving_average": "200 EMA",
            "use_rs": True,
            "positions": [
                {"Symbol": "RELIANCE.NS", "Buy Qty": 15, "Buy Price": 2400.0, "Entry Date": "2026-08-01"}
            ]
        },
        "Mauka largecap": {
            "created_date": (datetime.now() - timedelta(days=38)).strftime("%Y-%m-%d"),
            "status": "Active",
            "type": "Real",
            "allocated": 100000.0,
            "balance": 100000.0,
            "realized_pnl": 0.0,
            "rebalance_date": 1,
            "rebalance_days_left": 12,
            "no_of_stocks": 5,
            "period_days": [120, 60],
            "moving_average": "100 EMA",
            "use_rs": False,
            "positions": []
        },
        "MIP Largecap": {
            "created_date": (datetime.now() - timedelta(days=38)).strftime("%Y-%m-%d"),
            "status": "Paused",
            "type": "Real",
            "allocated": 2000000.0,
            "balance": 2000000.0,
            "realized_pnl": 22836.85,
            "rebalance_date": 20,
            "rebalance_days_left": 1,
            "no_of_stocks": 10,
            "period_days": [252, 120, 90, 60],
            "moving_average": "200 EMA",
            "use_rs": True,
            "positions": [
                {"Symbol": "TCS.NS", "Buy Qty": 10, "Buy Price": 3800.0, "Entry Date": "2026-07-14"}
            ]
        }
    }

# Helper Function: Calculate P&L and Returns for a given strategy
def calculate_strategy_metrics(strat):
    positions = strat["positions"]
    realized_pnl = strat.get("realized_pnl", 0.0)
    unrealized_pnl = 0.0
    current_holding_val = 0.0
    positions_data = []

    if positions and strat["status"] == "Active":
        tickers = [p["Symbol"] for p in positions]
        prices_df = fetch_market_data(tickers, period="1mo")
        for pos in positions:
            sym = pos["Symbol"]
            buy_qty = pos["Buy Qty"]
            buy_price = pos["Buy Price"]
            cmp_price = float(prices_df[sym].iloc[-1]) if (not prices_df.empty and sym in prices_df.columns) else buy_price
            
            curr_val = cmp_price * buy_qty
            pos_unrealized = (cmp_price - buy_price) * buy_qty
            pos_pnl_pct = ((cmp_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0
            
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

    total_pnl = realized_pnl + unrealized_pnl
    allocated = strat["allocated"]
    returns_pct = (total_pnl / allocated * 100) if allocated > 0 else 0.0
    current_total_value = allocated + total_pnl

    return {
        "allocated": allocated,
        "current_holding_val": current_holding_val,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "returns_pct": returns_pct,
        "current_total_value": current_total_value,
        "positions_data": positions_data
    }

# ==========================================
# NAVIGATION HEADER
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
        st.session_state.editing_strategy_name = None
        st.rerun()

st.markdown("---")

# ==========================================
# PAGE 1: PORTFOLIO DASHBOARD & DRILL-DOWN
# ==========================================
if st.session_state.navigation_tab == "DASHBOARD":
    
    # CASE A: STRATEGY SPECIFIC DRILL-DOWN VIEW
    if st.session_state.active_strategy_view is not None:
        strat_name = st.session_state.active_strategy_view
        strat = st.session_state.strategies.get(strat_name)
        
        if not strat:
            st.session_state.active_strategy_view = None
            st.rerun()

        metrics = calculate_strategy_metrics(strat)
        
        top_col1, top_col2, top_col3 = st.columns([2, 5, 3])
        with top_col1:
            if st.button("❮ Back to Dashboard"):
                st.session_state.active_strategy_view = None
                st.rerun()
        with top_col3:
            # Quick Toggle Start/Stop directly inside detail view
            current_status = strat["status"]
            new_status = "Paused" if current_status == "Active" else "Active"
            btn_label = "⏸️ Pause Strategy" if current_status == "Active" else "▶️ Start Strategy"
            if st.button(btn_label):
                strat["status"] = new_status
                st.rerun()

        st.subheader(f"Strategy Specific View: {strat_name}")
        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
        st.markdown(f"Status: <span class='{status_class}'>{strat['status']}</span>", unsafe_allow_html=True)

        # Strategy-Specific Config Summary
        with st.expander("⚙️ View Strategy Specific Indicator Parameters", expanded=False):
            st.write(f"**Lookback Periods:** {', '.join(map(str, strat.get('period_days', [252])))} Days")
            st.write(f"**Moving Average Filter:** {strat.get('moving_average', '200 EMA')}")
            st.write(f"**Relative Strength Filter:** {'Enabled' if strat.get('use_rs') else 'Disabled'}")
            st.write(f"**Target Positions:** {strat.get('no_of_stocks', 10)}")

        # Strategy Specific Realized, Unrealized, Total P&L and Returns
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Allocated Amount", f"₹{metrics['allocated']:,.2f}")
        m2.metric("Realized P&L", f"₹{metrics['realized_pnl']:,.2f}")
        m3.metric("Unrealized P&L", f"₹{metrics['unrealized_pnl']:,.2f}")
        m4.metric("Total Return", f"₹{metrics['total_pnl']:,.2f}", delta=f"{metrics['returns_pct']:.2f}%")

        st.markdown("### Positions Table")
        if metrics["positions_data"]:
            st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
        else:
            st.info("No active scanned holdings for this strategy.")

    # CASE B: MAIN AGGREGATE DASHBOARD
    else:
        st.title("MY PORTFOLIO")
        
        # Calculate Aggregated Portfolio Metrics across ALL Strategies
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

        # Dashboard Top Aggregate Summary
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

        # Render Strategy Cards Grid
        strat_cols = st.columns(3)
        keys = list(st.session_state.strategies.keys())

        for i, name in enumerate(keys):
            strat = st.session_state.strategies[name]
            sm = calculate_strategy_metrics(strat)
            col = strat_cols[i % 3]

            with col:
                with st.container(border=True):
                    c_head1, c_head2 = st.columns([3, 2])
                    with c_head1:
                        st.markdown(f"### **{name}**")
                        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
                        st.markdown(f"<span class='{status_class}'>{strat['status']}</span>", unsafe_allow_html=True)
                    
                    # Quick Actions: Start/Stop, Edit, Delete, View
                    with c_head2:
                        act_col1, act_col2, act_col3 = st.columns(3)
                        
                        # Start / Pause Toggle
                        is_active = strat["status"] == "Active"
                        if act_col1.button("⏸️" if is_active else "▶️", key=f"toggle_{name}", help="Start/Stop Strategy"):
                            strat["status"] = "Paused" if is_active else "Active"
                            st.rerun()

                        # Edit Strategy
                        if act_col2.button("✏️", key=f"edit_{name}", help="Edit Strategy Config"):
                            st.session_state.editing_strategy_name = name
                            st.session_state.navigation_tab = "STRATEGY_BUILDER"
                            st.rerun()

                        # Delete Strategy
                        if act_col3.button("🗑️", key=f"del_{name}", help="Delete Strategy"):
                            del st.session_state.strategies[name]
                            st.rerun()

                    st.markdown("---")
                    st.markdown(f"**Allocated:** ₹{sm['allocated']:,.2f}")
                    st.markdown(f"**Realized P&L:** ₹{sm['realized_pnl']:,.2f}")
                    st.markdown(f"**Unrealized P&L:** ₹{sm['unrealized_pnl']:,.2f}")
                    
                    pnl_color = "green" if sm['total_pnl'] >= 0 else "red"
                    st.markdown(f"**P&L / Returns:** <span style='color:{pnl_color}; font-weight:bold;'>₹{sm['total_pnl']:,.2f} ({sm['returns_pct']:.2f}%)</span>", unsafe_allow_html=True)

                    if st.button("📊 Open Strategy Details", key=f"view_detail_{name}", use_container_width=True):
                        st.session_state.active_strategy_view = name
                        st.rerun()

# ==========================================
# PAGE 2: INVESTING STRATEGY BUILDER / EDIT ENGINE
# ==========================================
elif st.session_state.navigation_tab == "STRATEGY_BUILDER":
    edit_mode = st.session_state.editing_strategy_name is not None
    edit_strat = st.session_state.strategies.get(st.session_state.editing_strategy_name, {}) if edit_mode else {}

    st.subheader("✏️ EDIT STRATEGY ENGINE" if edit_mode else "➕ CREATE STRATEGY ENGINE")
    
    st.markdown("#### Investment Details")
    
    inv_r1_1, inv_r1_2, inv_r1_3 = st.columns([1.5, 3, 3])
    with inv_r1_1:
        st_type = st.radio("Strategy Type :", ["Real", "Virtual"], index=0 if edit_strat.get("type") == "Real" else 1, horizontal=True)
    with inv_r1_2:
        default_name = st.session_state.editing_strategy_name if edit_mode else "New Momentum Strategy"
        strat_name_input = st.text_input("Strategy Name :", value=default_name, disabled=edit_mode)

    inv_r2_1, inv_r2_2 = st.columns(2)
    with inv_r2_1:
        tot_alloc = st.number_input("Total Allocation (₹) :", value=float(edit_strat.get("allocated", 1000000.0)), step=50000.0)
    with inv_r2_2:
        num_stocks = st.number_input("No. of Stocks Target :", value=int(edit_strat.get("no_of_stocks", 10)), min_value=1, max_value=50)

    st.markdown("---")
    st.markdown("#### Strategy Specific Technical Features & Indicators")

    sd_r1_1, sd_r1_2 = st.columns(2)
    with sd_r1_1:
        st.caption("Lookback Period (Days) Selection :")
        existing_periods = edit_strat.get("period_days", [252])
        p252 = st.checkbox("252 Days (1 Year)", value=(252 in existing_periods))
        p120 = st.checkbox("120 Days (6 Months)", value=(120 in existing_periods))
        p90 = st.checkbox("90 Days (3 Months)", value=(90 in existing_periods))
        p60 = st.checkbox("60 Days (2 Months)", value=(60 in existing_periods))
    
    with sd_r1_2:
        ma_options = ["None", "200 EMA", "100 EMA", "200 SMA", "50 SMA"]
        ma_val = edit_strat.get("moving_average", "200 EMA")
        ma_idx = ma_options.index(ma_val) if ma_val in ma_options else 1
        selected_ma = st.selectbox("Moving Average Filter :", ma_options, index=ma_idx)

        use_rs = st.checkbox("Enable Relative Strength Filter (Nifty 500 / G-Sec)", value=edit_strat.get("use_rs", True))

    st.markdown("---")
    save_label = "💾 Update Strategy Config" if edit_mode else "💾 Deploy New Strategy"
    if st.button(save_label, type="primary", use_container_width=True):
        selected_periods = []
        if p252: selected_periods.append(252)
        if p120: selected_periods.append(120)
        if p90: selected_periods.append(90)
        if p60: selected_periods.append(60)

        target_name = st.session_state.editing_strategy_name if edit_mode else strat_name_input

        st.session_state.strategies[target_name] = {
            "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
            "status": edit_strat.get("status", "Active"),
            "type": st_type,
            "allocated": float(tot_alloc),
            "balance": float(tot_alloc),
            "realized_pnl": edit_strat.get("realized_pnl", 0.0),
            "rebalance_date": edit_strat.get("rebalance_date", 1),
            "rebalance_days_left": edit_strat.get("rebalance_days_left", 30),
            "no_of_stocks": num_stocks,
            "period_days": selected_periods,
            "moving_average": selected_ma,
            "use_rs": use_rs,
            "positions": edit_strat.get("positions", [])
        }
        
        st.session_state.editing_strategy_name = None
        st.session_state.navigation_tab = "DASHBOARD"
        st.rerun()
