"""
Complete NSE ETF and index universe, grouped into categories.

Sourced from Fyers' public symbol master file
(https://public.fyers.in/sym_details/NSE_CM.csv) -- this is a plain,
unauthenticated download Fyers publishes and refreshes daily, so this
doesn't need a working Fyers login at all, unlike live quotes/history.

The file ships with no header row; its columns are known from Fyers'
community documentation to be (in order): fytoken, name, instrument_type,
lot_size, tick_size, isin, trading_session, last_update, expiry_date,
symbol, exchange_code, segment_code, scrip_code, short_symbol, strike,
option_type, fytoken_again, reserved. I'm only confident about 'name'
(index 1) and 'symbol' (index 9, e.g. "NSE:TATAMOTORS-EQ") -- those are
unambiguous in every example I could verify. If Fyers ever reorders these
columns this will quietly return nothing (caught and reported), not wrong
data, so it fails safe.

As with etf_index_universe's Kite-based predecessor: the tickers are only
as good as this source file, but the *category* each one lands in is a
keyword heuristic and occasionally debatable -- a wrong category just means
a stock shows up in a slightly surprising group.
"""
from io import StringIO

import pandas as pd
import requests
import streamlit as st

SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
NAME_COL_IDX = 1
SYMBOL_COL_IDX = 9

CATEGORY_KEYWORDS = [
    ("Gold & Silver", ["GOLD", "SILVER"]),
    ("International", ["NASDAQ", "HANG SENG", "S&P 500", "FTSE", "DOW JONES",
                        "GLOBAL", "EUROPE", "CHINA", "JAPAN", "US ", "US50", "US TECH"]),
    ("Debt / Gilt / Liquid", ["GILT", "SDL", "LIQUID", "BOND", "G-SEC", "GSEC",
                              "OVERNIGHT", "MONEY MARKET", "BHARAT BOND", "CRISIL"]),
    ("Sectoral / Thematic", ["BANK", "FINSERV", " IT", "TECH", "PHARMA", "HEALTHCARE",
                              "AUTO", "FMCG", "METAL", "ENERGY", "REALTY", "PSU",
                              "INFRA", "CONSUMPTION", "MANUFACTURING", "DEFENCE",
                              "RAILWAY", "INTERNET", "MEDIA", "COMMODITIES", "CPSE"]),
    ("Smart Beta / Factor", ["ALPHA", "QUALITY", "LOW VOL", "VALUE", "MOMENTUM",
                              "EQUAL WEIGHT", "DIVIDEND", "FACTOR"]),
]


def classify_etf(name: str) -> str:
    name_u = f" {name.upper()} "
    for category, keywords in CATEGORY_KEYWORDS:
        if any(kw in name_u for kw in keywords):
            return category
    return "Broad Market / Index"


@st.cache_data(ttl=86400, show_spinner="Loading the full NSE ETF list (Fyers symbol master)...")
def fetch_etf_and_index_universe() -> dict:
    """
    No arguments, no login needed -- this is a public file.
    Returns {"etfs": {category: [{"symbol": "X.NS", "name": "..."}]}, "indices": []}.
    "indices" is empty for now: the NSE_CM file is the cash-market (equity +
    ETF) segment; indices aren't directly tradable symbols so they don't
    appear here the same way. Empty "etfs" if the fetch fails.
    """
    try:
        resp = requests.get(SYMBOL_MASTER_URL, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(StringIO(resp.text), header=None)
    except Exception as e:
        st.warning(f"Couldn't fetch the Fyers symbol master ({e}); using the smaller built-in ETF list instead.")
        return {"etfs": {}, "indices": []}

    if df.shape[1] <= max(NAME_COL_IDX, SYMBOL_COL_IDX):
        st.warning("The Fyers symbol master file has an unexpected shape; using the smaller built-in ETF list instead.")
        return {"etfs": {}, "indices": []}

    names = df[NAME_COL_IDX].astype(str)
    symbols = df[SYMBOL_COL_IDX].astype(str)
    is_etf = names.str.contains("ETF", case=False, na=False)

    etf_groups: dict = {}
    for name, sym in zip(names[is_etf], symbols[is_etf]):
        if not sym.startswith("NSE:") or not sym.endswith("-EQ"):
            continue  # skip anything that isn't a plain equity-segment ETF unit
        plain_symbol = sym.replace("NSE:", "").replace("-EQ", "") + ".NS"
        category = classify_etf(name)
        etf_groups.setdefault(category, []).append({"symbol": plain_symbol, "name": name})

    return {"etfs": etf_groups, "indices": []}
