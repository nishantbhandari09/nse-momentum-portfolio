import io
from datetime import datetime, timedelta
import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(
    page_title="Quantitative Strategy Engine & Backtest Studio",
    page_icon="📈",
    layout="wide"
)

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

DATA_URL_PARQUET = "https://github.com/rishabhjain0295-web/nifty500-momentum-backtest/releases/download/data-v1/nifty500_historical.parquet"
DATA_URL_CSV = "https://github.com/rishabhjain0295-web/nifty500-momentum-backtest/releases/download/data-v1/nifty500_historical.csv.gz"

# ==========================================
# DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=86400)
def load_historical_release_dataset():
    """Fetches clean point-in-time dataset from release storage with fallbacks."""
    try:
        df = pd.read_parquet(DATA_URL_PARQUET)
        df.index = pd.to_datetime(df.index)
        return df
    except Exception:
        try:
            df = pd.read_csv(DATA_URL_CSV, compression="gzip", index_col=0, parse_dates=True)
            return df
        except Exception:
            return pd.DataFrame()

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
                df = pd.read_csv(io.StringIO(response.text))
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
# OPTIONAL SIGNAL & EXECUTION LAYER ENGINES
# ==========================================
def calculate_simple_momentum(price_df, lookback_days=126):
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)
    return (price_df.iloc[-1] / price_df.iloc[-lookback_days]) - 1.0

def calculate_volatility_adjusted_momentum(price_df, lookback_days=126):
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)

    subset = price_df.tail(lookback_days)
    daily_returns = subset.pct_change().dropna()
    total_return = (subset.iloc[-1] / subset.iloc[0]) - 1.0
    annualized_vol = daily_returns.std() * np.sqrt(252)

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
    if signal_type == "Volatility-Adjusted Momentum":
        return calculate_volatility_adjusted_momentum(price_df, lookback_days)
    else:
        return calculate_simple_momentum(price_df, lookback_days)

def is_regime_bullish(benchmark_series, current_date, sma_period=200):
    sub = benchmark_series.loc[:current_date].dropna()
    if len(sub) < sma_period:
        return True
    return sub.iloc[-1] >= sub.tail(sma_period).mean()

def apply_rank_buffering(current_holdings, ranked_candidates, target_size, buffer_rank):
    new_portfolio = []
    for stock in current_holdings:
        if stock in ranked_candidates:
            stock_rank = ranked_candidates.index(stock) + 1
            if stock_rank <= buffer_rank and len(new_portfolio) < target_size:
                new_portfolio.append(stock)

    for stock in ranked_candidates:
        if len(new_portfolio) >= target_size:
            break
        if stock not in new_portfolio:
            new_portfolio.append(stock)

    return new_portfolio

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
            "signal_type": "Simple Momentum",
            "use_regime_filter": True,
            "use_rank_buffer": True,
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

    ma_config = strat.get("moving_average", "200 EMA")
    if ma_config != "None":
        period = 200 if "200" in ma_config else (100 if "100" in ma_config else 50)
        is_ema = "EMA" in ma_config
        if is_ema:
            ma_vals = prices_df.ewm(span=period, adjust=False).mean().iloc[-1]
        else:
            ma_vals = prices_df.rolling(window=period).mean().iloc[-1]
        filtered_df = filtered_df.loc[:, latest_prices > ma_vals]

    pct_high = strat.get("pct_from_high", 15.0)
    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = ((period_high - latest_prices) / period_high) * 100
        filtered_df = filtered_df.loc[:, pct_diff <= pct_high]

    if strat.get("use_rs", True):
        if NIFTY_REGIME_TICKER in prices_df.columns and GSEC_REGIME_TICKER in prices_df.columns:
            rs_ratio = prices_df[NIFTY_REGIME_TICKER] / prices_df[GSEC_REGIME_TICKER]
            rs_sma = rs_ratio.rolling(window=50).mean()
            if rs_ratio.iloc[-1] < rs_sma.iloc[-1]:
                return [ASSET_GROUPS_MAPPING["Gov Bond"][0]]

    signal_model = strat.get("signal_type", "Simple Momentum")
    scores = get_momentum_scores(filtered_df, signal_type=signal_model, lookback_days=126)
    ranked = scores.sort_values(ascending=False).dropna().index.tolist()

    target_count = strat.get("entry_rank", 10)
    return ranked[:target_count]

