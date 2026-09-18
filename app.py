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
    "LiquidBEES": ["LIQUIDCASE.NS"],
    "Gov Bond": ["SETFGSEC.NS"],
    "All ETFs": ["GOLDBEES.NS", "LIQUIDCASE.NS", "SETFGSEC.NS", "NIFTYBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS"]
}

NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

# ==========================================
# DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_tickers_by_group(groups):
    tickers = []
    
    if "Nifty 500" in groups or "Nifty 200" in groups:
        official_nse_url = "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv"
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})
        try:
            response = session.get(official_nse_url, timeout=10)
            if response.status_code == 200:
                df = pd.read_csv(StringIO(response.text))
                if "Symbol" in df.columns:
                    n500 = [f"{str(sym).strip()}.NS" for sym in df["Symbol"].dropna().unique()]
                    if "Nifty 200" in groups and "Nifty 500" not in groups:
                        tickers.extend(n500[:200])
                    else:
                        tickers.extend(n500)
        except Exception:
            github_mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
            try:
                df_mirror = pd.read_csv(github_mirror_url)
                if "Symbol" in df_mirror.columns:
                    n500 = [f"{str(sym).strip()}.NS" for sym in df_mirror["Symbol"].dropna().unique()]
                    if "Nifty 200" in groups and "Nifty 500" not in groups:
                        tickers.extend(n500[:200])
                    else:
                        tickers.extend(n500)
            except Exception:
                tickers.extend(["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "LT.NS", "SBIN.NS"])

    for grp in groups:
        if grp in ASSET_GROUPS_MAPPING:
            tickers.extend(ASSET_GROUPS_MAPPING[grp])

    if not tickers:
        tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

    return list(set(tickers))

@st.cache_data(ttl=3600)
def fetch_market_data(tickers, period="5y"):
    all_tickers = list(set(tickers + ASSET_GROUPS_MAPPING["All ETFs"] + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]))
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
        
        for pos in positions:
            sym = pos["Symbol"]
            buy_qty = pos["Buy Qty"]
            buy_price = pos["Buy Price"]
            
            # Fetch latest adjusted close
            cmp_price = float(prices_df[sym].dropna().iloc[-1]) if (not prices_df.empty and sym in prices_df.columns) else buy_price
            
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

    # Account for remaining uninvested cash buffer
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

def run_strategy_stock_scanner(strat, price_subset=None):
    selected_groups = strat.get("groups", ["Nifty 500"])
    
    if price_subset is None:
        group_tickers = get_trusted_tickers_by_group(selected_groups)
        prices_df = fetch_market_data(group_tickers, period="2y")
    else:
        prices_df = price_subset

    if prices_df.empty:
        return []

    latest_prices = prices_df.iloc[-1]
    filtered_df = prices_df.copy()

    # Moving Average Filter
    ma_config = strat.get("moving_average", "200 EMA")
    if ma_config != "None":
        period = 200 if "200" in ma_config else (100 if "100" in ma_config else 50)
        is_ema = "EMA" in ma_config
        if is_ema:
            ma_vals = prices_df.ewm(span=period, adjust=False).mean().iloc[-1]
        else:
            ma_vals = prices_df.rolling(window=period).mean().iloc[-1]
        filtered_df = filtered_df.loc[:, latest_prices > ma_vals]

    # Period High/Low Proximity Filter
    pct_high = strat.get("pct_from_high", 15.0)
    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = ((period_high - latest_prices) / period_high) * 100
        filtered_df = filtered_df.loc[:, pct_diff <= pct_high]

    # Relative Strength (RS) Ratio Filter
    if strat.get("use_rs", True):
        if NIFTY_REGIME_TICKER in prices_df.columns and GSEC_REGIME_TICKER in prices_df.columns:
            rs_ratio = prices_df[NIFTY_REGIME_TICKER] / prices_df[GSEC_REGIME_TICKER]
            rs_sma = rs_ratio.rolling(window=50).mean()
            if rs_ratio.iloc[-1] < rs_sma.iloc[-1]:
                return [ASSET_GROUPS_MAPPING["Gov Bond"][0]]

    # Composite Ranking
    periods = strat.get("period_days", [252, 120, 60])
    weights = strat.get("period_weights", [0.5, 0.3, 0.2])
    
    if len(weights) < len(periods):
        weights = [1.0 / len(periods)] * len(periods)
    
    weights = np.array(weights[:len(periods)], dtype=float)
    weights /= weights.sum()

    rank_components = []
    for p in periods:
        if len(filtered_df) > p:
            returns = (filtered_df.iloc[-1] / filtered_df.iloc[-p - 1] - 1).dropna()
            rank_components.append(returns.rank(ascending=False, method="min"))

    if not rank_components:
        return list(filtered_df.columns[:strat.get("entry_rank", 10)])

    composite_rank = pd.concat(rank_components, axis=1).mul(weights, axis=1).sum(axis=1).sort_values()
    target_count = strat.get("entry_rank", 10)
    return list(composite_rank.index[:target_count])

def run_backtest_simulation(strat_config, initial_capital, start_date, end_date):
    groups = strat_config.get("groups", ["Nifty 500"])
    tickers = get_trusted_tickers_by_group(groups)
    prices_df = fetch_market_data(tickers, period="5y")
    
    prices_df = prices_df.loc[start_date:end_date]
    if len(prices_df) < 252:
        return None

    rebalance_dates = prices_df.resample('M').first().index
    portfolio_history = []
    current_cash = initial_capital
    current_holdings = {}
    multiplier = strat_config.get("allocation_multiplier", 1.0)

    for i in range(len(rebalance_dates) - 1):
        dt = rebalance_dates[i]
        historical_sub_df = prices_df.loc[:dt]
        
        if len(historical_sub_df) < 252:
            continue

        selected_stocks = run_strategy_stock_scanner(strat_config, price_subset=historical_sub_df)
        
        total_val = current_cash
        for sym, qty in current_holdings.items():
            if sym in historical_sub_df.columns:
                total_val += qty * historical_sub_df[sym].iloc[-1]

        if selected_stocks:
            effective_pool = total_val * multiplier
            alloc_per_stock = effective_pool / len(selected_stocks)
            current_holdings = {}
            current_cash = total_val  # Reset cash pool

            for sym in selected_stocks:
                if sym in historical_sub_df.columns:
                    stk_price = historical_sub_df[sym].iloc[-1]
                    if stk_price > 0:
                        qty = int(alloc_per_stock // stk_price)
                        if qty > 0:
                            current_holdings[sym] = qty
                            current_cash -= (qty * stk_price)

        portfolio_history.append({"Date": dt, "Portfolio Value": total_val})

    df_res = pd.DataFrame(portfolio_history).set_index("Date")
    return df_res

# ==========================================
# NAVIGATION HEADER
# ==========================================
nav_col1, nav_col2, nav_col3, _ = st.columns([1.5, 2, 1.5, 3])
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

st.markdown("---")

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
        st.markdown(f"Status: <span class='{status_class}'>{strat['status']}</span> | **Groups:** {', '.join(strat.get('groups', ['Nifty 500']))} | **Multiplier:** {strat.get('allocation_multiplier', 1.0)}x | **Rebalance in:** {days_left} Days", unsafe_allow_html=True)

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
                scanned_tickers = run_strategy_stock_scanner(strat)
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
                scanned_rows = []
                for ticker in scanned_results:
                    cmp = float(prices_df[ticker].dropna().iloc[-1]) if (not prices_df.empty and ticker in prices_df.columns) else 0.0
                    scanned_rows.append({
                        "Symbol": ticker.replace(".NS", ""),
                        "CMP": f"₹{cmp:,.2f}",
                        "Status": "Entry Target"
                    })
                st.dataframe(pd.DataFrame(scanned_rows), use_container_width=True)

            if st.button("⚡ Execute Rebalance & Reinvest Capital", type="primary", use_container_width=True):
                reinvest_pool = metrics["current_total_value"] * strat.get("allocation_multiplier", 1.0)
                per_stock_alloc = reinvest_pool / len(scanned_results) if scanned_results else 0.0
                
                prices_df = fetch_market_data(scanned_results, period="5d")
                new_positions = []
                
                for ticker in scanned_results:
                    cmp = float(prices_df[ticker].dropna().iloc[-1]) if (not prices_df.empty and ticker in prices_df.columns) else 1.0
                    qty = int(per_stock_alloc // cmp) if cmp > 0 else 0
                    
                    if qty > 0:
                        new_positions.append({
                            "Symbol": ticker,
                            "Buy Qty": qty,
                            "Buy Price": cmp,  # Synchronized with current market close
                            "Entry Date": datetime.now().strftime("%Y-%m-%d")
                        })

                strat["positions"] = new_positions
                strat["realized_pnl"] = metrics["total_pnl"]
                st.session_state[f"scanned_results_{strat_name}"] = None
                st.success(f"Rebalanced! Reinvested effective amount of ₹{reinvest_pool:,.2f} ({strat.get('allocation_multiplier', 1.0)}x multiplier) across {len(new_positions)} assets.")
                st.rerun()

        else:
            st.markdown("#### 📋 Current Portfolio Holdings")
            if metrics["positions_data"]:
                st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
            else:
                st.info("No active scanned holdings for this strategy. Click 'Run Live Stock Scan' above.")

    else:
        st.title("MY PORTFOLIO")
        
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
        st.subheader("Real Strategies")

        strat_cols = st.columns(3)
        keys = list(st.session_state.strategies.keys())

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

    st.markdown("---")
    st.markdown("#### Ranking, Entry & Exit Parameters")

    rank_c1, rank_c2 = st.columns(2)
    with rank_c1:
        entry_rank = st.number_input("Entry Rank Stocks (Top N Target) :", value=int(edit_strat.get("entry_rank", 10)), min_value=1, max_value=50)
    with rank_c2:
        exit_rank = st.number_input("Exit Rank Threshold :", value=int(edit_strat.get("exit_rank", 20)), min_value=1, max_value=100)

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

    with sd_r3:
        ma_options = ["None", "200 EMA", "100 EMA", "200 SMA", "50 SMA"]
        ma_val = edit_strat.get("moving_average", "200 EMA")
        ma_idx = ma_options.index(ma_val) if ma_val in ma_options else 1
        selected_ma = st.selectbox("Moving Average Filter :", ma_options, index=ma_idx)

        use_rs = st.checkbox("Enable Relative Strength Ratio Filter", value=edit_strat.get("use_rs", True))
        rs_benchmark = st.selectbox("RS Benchmark Ratio :", ["Nifty 500 / G-Sec", "Nifty 50 / Liquid ETF"])

    st.markdown("---")
    save_label = "💾 Update Strategy Config" if edit_mode else "💾 Deploy New Strategy Profile"
    if st.button(save_label, type="primary", use_container_width=True):
        selected_groups = []
        if grp_nifty500: selected_groups.append("Nifty 500")
        if grp_nifty200: selected_groups.append("Nifty 200")
        if grp_all_etfs: selected_groups.append("All ETFs")
        if grp_gold_etf: selected_groups.append("Gold ETF")
        if grp_liquidbees: selected_groups.append("LiquidBEES")
        if grp_gov_bond: selected_groups.append("Gov Bond")

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

        target_name = st.session_state.editing_strategy_name if edit_mode else strat_name_input

        st.session_state.strategies[target_name] = {
            "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
            "status": edit_strat.get("status", "Active"),
            "type": st_type,
            "allocated": float(tot_alloc),
            "realized_pnl": edit_strat.get("realized_pnl", 0.0),
            "rebalance_freq": rebal_freq,
            "rebalance_day": rebal_day,
            "groups": selected_groups if selected_groups else ["Nifty 500"],
            "allocation_multiplier": float(alloc_multiplier),
            "entry_rank": entry_rank,
            "exit_rank": exit_rank,
            "period_days": selected_periods if selected_periods else [252],
            "period_weights": weights if weights else [1.0],
            "pct_from_high": pct_high,
            "pct_from_low": pct_low,
            "moving_average": selected_ma,
            "use_rs": use_rs,
            "rs_benchmark": rs_benchmark,
            "positions": edit_strat.get("positions", [])
        }
        
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

    run_bt_btn = st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True)

    if run_bt_btn:
        with st.spinner("Running historical rebalance simulation against market data..."):
            bt_results = run_backtest_simulation(bt_strat_config, initial_cap, start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d"))

            if bt_results is not None and not bt_results.empty:
                st.success("Backtest Completed Successfully!")
                
                final_val = float(bt_results["Portfolio Value"].iloc[-1])
                total_return_pct = ((final_val - initial_cap) / initial_cap) * 100
                peak = bt_results["Portfolio Value"].cummax()
                drawdown = (bt_results["Portfolio Value"] - peak) / peak
                max_drawdown_pct = drawdown.min() * 100

                res_m1, res_m2, res_m3 = st.columns(3)
                res_m1.metric("Final Portfolio Value", f"₹{final_val:,.2f}")
                res_m2.metric("Total Backtest Return", f"{total_return_pct:.2f}%")
                res_m3.metric("Max Drawdown", f"{max_drawdown_pct:.2f}%")

                st.markdown("#### Portfolio Value Equity Curve")
                st.line_chart(bt_results["Portfolio Value"])

                st.markdown("#### Historical Monthly Portfolio Curve Data")
                st.dataframe(bt_results, use_container_width=True)
            else:
                st.error("Insufficient market data for the selected timeframe. Try selecting a broader timeframe.")
