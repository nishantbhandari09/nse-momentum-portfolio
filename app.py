from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from fyers_data import FyersClient, build_login_url, exchange_auth_code, get_default_access_token, download_symbol_master, get_history_cached
from portfolio_store import (
    add_capital,
    capital_summary,
    get_managed_symbols,
    get_strategy,
    get_virtual_holdings,
    init_db,
    list_strategies,
    mark_managed,
    record_transaction,
    save_strategy,
    set_virtual_holding,
    transaction_history,
    unmark_managed,
    update_virtual_cash,
)
from strategy_engine import backtest_strategy, scan_strategy, load_history_for_symbol, clean_symbol
from universe_manager import available_groups, load_catalog, refresh_catalog, sync_nifty_constituents, symbols_for_group, CATALOG_PATH

st.set_page_config(page_title="NSE Momentum Portfolio", page_icon="📈", layout="wide")

DATA = Path("data")
(DATA / "prices").mkdir(parents=True, exist_ok=True)
(DATA / "indices").mkdir(parents=True, exist_ok=True)
(DATA / "reference").mkdir(parents=True, exist_ok=True)
init_db()


def money(v: float) -> str:
    try:
        return f"₹{float(v):,.2f}"
    except Exception:
        return "₹0.00"


def pct(v: float) -> str:
    try:
        return f"{float(v) * 100:.2f}%"
    except Exception:
        return "0.00%"


def client_from_session() -> FyersClient | None:
    token = st.session_state.get("fyers_access_token", "")
    if not token:
        token = get_default_access_token()
    if not token:
        return None
    try:
        return FyersClient(token)
    except Exception:
        return None


def handle_fyers_callback() -> None:
    try:
        params = st.query_params
        auth_code = params.get("auth_code", "")
        err = params.get("error", "")
        if err:
            st.error(f"FYERS returned an error: {err}")
        if auth_code:
            with st.spinner("Connecting to FYERS..."):
                result = exchange_auth_code(auth_code)
            st.session_state["fyers_access_token"] = result["access_token"]
            st.session_state["fyers_connected"] = True
            st.query_params.clear()
            st.success("FYERS connected for this session.")
    except Exception as e:
        st.error(f"FYERS authentication failed: {e}")


handle_fyers_callback()
client = client_from_session()

with st.sidebar:
    st.title("📈 Momentum Portfolio")
    st.caption("FYERS data + rules-based scanner + portfolio + backtest")
    st.divider()
    if client:
        st.success("FYERS CONNECTED")
        try:
            profile = client.profile()
            display_name = profile.get("data", {}).get("name") or profile.get("name") or "Connected account"
            st.caption(str(display_name))
        except Exception:
            pass
    else:
        st.warning("FYERS not connected")
        login_url = build_login_url(state="momentum")
        st.markdown(f"[**🔐 Connect FYERS**]({login_url})")
    st.divider()
    page = st.radio("Section", ["Scanner", "Portfolio", "Monthly Backtest", "Data Manager", "Saved Strategies"], index=0)


def default_strategy() -> dict:
    return {
        "group": "NIFTY500",
        "lookbacks": {252: 0.40, 120: 0.30, 90: 0.20, 60: 0.10},
        "top_n": 20,
        "exit_rank": 40,
        "rebalance": "Monthly",
        "market_index": "NSE:NIFTY50-INDEX",
        "market_indicator": "EMA",
        "market_period": 200,
        "vstop_atr": 14,
        "vstop_multiplier": 3.0,
        "conditions": {
            "above_ema": False,
            "ema_period": 50,
            "above_sma": False,
            "sma_period": 50,
            "near_52w": False,
            "near_52w_pct": 40.0,
            "retracement_reference": "52W High",
            "breakout_52w": False,
        },
        "transaction_cost_bps": 10.0,
        "slippage_bps": 5.0,
        "product_type": "CNC",
    }