def run_backtest_simulation(
    strat_config,
    initial_capital,
    start_date,
    end_date,
    price_df=None,
    benchmark_series=None,
    signal_type="Simple Momentum",
    use_regime_filter=True,
    use_rank_buffer=True,
    buffer_threshold=20
):
    if price_df is None or price_df.empty:
        groups = strat_config.get("groups", ["Nifty 500"])
        tickers = get_trusted_tickers_by_group(groups)
        price_df = fetch_market_data(tickers, period="5y")
    
    sub_prices = price_df.loc[start_date:end_date]
    if len(sub_prices) < 252:
        return None

    try:
        rebalance_dates = sub_prices.resample('ME').first().index
    except ValueError:
        rebalance_dates = sub_prices.resample('MS').first().index

    portfolio_history = []
    current_cash = initial_capital
    current_holdings = {}
    multiplier = strat_config.get("allocation_multiplier", 1.0)

    for i in range(len(rebalance_dates) - 1):
        dt = rebalance_dates[i]
        hist_df = sub_prices.loc[:dt]
        
        if len(hist_df) < 126:
            continue

        regime_ok = True
        if use_regime_filter and benchmark_series is not None:
            regime_ok = is_regime_bullish(benchmark_series, dt, sma_period=200)

        total_val = current_cash
        for sym, qty in current_holdings.items():
            if sym in hist_df.columns and not np.isnan(hist_df[sym].iloc[-1]):
                total_val += qty * hist_df[sym].iloc[-1]

        scores = get_momentum_scores(hist_df, signal_type=signal_type, lookback_days=126)
        ranked_stocks = scores.sort_values(ascending=False).dropna().index.tolist()

        if not regime_ok:
            current_holdings = {}
            current_cash = total_val
        else:
            top_n = strat_config.get("entry_rank", 10)
            if use_rank_buffer and current_holdings:
                selected_stocks = apply_rank_buffering(list(current_holdings.keys()), ranked_stocks, top_n, buffer_threshold)
            else:
                selected_stocks = ranked_stocks[:top_n]

            if selected_stocks:
                effective_pool = total_val * multiplier
                alloc_per_stock = effective_pool / len(selected_stocks)
                current_holdings = {}
                current_cash = total_val

                for sym in selected_stocks:
                    if sym in hist_df.columns:
                        stk_price = hist_df[sym].iloc[-1]
                        if stk_price > 0:
                            qty = int(alloc_per_stock // stk_price)
                            if qty > 0:
                                current_holdings[sym] = qty
                                current_cash -= (qty * stk_price)

        portfolio_history.append({"Date": dt, "Portfolio Value": total_val})

    if not portfolio_history:
        return None

    return pd.DataFrame(portfolio_history).set_index("Date")

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

        scan_col1, _ = st.columns([2, 3])
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
                            "Buy Price": cmp,
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
                        
                        if act_col1.button("⏸️" if is_active else "▶️", key=f"toggle_{name}"):
                            strat["status"] = "Paused" if is_active else "Active"
                            st.rerun()

                        if act_col2.button("✏️", key=f"edit_{name}"):
                            st.session_state.editing_strategy_name = name
                            st.session_state.navigation_tab = "STRATEGY_BUILDER"
                            st.rerun()

                        if act_col3.button("🗑️", key=f"del_{name}"):
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
    st.markdown("#### Asset Universe Selection")

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
    st.markdown("#### Signal & Execution Layer Settings")

    sig_c1, sig_c2 = st.columns(2)
    with sig_c1:
        signal_choice = st.selectbox(
            "Momentum Signal Layer :",
            ["Simple Momentum", "Volatility-Adjusted Momentum"],
            index=0 if edit_strat.get("signal_type") == "Simple Momentum" else 1
        )
        entry_rank = st.number_input("Entry Rank Stocks (Top N Target) :", value=int(edit_strat.get("entry_rank", 10)), min_value=1, max_value=50)
    with sig_c2:
        use_regime = st.checkbox("Enable 200 SMA Regime Filter", value=edit_strat.get("use_regime_filter", True))
        use_buffer = st.checkbox("Enable Rank Buffer Rule", value=edit_strat.get("use_rank_buffer", True))
        exit_rank = st.number_input("Exit / Buffer Rank Cutoff :", value=int(edit_strat.get("exit_rank", 20)), min_value=1, max_value=100)

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
            "signal_type": signal_choice,
            "use_regime_filter": use_regime,
            "use_rank_buffer": use_buffer,
            "pct_from_high": 15.0,
            "moving_average": "200 EMA",
            "use_rs": True,
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
    st.caption("Simulate historical momentum performance with clean point-in-time release dataset, configurable signal models, and execution filters.")

    strategy_names = list(st.session_state.strategies.keys())
    selected_backtest_strat = st.selectbox("Select Base Strategy Profile :", strategy_names)

    bt_strat_config = st.session_state.strategies[selected_backtest_strat]

    st.markdown("---")
    st.markdown("#### ⚙️ Simulation Settings & Optional Filters")

    bt_c1, bt_c2, bt_c3 = st.columns(3)
    with bt_c1:
        initial_cap = st.number_input("Initial Backtest Capital (₹) :", value=100000.0, step=10000.0)
        signal_model = st.selectbox("Signal Layer Model :", ["Simple Momentum", "Volatility-Adjusted Momentum"])
    with bt_c2:
        start_d = st.date_input("Start Date :", value=datetime.now() - timedelta(days=365*3))
        regime_flt = st.checkbox("Enable 200 SMA Market Regime Filter", value=True)
    with bt_c3:
        end_d = st.date_input("End Date :", value=datetime.now())
        rank_buff = st.checkbox("Enable Rank Buffer Rule (Reduce Turnover)", value=True)
        buffer_val = st.number_input("Buffer Rank Cutoff :", value=20, min_value=10, max_value=50)

    run_bt_btn = st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True)

    if run_bt_btn:
        with st.spinner("Loading dataset & running simulation..."):
            rel_df = load_historical_release_dataset()
            
            benchmark_series = None
            if "^NSEI" in rel_df.columns:
                benchmark_series = rel_df["^NSEI"]
                stock_prices = rel_df.drop(columns=["^NSEI"])
            else:
                stock_prices = rel_df

            bt_results = run_backtest_simulation(
                strat_config=bt_strat_config,
                initial_capital=initial_cap,
                start_date=start_d.strftime("%Y-%m-%d"),
                end_date=end_d.strftime("%Y-%m-%d"),
                price_df=stock_prices,
                benchmark_series=benchmark_series,
                signal_type=signal_model,
                use_regime_filter=regime_flt,
                use_rank_buffer=rank_buff,
                buffer_threshold=buffer_val
            )

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