def strategy_editor(prefix: str, base: dict | None = None) -> dict:
    base = base or default_strategy()
    catalog = load_catalog()
    groups = available_groups(catalog)
    c1, c2, c3 = st.columns(3)
    preferred_group = base.get("group", "NIFTY500")
    group_index = groups.index(preferred_group) if preferred_group in groups else (groups.index("NIFTY500") if "NIFTY500" in groups else 0)
    group = c1.selectbox("Universe / Group", groups, index=group_index, key=f"{prefix}_group")
    top_n = c2.number_input("Top N Entry Rank", min_value=1, max_value=500, value=int(base.get("top_n", 20)), key=f"{prefix}_top")
    exit_rank = c3.number_input("Exit Rank", min_value=1, max_value=1000, value=int(base.get("exit_rank", 40)), key=f"{prefix}_exit")
    if exit_rank <= top_n:
        st.warning("Exit Rank should normally be greater than Top N so that a held stock can remain invested after falling out of the entry set.")

    st.markdown("### Momentum Ranking")
    cols = st.columns(4)
    default_weights = base.get("lookbacks", default_strategy()["lookbacks"])
    weights = {}
    for col, n in zip(cols, [252, 120, 90, 60]):
        weights[n] = col.number_input(f"{n}D weight", min_value=0.0, max_value=100.0,
                                      value=float(default_weights.get(n, 0.0) * 100), step=5.0, key=f"{prefix}_w{n}") / 100.0
    if sum(weights.values()) <= 0:
        st.error("At least one momentum weight must be greater than zero.")
    elif abs(sum(weights.values()) - 1.0) > 1e-6:
        st.caption(f"Weights will be normalized automatically (current total: {sum(weights.values()) * 100:.1f}%).")
        total = sum(weights.values())
        weights = {k: v / total for k, v in weights.items()}

    st.markdown("### Stock Conditions")
    cond0 = base.get("conditions", {})
    c1, c2 = st.columns(2)
    above_ema = c1.checkbox("Price above EMA", bool(cond0.get("above_ema", False)), key=f"{prefix}_ema_on")
    ema_period = c2.number_input("EMA period", 2, 500, int(cond0.get("ema_period", 50)), key=f"{prefix}_ema_p")
    c3, c4 = st.columns(2)
    above_sma = c3.checkbox("Price above SMA", bool(cond0.get("above_sma", False)), key=f"{prefix}_sma_on")
    sma_period = c4.number_input("SMA period", 2, 500, int(cond0.get("sma_period", 50)), key=f"{prefix}_sma_p")
    c5, c6 = st.columns(2)
    near_52w = c5.checkbox("Within % of 52-week high", bool(cond0.get("near_52w", False)), key=f"{prefix}_52w_on")
    near_52w_pct = c6.number_input("Maximum % below 52-week high", 0.0, 100.0, float(cond0.get("near_52w_pct", 40.0)), step=1.0, key=f"{prefix}_52w_pct")
    st.caption("Example: 40% means the close can be at most 40% below the 52-week high.")
    breakout_52w = st.checkbox("52-week breakout (close > prior 252-day high)", bool(cond0.get("breakout_52w", False)), key=f"{prefix}_breakout")

    st.markdown("### Market Entry Gate — blocks new entries only")
    idx_options = ["NSE:NIFTY50-INDEX", "NSE:NIFTY100-INDEX", "NSE:NIFTY200-INDEX", "NSE:NIFTY500-INDEX", "NSE:NIFTYBANK-INDEX"]
    index_value = base.get("market_index", idx_options[0])
    market_index = st.selectbox("Market Index", idx_options, index=idx_options.index(index_value) if index_value in idx_options else 0, key=f"{prefix}_midx")
    m1, m2 = st.columns(2)
    indicator_options = ["EMA", "SMA", "VSTOP"]
    market_indicator = m1.selectbox("Market Indicator", indicator_options,
                                     index=indicator_options.index(base.get("market_indicator", "EMA")), key=f"{prefix}_mind")
    market_period = m2.number_input("MA period", 2, 1000, int(base.get("market_period", 200)), key=f"{prefix}_mp")
    if market_indicator == "VSTOP":
        v1, v2 = st.columns(2)
        vstop_atr = v1.number_input("VSTOP ATR period", 2, 100, int(base.get("vstop_atr", 14)), key=f"{prefix}_vatr")
        vstop_multiplier = v2.number_input("VSTOP multiplier", 0.5, 10.0, float(base.get("vstop_multiplier", 3.0)), step=0.25, key=f"{prefix}_vmult")
    else:
        vstop_atr = int(base.get("vstop_atr", 14))
        vstop_multiplier = float(base.get("vstop_multiplier", 3.0))
    st.caption("VSTOP is only the optional market gate. It is not part of stock ranking, and the app has no volatility ranking/penalty function.")

    r1, r2 = st.columns(2)
    rebalance = r1.selectbox("Rebalance frequency", ["Monthly", "Quarterly"], index=["Monthly", "Quarterly"].index(base.get("rebalance", "Monthly")), key=f"{prefix}_reb")
    product_type = r2.selectbox("Product type for live equity/ETF orders", ["CNC"], key=f"{prefix}_product")
    costs1, costs2 = st.columns(2)
    cost_bps = costs1.number_input("Transaction cost assumption (bps)", 0.0, 200.0, float(base.get("transaction_cost_bps", 10.0)), key=f"{prefix}_cost")
    slip_bps = costs2.number_input("Slippage assumption (bps)", 0.0, 200.0, float(base.get("slippage_bps", 5.0)), key=f"{prefix}_slip")

    return {
        "group": group,
        "lookbacks": weights,
        "top_n": int(top_n),
        "exit_rank": int(exit_rank),
        "rebalance": rebalance,
        "market_index": market_index,
        "market_indicator": market_indicator,
        "market_period": int(market_period),
        "vstop_atr": int(vstop_atr),
        "vstop_multiplier": float(vstop_multiplier),
        "conditions": {
            "above_ema": bool(above_ema), "ema_period": int(ema_period),
            "above_sma": bool(above_sma), "sma_period": int(sma_period),
            "near_52w": bool(near_52w), "near_52w_pct": float(near_52w_pct),
            "retracement_reference": "52W High",
            "breakout_52w": bool(breakout_52w),
        },
        "transaction_cost_bps": float(cost_bps),
        "slippage_bps": float(slip_bps),
        "product_type": product_type,
    }


def live_holdings_df(c: FyersClient) -> pd.DataFrame:
    raw = c.holdings()
    data = raw.get("holdings", raw.get("data", []))
    if isinstance(data, dict):
        data = data.get("holdings", [])
    rows = []
    for x in data or []:
        rows.append({
            "symbol": x.get("symbol") or x.get("fyToken") or x.get("n"),
            "qty": int(x.get("qty") or x.get("quantity") or x.get("holdingQty") or 0),
            "avg_price": float(x.get("costPrice") or x.get("avgPrice") or x.get("avg_price") or 0),
            "pnl": float(x.get("pl") or x.get("pnl") or 0),
        })
    return pd.DataFrame(rows)


def available_funds(c: FyersClient) -> float:
    data = c.funds()
    limits = data.get("fund_limit") or data.get("fundLimit") or data.get("data", {}).get("fund_limit", [])
    for row in limits if isinstance(limits, list) else []:
        rid = row.get("id")
        if rid in (1, 6, 9):
            for key in ("equityAmount", "available", "fundValue", "value"):
                if row.get(key) is not None:
                    try:
                        return float(row[key])
                    except Exception:
                        pass
    for key in ("availableBalance", "available", "clearBalance", "cash"):
        if isinstance(data, dict) and data.get(key) is not None:
            try:
                return float(data[key])
            except Exception:
                pass
    return 0.0


def price_from_quotes(c: FyersClient, symbols: list[str]) -> dict[str, float]:
    q = c.quotes(symbols)
    out = {}
    for sym, obj in q.items():
        v = obj.get("v", obj) if isinstance(obj, dict) else {}
        lp = v.get("lp") or v.get("last_price") or v.get("ltp")
        if lp is not None:
            out[sym] = float(lp)
    return out


def create_proposal(strategy: dict, mode: str, ranked: pd.DataFrame, strategy_id: int, c: FyersClient | None) -> tuple[pd.DataFrame, pd.DataFrame, float]:
    if mode == "VIRTUAL":
        held = pd.DataFrame(get_virtual_holdings(strategy_id))
        if held.empty:
            held = pd.DataFrame(columns=["symbol", "qty", "avg_price"])
        cash = capital_summary(strategy_id)["virtual_cash"]
    else:
        if c is None:
            raise RuntimeError("Connect FYERS first.")
        live = live_holdings_df(c)
        managed = set(get_managed_symbols(strategy_id, "REAL"))
        held = live[live["symbol"].isin(managed)].copy() if not live.empty else pd.DataFrame(columns=["symbol", "qty", "avg_price"])
        cash = available_funds(c)
    rank_map = dict(zip(ranked["symbol"], ranked["rank"])) if not ranked.empty else {}
    price_map = price_from_quotes(c, held["symbol"].tolist() + ranked.head(strategy["top_n"])["symbol"].tolist()) if c else {}
    exits=[]
    for _, h in held.iterrows():
        sym=h["symbol"]
        r=rank_map.get(sym, 999999)
        if r > strategy["exit_rank"]:
            px = price_map.get(sym, float(h.get("avg_price", 0)))
            exits.append({"action":"SELL","symbol":sym,"rank":None if r>=999999 else r,"qty":int(h["qty"]),"price":px,"amount":int(h["qty"])*px,"reason":"EXIT RANK / NOT IN SCAN"})
    held_symbols=set(held["symbol"].tolist())
    candidates=ranked[ranked["rank"]<=strategy["top_n"]].copy() if not ranked.empty else ranked
    entries=[]
    available_after_sales=cash+sum(x["amount"] for x in exits)
    new_candidates=[row for _,row in candidates.iterrows() if row["symbol"] not in held_symbols]
    slots=max(len(new_candidates),1)
    per_slot=available_after_sales/slots if new_candidates else 0
    for row in new_candidates:
        sym=row["symbol"]
        px=price_map.get(sym, float(row["last_close"]))
        if px<=0: continue
        qty=int(per_slot//px)
        if qty>0:
            entries.append({"action":"BUY","symbol":sym,"rank":int(row["rank"]),"qty":qty,"price":px,"amount":qty*px,"reason":"TOP N ENTRY"})
    return pd.DataFrame(exits), pd.DataFrame(entries), cash


if page == "Scanner":
    st.header("1. Scanner")
    st.write("Scan the selected universe, rank it by momentum, and create a strategy. Scanning never places orders.")
    strategy = strategy_editor("scan", default_strategy())
    c1, c2, c3 = st.columns([1,1,2])
    scan_date = c1.date_input("Scan date", date.today(), key="scan_date")
    scan_clicked = c2.button("🔎 SCAN STOCKS", type="primary", use_container_width=True)
    strategy_name = c3.text_input("Strategy name", "Momentum Strategy 20", key="scan_strategy_name")
    if scan_clicked:
        with st.spinner("Checking FYERS history and scanning the selected universe..."):
            try:
                if client is None:
                    st.error("Connect FYERS first. The scanner now downloads missing history automatically from FYERS.")
                    st.stop()
                ranked, meta = scan_strategy(strategy, scan_date, client=client, auto_fetch=True)
                st.session_state["last_scan"] = ranked
                st.session_state["last_scan_meta"] = meta
                st.session_state["last_scan_strategy"] = strategy
                st.success(f"Scan completed: {len(ranked)} qualifying symbols from universe size {meta['universe_size']}.")
            except Exception as e:
                st.error(f"Scan failed: {e}")
    ranked = st.session_state.get("last_scan", pd.DataFrame())
    if not ranked.empty:
        st.subheader("Ranked Stocks / ETFs / Index Data")
        st.dataframe(ranked, use_container_width=True, hide_index=True)
        allowed = st.session_state.get("last_scan_meta", {})
        st.info(f"{allowed.get('asof','')} · {allowed.get('group','')} · {len(ranked)} qualifying symbols")
        save_col, execute_col = st.columns(2)
        if save_col.button("💾 SAVE / UPDATE STRATEGY", use_container_width=True):
            sid = save_strategy(strategy_name.strip() or "Momentum Strategy", strategy)
            st.session_state["selected_strategy_id"] = sid
            st.success(f"Strategy saved: {strategy_name}")
        if execute_col.button("Open Portfolio Execution →", use_container_width=True):
            st.session_state["selected_strategy_id"] = save_strategy(strategy_name.strip() or "Momentum Strategy", strategy)
            st.session_state["page_jump"] = "Portfolio"
            st.rerun()
    else:
        st.info("Run a scan. If FYERS is connected, missing history is downloaded automatically; Data Manager is only needed for manual/bulk downloads.")

elif page == "Portfolio":
    st.header("2. Portfolio Execution")
    strategies = list_strategies()
    if not strategies:
        st.warning("Save a strategy in Scanner first.")
        st.stop()
    ids = [x["id"] for x in strategies]
    current_id = st.session_state.get("selected_strategy_id", ids[0])
    selected = next((x for x in strategies if x["id"] == current_id), strategies[0])
    sid = st.selectbox("Saved Strategy", ids, format_func=lambda i: next(x["name"] for x in strategies if x["id"] == i), index=ids.index(selected["id"]))
    strategy = get_strategy(int(sid))
    st.session_state["selected_strategy_id"] = sid
    strategy_cfg = strategy["config"]

    st.subheader(strategy["name"])
    st.caption(f"Group: {strategy_cfg['group']} · Top {strategy_cfg['top_n']} · Exit Rank {strategy_cfg['exit_rank']} · Rebalance: {strategy_cfg['rebalance']}")
    cap1, cap2, cap3, cap4 = st.columns(4)
    investment = cap1.number_input("Investment", 0.0, 1000000000.0, 0.0, step=10000.0, key="investment")
    addfunds = cap2.number_input("Add Funds", 0.0, 1000000000.0, 0.0, step=10000.0, key="addfunds")
    if cap3.button("Add Capital", use_container_width=True):
        if investment > 0:
            add_capital(sid, investment, "Investment", "Initial strategy allocation")
        if addfunds > 0:
            add_capital(sid, addfunds, "Add Funds", "Additional strategy allocation")
        st.success("Capital ledger updated.")
        st.rerun()
    summary = capital_summary(sid)
    cap4.metric("Total Strategy Capital", money(summary["total_capital"]))
    st.write(f"Investment: **{money(summary['investment'])}** · Add Funds: **{money(summary['add_funds'])}** · Virtual cash: **{money(summary['virtual_cash'])}**")

    mode = st.radio("Execution Mode", ["VIRTUAL", "REAL"], horizontal=True, help="VIRTUAL updates the model portfolio. REAL places live FYERS orders after confirmation.")
    if mode == "REAL":
        st.warning("REAL mode sends live FYERS orders. Use only after checking every symbol, quantity and amount in the proposal.")
    scan = st.session_state.get("last_scan", pd.DataFrame())
    if scan.empty:
        st.info("Run the Scanner first. The Portfolio section executes the latest saved/current scan against the selected strategy.")
        st.stop()
    exits, entries, cash = create_proposal(strategy_cfg, mode, scan, sid, client)
    st.subheader("Proposed Exits")
    st.dataframe(exits if not exits.empty else pd.DataFrame(columns=["action","symbol","rank","qty","price","amount","reason"]), use_container_width=True, hide_index=True)
    st.subheader("Proposed Entries")
    st.dataframe(entries if not entries.empty else pd.DataFrame(columns=["action","symbol","rank","qty","price","amount","reason"]), use_container_width=True, hide_index=True)

    exec_date = st.date_input("Execution date", date.today(), key="portfolio_exec_date")
    if exits.empty and entries.empty:
        st.info("No portfolio changes are required from the latest scan.")
    elif mode == "VIRTUAL":
        if st.button("▶ EXECUTE VIRTUAL PORTFOLIO", type="primary", use_container_width=True):
            with st.spinner("Updating virtual portfolio..."):
                for _, r in exits.iterrows():
                    old = next((h for h in get_virtual_holdings(sid) if h["symbol"] == r["symbol"]), None)
                    pnl = (float(r["price"]) - float(old["avg_price"])) * int(r["qty"]) if old else 0.0
                    set_virtual_holding(sid, r["symbol"], 0, 0, str(exec_date))
                    update_virtual_cash(sid, float(r["amount"]))
                    record_transaction(sid, "VIRTUAL", "SELL", r["symbol"], int(r["qty"]), float(r["price"]), float(r["amount"]),
                                       int(r["rank"]) if pd.notna(r["rank"]) else None, r["reason"], status="EXECUTED", pnl=pnl, event_date=str(exec_date))
                    unmark_managed(sid, r["symbol"], "VIRTUAL")
                for _, r in entries.iterrows():
                    set_virtual_holding(sid, r["symbol"], int(r["qty"]), float(r["price"]), str(exec_date))
                    update_virtual_cash(sid, -float(r["amount"]))
                    record_transaction(sid, "VIRTUAL", "BUY", r["symbol"], int(r["qty"]), float(r["price"]), float(r["amount"]),
                                       int(r["rank"]), r["reason"], status="EXECUTED", event_date=str(exec_date))
                    mark_managed(sid, r["symbol"], "VIRTUAL")
            st.success("Virtual portfolio updated.")
            st.rerun()
    else:
        confirm = st.checkbox("I understand this will place LIVE orders in my FYERS account.")
        typed = st.text_input("Type EXECUTE LIVE to unlock", type="password")
        if st.button("🚨 CONFIRM & PLACE LIVE ORDERS", type="primary", use_container_width=True, disabled=not (confirm and typed == "EXECUTE LIVE")):
            if client is None:
                st.error("FYERS is not connected.")
            else:
                results=[]
                # Sell first, so realised cash can become available for new entries.
                for _, r in exits.iterrows():
                    try:
                        resp=client.place_order(r["symbol"], -1, int(r["qty"]), product_type=str(strategy_cfg.get("product_type","CNC")), order_type=2, order_tag="MOMEXSELL")
                        ok=str(resp.get("s","" )).lower()=="ok"
                        oid=str(resp.get("id") or resp.get("orderId") or "")
                        record_transaction(sid,"REAL","SELL",r["symbol"],int(r["qty"]),float(r["price"]),float(r["amount"]),int(r["rank"]) if pd.notna(r["rank"]) else None,r["reason"],order_id=oid,status="ACCEPTED" if ok else "REJECTED",raw=resp,event_date=str(exec_date))
                        if ok:
                            unmark_managed(sid,r["symbol"] ,"REAL")
                        results.append({"action":"SELL","symbol":r["symbol"],"status":"ACCEPTED" if ok else "REJECTED","response":resp})
                    except Exception as e:
                        results.append({"action":"SELL","symbol":r["symbol"],"status":"ERROR","response":str(e)})
                for _, r in entries.iterrows():
                    try:
                        resp=client.place_order(r["symbol"], 1, int(r["qty"]), product_type=str(strategy_cfg.get("product_type","CNC")), order_type=2, order_tag="MOMEXBUY")
                        ok=str(resp.get("s","" )).lower()=="ok"
                        oid=str(resp.get("id") or resp.get("orderId") or "")
                        record_transaction(sid,"REAL","BUY",r["symbol"],int(r["qty"]),float(r["price"]),float(r["amount"]),int(r["rank"]),r["reason"],order_id=oid,status="ACCEPTED" if ok else "REJECTED",raw=resp,event_date=str(exec_date))
                        if ok:
                            mark_managed(sid,r["symbol"],"REAL")
                        results.append({"action":"BUY","symbol":r["symbol"],"status":"ACCEPTED" if ok else "REJECTED","response":resp})
                    except Exception as e:
                        results.append({"action":"BUY","symbol":r["symbol"],"status":"ERROR","response":str(e)})
                st.dataframe(pd.DataFrame(results), use_container_width=True, hide_index=True)
                st.warning("Refresh your FYERS holdings/orderbook after execution; market order fills are not guaranteed at the scanned price.")

    st.subheader("Current Holdings")
    if mode == "VIRTUAL":
        vh = pd.DataFrame(get_virtual_holdings(sid))
        if not vh.empty:
            prices = price_from_quotes(client, vh["symbol"].tolist()) if client else {}
            vh["ltp"] = vh["symbol"].map(prices)
            vh["value"] = vh["qty"] * vh["ltp"]
            vh["unrealized_pnl"] = vh["qty"] * (vh["ltp"] - vh["avg_price"])
        st.dataframe(vh, use_container_width=True, hide_index=True)
    else:
        if client:
            live = live_holdings_df(client)
            managed=set(get_managed_symbols(sid,"REAL"))
            st.dataframe(live[live["symbol"].isin(managed)] if not live.empty else live, use_container_width=True, hide_index=True)
        else:
            st.info("Connect FYERS to see real holdings.")
    st.subheader("Transaction History")
    hist=transaction_history(sid, mode)
    st.dataframe(hist, use_container_width=True, hide_index=True)

elif page == "Monthly Backtest":
    st.header("3. Monthly / Quarterly Backtest")
    st.write("Historical signals use the rebalance close; trades execute at the next available trading-day open to avoid look-ahead bias. Existing holdings can exit even while the market gate blocks new entries.")
    strategies=list_strategies()
    if not strategies:
        st.warning("Save a strategy first.")
        st.stop()
    ids=[x["id"] for x in strategies]
    sid=st.selectbox("Strategy", ids, format_func=lambda i: next(x["name"] for x in strategies if x["id"]==i), key="bt_strategy")
    cfg=get_strategy(sid)["config"]
    b1,b2,b3=st.columns(3)
    start=b1.date_input("Backtest start", date.today()-timedelta(days=5*365), key="bt_start")
    end=b2.date_input("Backtest end", date.today(), key="bt_end")
    capital=b3.number_input("Initial Capital", 10000.0, 1000000000.0, 500000.0, step=10000.0, key="bt_cap")
    auto_fetch=st.checkbox("Auto-fetch missing FYERS history", True, key="bt_fetch")
    if st.button("▶ RUN FULL BACKTEST", type="primary", use_container_width=True):
        if end <= start:
            st.error("Backtest end date must be after start date.")
        else:
            with st.spinner("Running historical ranking, market gate, entries, exits, cash and equity curve..."):
                try:
                    result=backtest_strategy(cfg,start,end,float(capital),client=client,auto_fetch=auto_fetch)
                    st.session_state["backtest_result"]=result
                    st.success("Backtest completed.")
                except Exception as e:
                    st.error(f"Backtest failed: {e}")
                    st.exception(e)
    result=st.session_state.get("backtest_result")
    if result:
        m=result["metrics"]
        st.subheader("Backtest Summary")
        cols=st.columns(6)
        cols[0].metric("Starting Capital",money(m["starting_capital"]))
        cols[1].metric("Ending Value",money(m["ending_value"]))
        cols[2].metric("Total Return",pct(m["total_return"]))
        cols[3].metric("CAGR",pct(m["cagr"]))
        cols[4].metric("Max Drawdown",pct(m["max_drawdown"]))
        cols[5].metric("Profit Factor", "∞" if np.isinf(m["profit_factor"]) else f"{m['profit_factor']:.2f}")
        st.caption(f"Trades: {m['total_trades']} · Buys: {m['buy_count']} · Sells: {m['sell_count']} · Win rate: {pct(m['win_rate'])} · Ending cash: {money(m['ending_cash'])} · Open positions: {m['open_positions']}")
        eq=result["equity"]
        if not eq.empty:
            e=eq.copy(); e["date"]=pd.to_datetime(e["date"]); e=e.set_index("date")
            st.subheader("Equity Curve")
            st.line_chart(e["equity"])
        st.subheader("Month-by-Month Rebalance Detail")
        st.dataframe(result["rebalances"], use_container_width=True, hide_index=True)
        st.subheader("Complete Entry / Exit Log")
        st.dataframe(result["trades"], use_container_width=True, hide_index=True)
        st.subheader("End-of-Period Holdings")
        st.dataframe(result["open_holdings"], use_container_width=True, hide_index=True)
        st.download_button("Download Backtest Transactions CSV", result["trades"].to_csv(index=False), "backtest_transactions.csv", "text/csv")
        st.download_button("Download Monthly Rebalances CSV", result["rebalances"].to_csv(index=False), "backtest_rebalances.csv", "text/csv")
        st.download_button("Download Equity Curve CSV", result["equity"].to_csv(index=False), "backtest_equity_curve.csv", "text/csv")

elif page == "Data Manager":
    st.header("4. Data Manager")
    st.write("FYERS is the market-data source. The Data Manager syncs the current symbol master and stores historical candles locally in Parquet for scanning and backtesting.")
    c1,c2,c3,c4=st.columns(4)
    if c1.button("⬇ Sync FYERS NSE Symbol Master", use_container_width=True):
        try:
            download_symbol_master("NSE_CM", "data/reference/fyers_symbol_master.csv")
            catalog=refresh_catalog()
            st.success(f"Symbol master synced. Catalog rows: {len(catalog)}")
        except Exception as e:
            st.error(f"Symbol master sync failed: {e}")
    if c2.button("🔄 Rebuild Universe Catalog", use_container_width=True):
        catalog=refresh_catalog()
        st.success(f"Catalog rebuilt: {len(catalog)} symbols.")
    if c3.button("🇮🇳 Sync NIFTY 50/100/200/500", use_container_width=True):
        catalog=sync_nifty_constituents(load_catalog())
        st.success(f"NIFTY constituent groups synced. Catalog rows: {len(catalog)}")
    if c4.button("📦 Export Universe Catalog", use_container_width=True):
        if CATALOG_PATH.exists():
            st.download_button("Download Catalog", CATALOG_PATH.read_bytes(), "universe_catalog.csv", "text/csv")
    st.subheader("Download Historical Data")
    catalog=load_catalog()
    groups=available_groups(catalog)
    group=st.selectbox("Group",groups,key="dm_group")
    syms=symbols_for_group(group,catalog)
    if syms.empty:
        st.warning("No symbols found. Sync the FYERS symbol master first.")
    else:
        st.caption(f"{len(syms)} symbols in {group}")
        symbols=st.multiselect("Symbols (leave empty to download the whole group)", syms["symbol"].tolist(), key="dm_syms")
        d1,d2=st.columns(2)
        start=d1.date_input("Start",date.today()-timedelta(days=730),key="dm_start")
        end=d2.date_input("End",date.today(),key="dm_end")
        force=st.checkbox("Force re-download existing ranges",False,key="dm_force")
        if st.button("⬇ Download / Update Historical Data", type="primary", use_container_width=True):
            if client is None:
                st.error("Connect FYERS first.")
            else:
                chosen=symbols or syms["symbol"].tolist()
                progress=st.progress(0.0)
                status=st.empty()
                errors=[]
                for i,sym in enumerate(chosen):
                    try:
                        status.write(f"Downloading {sym} ({i+1}/{len(chosen)})")
                        cache_dir="data/indices" if "-INDEX" in sym else "data/prices"
                        get_history_cached(client,sym,start,end,cache_dir,force=force)
                    except Exception as e:
                        errors.append({"symbol":sym,"error":str(e)})
                    progress.progress((i+1)/len(chosen))
                if errors:
                    st.warning(f"Completed with {len(errors)} failures.")
                    st.dataframe(pd.DataFrame(errors),use_container_width=True,hide_index=True)
                else:
                    st.success(f"Downloaded/updated {len(chosen)} symbols.")
    st.subheader("Universe Coverage")
    if not catalog.empty:
        st.dataframe(catalog.groupby(["asset_type"]).size().rename("symbols").reset_index(), use_container_width=True, hide_index=True)
        st.caption("Dynamic groups include ALL ETF, DOMESTIC ETF, INTERNATIONAL ETF, DEFENSIVE, and ALL INDEX. Edit universe_groups.csv for additional custom group mappings.")

elif page == "Saved Strategies":
    st.header("5. Saved Strategies")
    strategies=list_strategies()
    if not strategies:
        st.info("No saved strategies yet. Save one from Scanner.")
    else:
        for s in strategies:
            with st.expander(f"{s['name']} (ID {s['id']})"):
                st.json(s["config"])
                st.caption(f"Last updated: {s['updated_at']}")
