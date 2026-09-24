import io
import re
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf



# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(
    page_title="Quantitative Strategy Engine & Backtest Studio",
    page_icon="📈",
    layout="wide",
)

st.markdown(
    """
    <style>
    .main {
        background-color: #F6F8FB;
    }

    .stMetric {
        background-color: #FFFFFF;
        padding: 14px;
        border-radius: 8px;
        border: 1px solid #E5E9F0;
    }

    div[data-testid="stVerticalBlockBorderWrapper"] {
        border-color: #E5E9F0;
        border-radius: 10px;
    }

    h1, h2, h3 {
        letter-spacing: -0.2px;
    }

    .status-active {
        color: #00875A;
        font-weight: bold;
        background-color: #E3FCEF;
        padding: 2px 8px;
        border-radius: 4px;
    }

    .status-paused {
        color: #DE350B;
        font-weight: bold;
        background-color: #FFEBE6;
        padding: 2px 8px;
        border-radius: 4px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ==========================================
# CONSTANTS
# ==========================================
NSE_HOME_URL = "https://www.nseindia.com/"
NSE_ETF_URL = "https://www.nseindia.com/api/allETFList"

DATA_URL_PARQUET = (
    "https://github.com/rishabhjain0295-web/"
    "nifty500-momentum-backtest/releases/download/"
    "data-v1/nifty500_historical.parquet"
)

DATA_URL_CSV = (
    "https://github.com/rishabhjain0295-web/"
    "nifty500-momentum-backtest/releases/download/"
    "data-v1/nifty500_historical.csv.gz"
)

NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

ETF_GROUP_NAMES = [
    "Domestic ETFs",
    "International ETFs",
    "All ETFs",
]

ETF_CATEGORY_LABELS = [
    "Equity",
    "Gold",
    "World Indices",
    "Debt",
    "Silver",
    "Other",
]

ETF_FALLBACK_TICKERS = [
    "GOLDBEES.NS",
    "LIQUIDBEES.NS",
    "SETFGSEC.NS",
    "NIFTYBEES.NS",
    "JUNIORBEES.NS",
    "BANKBEES.NS",
]

KITE_SECRET_NAMES = [
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_USER_ID",
    "KITE_PASSWORD",
    "KITE_TOTP_SECRET",
]


# ==========================================
# MARKET DATA
# ==========================================
def _period_to_days(period):
    return {
        "1d": 3, "5d": 10, "1mo": 31, "3mo": 93,
        "6mo": 186, "1y": 366, "2y": 731,
        "5y": 1826, "10y": 3652,
    }.get(period, 1826)


def _clean_tickers(tickers):
    cleaned = []
    for ticker in tickers:
        if ticker is None:
            continue
        ticker = str(ticker).strip().upper()
        if not ticker:
            continue
        if not ticker.endswith(".NS") and not ticker.startswith("^"):
            ticker = f"{ticker}.NS"
        if ticker not in cleaned:
            cleaned.append(ticker)
    return cleaned


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_data(tickers, period="5y"):
    """Fetch daily historical data from Yahoo Finance."""
    requested = _clean_tickers(tickers)
    if not requested:
        return pd.DataFrame()
    try:
        data = yf.download(
            tickers=requested, period=period, interval="1d",
            auto_adjust=True, progress=False, group_by="column",
            threads=True,
        )
        if data.empty:
            return pd.DataFrame()
        if isinstance(data.columns, pd.MultiIndex):
            level0 = data.columns.get_level_values(0)
            if "Close" in level0:
                prices = data["Close"].copy()
            elif "Adj Close" in level0:
                prices = data["Adj Close"].copy()
            else:
                return pd.DataFrame()
        else:
            if "Close" not in data.columns:
                return pd.DataFrame()
            prices = data[["Close"]].copy()
            if len(requested) == 1:
                prices.columns = [requested[0]]
        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=requested[0])
        prices.index = pd.to_datetime(prices.index).tz_localize(None) if getattr(prices.index, "tz", None) is not None else pd.to_datetime(prices.index)
        return prices.sort_index().ffill().bfill()
    except Exception:
        return pd.DataFrame()


# ==========================================
# NSE ETF UNIVERSE
# ==========================================
def _create_nse_session():
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/131 Safari/537.36"
            ),
            "Accept": "application/json,text/plain,*/*",
            "Referer": NSE_HOME_URL,
            "Origin": "https://www.nseindia.com",
        }
    )
    return session


def _extract_nse_rows(payload):
    if isinstance(payload, dict):
        rows = payload.get("data", payload)
    else:
        rows = payload

    if not isinstance(rows, list):
        return []

    return [
        row for row in rows
        if isinstance(row, dict)
    ]


def _get_first_value(row, keys):
    for key in keys:
        value = row.get(key)

        if value is not None and str(value).strip():
            return str(value).strip()

    metadata = row.get("meta")

    if isinstance(metadata, dict):
        for key in keys:
            value = metadata.get(key)

            if value is not None and str(value).strip():
                return str(value).strip()

    return ""


def _normalise_etf_symbol(symbol):
    symbol = str(symbol).strip().upper()

    if not symbol:
        return ""

    if symbol.endswith(".NS"):
        return symbol

    return f"{symbol}.NS"


def _normalise_nse_text(value):
    text = "" if value is None else str(value).upper()
    return re.sub(r"[^A-Z0-9]+", " ", text).strip()


def classify_etf(name, symbol, category="", sub_category=""):
    """Assign an ETF to the app's main universe groups.

    NSE's own category/sub-category fields are preferred. Name/symbol
    heuristics are only a fallback when those fields are missing.
    """
    text = _normalise_nse_text(
        f"{name} {symbol} {category} {sub_category}"
    )
    cat = _normalise_nse_text(category)
    sub = _normalise_nse_text(sub_category)

    gold_terms = ["GOLD", "GOLD ETF"]
    liquid_terms = ["LIQUID", "LIQUIDBEES", "OVERNIGHT", "MONEY MARKET"]
    gsec_terms = [
        "GSEC", "GILT", "GOVERNMENT", "GOVT", "TREASURY",
        "GOV BOND", "SOVEREIGN", "STATE DEVELOPMENT LOAN",
    ]
    international_terms = [
        "INTERNATIONAL", "WORLD", "GLOBAL", "NASDAQ", "S P 500",
        "MSCI", "HANG SENG", "CHINA", "JAPAN", "EUROPE", "USA",
        "UNITED STATES", "UNITED KINGDOM", "TAIWAN", "BRAZIL",
        "EMERGING MARKET", "NYSE", "GERMANY", "FRANCE", "KOREA",
        "DOW JONES", "FTSE", "SENSEX", "SP 500",
    ]

    if any(term in text for term in gold_terms) or "GOLD" in cat or "GOLD" in sub:
        return "Defensive ETFs"
    if any(term in text for term in liquid_terms + gsec_terms):
        return "Defensive ETFs"
    if any(term in text for term in international_terms):
        return "International ETFs"
    if "WORLD" in cat or "INTERNATIONAL" in cat:
        return "International ETFs"
    return "Domestic ETFs"


def classify_defensive_type(name, symbol, category="", sub_category=""):
    text = _normalise_nse_text(
        f"{name} {symbol} {category} {sub_category}"
    )
    if "GOLD" in text:
        return "Gold"
    if any(x in text for x in ["LIQUID", "LIQUIDBEES", "OVERNIGHT", "MONEY MARKET"]):
        return "LiquidBEES"
    if any(x in text for x in [
        "GSEC", "GILT", "GOVERNMENT", "GOVT", "TREASURY",
        "GOV BOND", "SOVEREIGN", "STATE DEVELOPMENT LOAN",
    ]):
        return "G-Sec"
    if any(x in text for x in ["DEBT", "BOND", "FIXED INCOME"]):
        return "Other Defensive"
    return ""


def _derive_etf_category(name, symbol, category="", sub_category=""):
    """Create a stable category label while retaining NSE's raw fields."""
    text = _normalise_nse_text(
        f"{name} {symbol} {category} {sub_category}"
    )
    raw = _normalise_nse_text(category)
    sub = _normalise_nse_text(sub_category)

    if "GOLD" in text:
        return "Gold"
    if "SILVER" in text:
        return "Silver"
    if any(x in text for x in [
        "GSEC", "GILT", "GOVERNMENT", "GOVT", "TREASURY",
        "BOND", "DEBT", "FIXED INCOME", "LIQUID", "OVERNIGHT",
        "MONEY MARKET", "SOVEREIGN",
    ]) or raw == "DEBT" or sub == "DEBT":
        return "Debt"
    if any(x in text for x in [
        "INTERNATIONAL", "WORLD", "GLOBAL", "NASDAQ", "MSCI",
        "HANG SENG", "CHINA", "JAPAN", "EUROPE", "USA",
        "UNITED STATES", "UNITED KINGDOM", "TAIWAN", "BRAZIL",
        "EMERGING MARKET", "FTSE", "DOW JONES", "KOREA",
    ]):
        return "World Indices"
    if raw in {"EQUITY", "EQUITY ETF"} or sub == "EQUITY":
        return "Equity"
    if "EQUITY" in text:
        return "Equity"
    return "Other"


@st.cache_data(ttl=86400, show_spinner=False)
def load_nse_etf_catalog():
    try:
        session = _create_nse_session()
        session.get(NSE_HOME_URL, timeout=15)

        response = session.get(
            NSE_ETF_URL,
            timeout=20,
        )

        response.raise_for_status()

        rows = _extract_nse_rows(response.json())
        records = []
        seen = set()

        for row in rows:
            symbol = _get_first_value(
                row,
                ["symbol", "Symbol", "SYMBOL"],
            )

            name = _get_first_value(
                row,
                [
                    "name",
                    "Name",
                    "companyName",
                    "companyname",
                    "securityName",
                    "securityname",
                ],
            )

            yahoo_symbol = _normalise_etf_symbol(symbol)

            if not yahoo_symbol or yahoo_symbol in seen:
                continue

            seen.add(yahoo_symbol)

            category = _get_first_value(
                row, ["category", "Category", "CATEGORY"]
            )
            sub_category = _get_first_value(
                row, [
                    "subCategory", "sub-category", "subcategory",
                    "Sub Category", "SUB-CATEGORY", "SUB_CATEGORY",
                ]
            )

            record = dict(row)
            record["symbol"] = yahoo_symbol
            record["name"] = name
            record["nse_category"] = category
            record["nse_sub_category"] = sub_category
            record["etf_category"] = _derive_etf_category(
                name, symbol, category, sub_category
            )
            record["group"] = classify_etf(
                name, symbol, category, sub_category
            )
            record["defensive_type"] = classify_defensive_type(
                name, symbol, category, sub_category
            )
            records.append(record)

        catalog = pd.DataFrame(records)
        if not catalog.empty:
            # Keep every field returned by NSE.  Put the fields used by the
            # strategy first, while preserving all other ETF metadata.
            preferred = ["symbol", "name", "etf_category", "nse_category", "nse_sub_category", "group", "defensive_type"]
            other = [c for c in catalog.columns if c not in preferred]
            catalog = catalog[preferred + other]

        if not catalog.empty:
            return catalog

    except Exception:
        pass

    return pd.DataFrame(
        [
            {
                "symbol": ticker,
                "name": "",
                "group": classify_etf("", ticker),
                "defensive_type": classify_defensive_type("", ticker),
            }
            for ticker in ETF_FALLBACK_TICKERS
        ]
    )


def get_etf_tickers(group=None):
    catalog = load_nse_etf_catalog()

    if group in ETF_GROUP_NAMES and group != "All ETFs":
        catalog = catalog[
            catalog["group"] == group
        ]

    if catalog.empty:
        return []

    return (
        catalog["symbol"]
        .dropna()
        .unique()
        .tolist()
    )


def get_etf_group_counts():
    catalog = load_nse_etf_catalog()

    counts = {
        "Defensive ETFs": 0,
        "Domestic ETFs": 0,
        "International ETFs": 0,
        "All ETFs": len(catalog),
    }

    if not catalog.empty:
        counts.update(
            catalog["group"].value_counts().to_dict()
        )

    return counts


def get_etf_group_catalog(group=None):
    catalog = load_nse_etf_catalog()

    if group in ETF_GROUP_NAMES and group != "All ETFs":
        catalog = catalog[
            catalog["group"] == group
        ]

    return catalog


def get_defensive_catalog(defensive_type=None):
    catalog = load_nse_etf_catalog()
    catalog = catalog[catalog["group"] == "Defensive ETFs"]
    if defensive_type:
        catalog = catalog[catalog["defensive_type"] == defensive_type]
    return catalog


@st.cache_data(ttl=86400, show_spinner=False)
def get_nifty_constituent_catalog():
    """Load official Nifty 500 and Nifty 200 constituent lists."""
    frames = []
    urls = {
        "Nifty 500": "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
        "Nifty 200": "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    }

    for group_name, url in urls.items():
        try:
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0"})
            response = session.get(url, timeout=15)
            response.raise_for_status()
            frame = pd.read_csv(io.StringIO(response.text))
            if "Symbol" not in frame.columns:
                continue
            frame = frame.copy()
            frame["Symbol"] = frame["Symbol"].astype(str).str.strip().str.upper()
            frame["Ticker"] = frame["Symbol"] + ".NS"
            frame["App Group"] = group_name
            frames.append(frame)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame(columns=["Symbol", "Ticker", "App Group"])

    return pd.concat(frames, ignore_index=True)


def get_group_membership_catalog():
    """Return a single table showing which stocks/ETFs belong to app groups."""
    rows = []

    constituent = get_nifty_constituent_catalog()
    if not constituent.empty:
        for _, row in constituent.iterrows():
            rows.append({
                "Group Type": "Stock Universe",
                "App Group": row.get("App Group", ""),
                "Symbol": row.get("Ticker", ""),
                "Name": row.get("Company Name", row.get("Company Name", "")),
                "Category": "Nifty constituent",
            })

    catalog = load_nse_etf_catalog()
    if not catalog.empty:
        for _, row in catalog.iterrows():
            rows.append({
                "Group Type": "ETF Universe",
                "App Group": row.get("group", ""),
                "Symbol": row.get("symbol", ""),
                "Name": row.get("name", ""),
                "Category": row.get("etf_category", ""),
                "Defensive Type": row.get("defensive_type", ""),
            })

    return pd.DataFrame(rows)


ASSET_GROUPS_MAPPING = {
    "Defensive ETFs": get_etf_tickers("Defensive ETFs"),
    "Domestic ETFs": get_etf_tickers("Domestic ETFs"),
    "International ETFs": get_etf_tickers(
        "International ETFs"
    ),
    "All ETFs": get_etf_tickers("All ETFs"),
    "Gold ETF": [],
    "LiquidBEES": [],
    "Gov Bond": [],
}

for ticker in ASSET_GROUPS_MAPPING["Defensive ETFs"]:
    upper_ticker = ticker.upper()

    if "GOLD" in upper_ticker:
        ASSET_GROUPS_MAPPING["Gold ETF"].append(ticker)

    if "LIQUID" in upper_ticker:
        ASSET_GROUPS_MAPPING["LiquidBEES"].append(ticker)

    if any(
        term in upper_ticker
        for term in [
            "GSEC",
            "GILT",
            "BOND",
            "TREASURY",
            "GOV",
        ]
    ):
        ASSET_GROUPS_MAPPING["Gov Bond"].append(ticker)

if not ASSET_GROUPS_MAPPING["Gold ETF"]:
    ASSET_GROUPS_MAPPING["Gold ETF"] = ["GOLDBEES.NS"]

if not ASSET_GROUPS_MAPPING["LiquidBEES"]:
    ASSET_GROUPS_MAPPING["LiquidBEES"] = ["LIQUIDBEES.NS"]

if not ASSET_GROUPS_MAPPING["Gov Bond"]:
    ASSET_GROUPS_MAPPING["Gov Bond"] = ["SETFGSEC.NS"]


# ==========================================
# MOMENTUM / RANKING ENGINE
# ==========================================
def calculate_simple_momentum(
    price_df,
    lookback_days=126,
):
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)

    return (
        price_df.iloc[-1] / price_df.iloc[-lookback_days]
    ) - 1.0


def calculate_volatility_adjusted_momentum(
    price_df,
    lookback_days=126,
):
    if len(price_df) < lookback_days:
        return pd.Series(dtype=float)

    subset = price_df.tail(lookback_days)
    daily_returns = subset.pct_change().dropna()
    total_return = (
        subset.iloc[-1] / subset.iloc[0]
    ) - 1.0

    annualized_vol = (
        daily_returns.std() * np.sqrt(252)
    )

    scores = {}
    x = np.arange(len(subset))

    for column in subset.columns:
        values = subset[column].values
        log_values = np.log(values)

        if (
            np.isnan(log_values).any()
            or annualized_vol[column] == 0
        ):
            scores[column] = 0.0
            continue

        correlation = np.corrcoef(
            x,
            log_values,
        )[0, 1]

        r_squared = (
            correlation ** 2
            if not np.isnan(correlation)
            else 0.0
        )

        scores[column] = (
            total_return[column]
            / annualized_vol[column]
        ) * r_squared

    return pd.Series(scores)


def get_momentum_scores(
    price_df,
    signal_type="Ranking",
    lookback_days=126,
):
    if signal_type == "Volatility-Adjusted":
        return calculate_volatility_adjusted_momentum(
            price_df,
            lookback_days,
        )

    return calculate_simple_momentum(
        price_df,
        lookback_days,
    )


def get_defensive_etf_tickers():
    tickers = []

    tickers.extend(
        ASSET_GROUPS_MAPPING.get("LiquidBEES", [])
    )

    tickers.extend(
        ASSET_GROUPS_MAPPING.get("Gov Bond", [])
    )

    return list(dict.fromkeys(tickers))


def rank_defensive_etfs_with_multipliers(
    prices_df,
    target_count,
    lookback_days=126,
    defensive_options=None,
):
    """Rank selected defensive assets using only the user-selected multipliers."""
    options = defensive_options or {
        "LiquidBEES": [1],
        "G-Sec": [1],
        "Gold": True,
    }

    rows = []
    groups = {
        "LiquidBEES": get_defensive_catalog("LiquidBEES"),
        "G-Sec": get_defensive_catalog("G-Sec"),
        "Gold": get_defensive_catalog("Gold"),
    }

    for defensive_type, catalog in groups.items():
        if catalog.empty:
            continue
        tickers = [t for t in catalog["symbol"].dropna().unique() if t in prices_df.columns]
        if not tickers:
            continue
        raw = calculate_simple_momentum(prices_df[tickers], lookback_days=lookback_days).dropna()
        if raw.empty:
            continue

        if defensive_type == "Gold":
            multipliers = [1] if options.get("Gold", False) else []
        else:
            multipliers = [int(x) for x in options.get(defensive_type, [])]

        for ticker in raw.index:
            for multiplier in multipliers:
                raw_return = float(raw[ticker])
                rows.append({
                    "Symbol": ticker,
                    "Defensive Type": defensive_type,
                    "Multiplier": multiplier,
                    "Raw Return": raw_return,
                    "Adjusted Return": raw_return * multiplier,
                })

    if not rows:
        return []

    ranked = pd.DataFrame(rows).sort_values(
        ["Adjusted Return", "Raw Return"], ascending=False
    )
    # A ticker is selected only once; its best selected multiplier is used.
    ranked = ranked.drop_duplicates(subset=["Symbol"], keep="first")
    return ranked.head(target_count).to_dict("records")


def is_regime_bullish(
    benchmark_series,
    current_date,
    sma_period=200,
):
    sub = benchmark_series.loc[:current_date].dropna()

    if len(sub) < sma_period:
        return True

    return sub.iloc[-1] >= sub.tail(sma_period).mean()


def apply_entry_exit_ranks(
    current_holdings,
    ranked_candidates,
    entry_rank,
    exit_rank,
):
    """
    Entry/exit rank logic:
    - Existing holdings are retained while their rank is <= exit_rank.
    - New positions are selected only from the top entry_rank.
    - The portfolio is filled up to entry_rank positions.
    """
    new_portfolio = []

    rank_map = {
        symbol: rank
        for rank, symbol in enumerate(ranked_candidates, start=1)
    }

    for stock in current_holdings:
        rank = rank_map.get(stock)
        if rank is not None and rank <= exit_rank:
            new_portfolio.append(stock)

    for stock in ranked_candidates[:entry_rank]:
        if len(new_portfolio) >= entry_rank:
            break
        if stock not in new_portfolio:
            new_portfolio.append(stock)

    return new_portfolio


def run_backtest_simulation(
    strat_config, initial_capital, start_date, end_date,
    price_df=None, benchmark_series=None, signal_type="Ranking",
):
    if price_df is None or price_df.empty:
        tickers = get_trusted_tickers_by_group(strat_config.get("groups", ["Nifty 500"]))
        price_df = fetch_market_data(tickers, period="5y")

    if price_df.empty:
        return None

    price_df = price_df.copy()
    price_df.index = pd.to_datetime(price_df.index)
    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date)
    warmup_start = start_ts - pd.Timedelta(days=420)
    work = price_df.loc[warmup_start:end_ts].copy()
    if len(work) < 252:
        return None

    # Only selected groups participate in the ranking.
    universe = get_trusted_tickers_by_group(strat_config.get("groups", ["Nifty 500"]))
    universe = [t for t in universe if t in work.columns]
    if not universe:
        return None
    work = work[universe].copy()

    try:
        rebalance_dates = work.loc[start_ts:end_ts].resample("ME").last().index
    except ValueError:
        rebalance_dates = work.loc[start_ts:end_ts].resample("M").last().index
    rebalance_dates = list(rebalance_dates)
    if not rebalance_dates:
        return None

    cash = float(initial_capital)
    holdings = {}
    portfolio_history = []
    entry_rank = int(strat_config.get("entry_rank", 10))
    exit_rank = int(strat_config.get("exit_rank", 20))
    ema_name = strat_config.get("moving_average", "200 EMA")
    ema_period = None if ema_name == "None" else int(str(ema_name).split()[0])
    pct_from_high = float(strat_config.get("pct_from_high", 15.0))
    defensive_options = strat_config.get("defensive_options", {"LiquidBEES": [1], "G-Sec": [1], "Gold": True})

    for current_date in rebalance_dates:
        history = work.loc[:current_date].dropna(axis=1, how="all")
        if len(history) < 126:
            continue

        latest = history.iloc[-1]
        candidates = history.columns.tolist()

        if ema_period is not None:
            ema = history.ewm(span=ema_period, adjust=False).mean().iloc[-1]
            candidates = [c for c in candidates if pd.notna(latest.get(c)) and pd.notna(ema.get(c)) and latest[c] > ema[c]]

        if pct_from_high > 0 and len(history) >= 252:
            highs = history.iloc[-252:].max()
            candidates = [c for c in candidates if pd.notna(highs.get(c)) and ((highs[c] - latest[c]) / highs[c] * 100) <= pct_from_high]

        ranking_prices = history[candidates] if candidates else history.iloc[:, 0:0]
        scores = get_momentum_scores(ranking_prices, signal_type=signal_type, lookback_days=126)
        ranked = scores.dropna().sort_values(ascending=False).index.tolist()

        selected = apply_entry_exit_ranks(list(holdings.keys()), ranked, entry_rank, exit_rank)

        if len(selected) < entry_rank:
            fallback_prices = history
            fallback = rank_defensive_etfs_with_multipliers(
                fallback_prices, entry_rank - len(selected), 126, defensive_options
            )
            for item in fallback:
                symbol = item["Symbol"]
                if symbol in history.columns and symbol not in selected:
                    selected.append(symbol)
                if len(selected) >= entry_rank:
                    break

        selected = selected[:entry_rank]

        # Rebalance equally among selected assets.
        current_value = cash
        for symbol, qty in holdings.items():
            if symbol in latest.index and pd.notna(latest[symbol]):
                current_value += qty * latest[symbol]

        new_holdings = {}
        new_cash = current_value
        if selected:
            per_asset = current_value / len(selected)
            for symbol in selected:
                px = latest.get(symbol)
                if pd.notna(px) and float(px) > 0:
                    qty = int(per_asset // float(px))
                    if qty > 0:
                        new_holdings[symbol] = qty
                        new_cash -= qty * float(px)
        holdings = new_holdings
        cash = new_cash

        portfolio_value = cash
        for symbol, qty in holdings.items():
            if symbol in latest.index and pd.notna(latest[symbol]):
                portfolio_value += qty * latest[symbol]
        if current_date >= start_ts:
            portfolio_history.append({"Date": current_date, "Portfolio Value": float(portfolio_value)})

    if not portfolio_history:
        return None
    return pd.DataFrame(portfolio_history).set_index("Date")


# ==========================================
# STOCK SCANNER
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_tickers_by_group(groups):
    """Build the scanner universe from official Nifty constituent files + NSE ETF catalogue."""
    tickers = []

    if "Nifty 500" in groups or "Nifty 200" in groups:
        constituents = get_nifty_constituent_catalog()
        if not constituents.empty:
            if "Nifty 500" in groups:
                tickers.extend(
                    constituents.loc[
                        constituents["App Group"] == "Nifty 500", "Ticker"
                    ].tolist()
                )
            if "Nifty 200" in groups:
                tickers.extend(
                    constituents.loc[
                        constituents["App Group"] == "Nifty 200", "Ticker"
                    ].tolist()
                )

    for group in ["Domestic ETFs", "International ETFs", "All ETFs"]:
        if group in groups:
            tickers.extend(get_etf_tickers(group))

    if not tickers:
        tickers = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
            "ICICIBANK.NS", "NIFTYBEES.NS",
        ]

    return sorted(set(tickers))


def run_strategy_stock_scanner(
    strat,
    price_subset=None,
):
    """
    Scan the selected stock/ETF universe.

    Core ranking:
      1. Ranking = raw 126-day momentum
      2. Volatility-Adjusted = momentum adjusted for annualised volatility and R²

    Portfolio rule:
      - Entry rank controls new entries.
      - Exit rank controls how far an existing holding can fall before exit.
      - If fewer than Entry Rank stocks qualify, selected defensive assets are
        automatically used as fallback assets.
    """
    selected_groups = strat.get("groups", ["Nifty 500"])

    if price_subset is None:
        group_tickers = get_trusted_tickers_by_group(selected_groups)
        group_tickers.extend(
            [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]
        )
        prices_df = fetch_market_data(group_tickers, period="2y")
    else:
        prices_df = price_subset

    if prices_df.empty:
        return []

    latest_prices = prices_df.iloc[-1]
    filtered_df = prices_df.copy()

    # EMA filter
    ma_config = strat.get("moving_average", "200 EMA")
    if ma_config != "None":
        if "200" in ma_config:
            period = 200
        elif "100" in ma_config:
            period = 100
        elif "50" in ma_config:
            period = 50
        elif "20" in ma_config:
            period = 20
        else:
            period = 10

        ma_values = (
            prices_df.ewm(span=period, adjust=False).mean().iloc[-1]
            if "EMA" in ma_config
            else prices_df.rolling(window=period).mean().iloc[-1]
        )

        valid_columns = latest_prices > ma_values
        filtered_df = filtered_df.loc[
            :,
            valid_columns[valid_columns.index.isin(filtered_df.columns)],
        ]

    # 52-week high retracement filter
    pct_high = float(strat.get("pct_from_high", 15.0))
    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = ((period_high - latest_prices) / period_high) * 100
        valid_columns = pct_diff <= pct_high
        filtered_df = filtered_df.loc[
            :,
            valid_columns[valid_columns.index.isin(filtered_df.columns)],
        ]

    # Relative-strength/regime check retained from the earlier strategy.
    if strat.get("use_rs", True):
        if (
            NIFTY_REGIME_TICKER in prices_df.columns
            and GSEC_REGIME_TICKER in prices_df.columns
        ):
            rs_ratio = (
                prices_df[NIFTY_REGIME_TICKER]
                / prices_df[GSEC_REGIME_TICKER]
            )
            rs_sma = rs_ratio.rolling(window=50).mean()

            if (
                not rs_ratio.dropna().empty
                and not rs_sma.dropna().empty
                and rs_ratio.iloc[-1] < rs_sma.iloc[-1]
            ):
                # Do not force G-Sec immediately. Treat it as a
                # defensive fallback below so qualified stocks still win.
                filtered_df = filtered_df.drop(
                    columns=[NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER],
                    errors="ignore",
                )

    # Standard stock/ETF ranking.
    filtered_df = filtered_df.drop(
        columns=[NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER],
        errors="ignore",
    )

    signal_model = strat.get("signal_type", "Ranking")
    scores = get_momentum_scores(
        filtered_df,
        signal_type=signal_model,
        lookback_days=126,
    )

    ranked = (
        scores.sort_values(ascending=False)
        .dropna()
        .index
        .tolist()
    )

    entry_rank = int(strat.get("entry_rank", 10))
    stock_selection = ranked[:entry_rank]

    # Automatic defensive fallback only when there are not enough
    defensive_target = max(0, entry_rank - len(stock_selection))
    defensive_results = []
    if defensive_target > 0:
        defensive_results = rank_defensive_etfs_with_multipliers(
            prices_df=prices_df,
            target_count=defensive_target,
            lookback_days=126,
            defensive_options=strat.get("defensive_options"),
        )

        for result in defensive_results:
            symbol = result["Symbol"]
            if symbol not in stock_selection:
                stock_selection.append(symbol)

    st.session_state["last_defensive_ranking"] = defensive_results
    st.session_state["last_scan_stock_count"] = len(ranked)
    st.session_state["last_scan_fallback_count"] = len(defensive_results)

    return stock_selection[:entry_rank]


def get_current_holding_symbols(strat):
    return [
        position["Symbol"]
        for position in strat.get("positions", [])
        if position.get("Symbol")
    ]


def compare_portfolio_symbols(current_symbols, proposed_symbols):
    current = list(dict.fromkeys(current_symbols))
    proposed = list(dict.fromkeys(proposed_symbols))
    return {
        "added": [s for s in proposed if s not in current],
        "removed": [s for s in current if s not in proposed],
        "changed": current != proposed,
    }


def show_portfolio_change_alert(strat_name, strat, proposed_symbols, source):
    current_symbols = get_current_holding_symbols(strat)
    change = compare_portfolio_symbols(current_symbols, proposed_symbols)

    if not change["changed"]:
        st.info(
            f"No portfolio change detected on this {source.lower()} scan."
        )
        return change

    if change["added"]:
        st.success(
            "Added: " + ", ".join(s.replace(".NS", "") for s in change["added"])
        )
    if change["removed"]:
        st.warning(
            "Exit candidates: "
            + ", ".join(s.replace(".NS", "") for s in change["removed"])
        )

    st.session_state["last_portfolio_alert"] = {
        "strategy": strat_name,
        "source": source,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "added": change["added"],
        "removed": change["removed"],
    }

    st.toast(
        f"Portfolio changed: {len(change['added'])} added, "
        f"{len(change['removed'])} removed",
        icon="🔔",
    )
    return change


# ==========================================
# STRATEGY METRICS
# ==========================================
def calculate_days_to_rebalance(rebalance_day):
    today = datetime.now()

    target_date = datetime(
        today.year,
        today.month,
        min(rebalance_day, 28),
    )

    if target_date < today:
        if today.month == 12:
            target_date = datetime(
                today.year + 1,
                1,
                min(rebalance_day, 28),
            )
        else:
            target_date = datetime(
                today.year,
                today.month + 1,
                min(rebalance_day, 28),
            )

    return (target_date - today).days


def calculate_strategy_metrics(strat):
    positions = strat.get("positions", [])
    realized_pnl = strat.get("realized_pnl", 0.0)
    allocated = strat["allocated"]

    unrealized_pnl = 0.0
    current_holding_value = 0.0
    total_cost_basis = 0.0
    positions_data = []

    if positions and strat["status"] == "Active":
        symbols = [
            position["Symbol"]
            for position in positions
        ]

        prices_df = fetch_market_data(
            symbols,
            period="1mo",
        )

        for position in positions:
            symbol = position["Symbol"]
            quantity = position["Buy Qty"]
            buy_price = position["Buy Price"]

            if (
                not prices_df.empty
                and symbol in prices_df.columns
            ):
                current_price = float(
                    prices_df[symbol]
                    .dropna()
                    .iloc[-1]
                )
            else:
                current_price = buy_price

            cost_value = buy_price * quantity
            current_value = current_price * quantity
            position_pnl = current_value - cost_value

            position_pnl_pct = (
                ((current_price - buy_price) / buy_price)
                * 100
                if buy_price > 0
                else 0.0
            )

            total_cost_basis += cost_value
            unrealized_pnl += position_pnl
            current_holding_value += current_value

            positions_data.append(
                {
                    "Symbol": symbol.replace(".NS", ""),
                    "Buy Qty": quantity,
                    "Buy Price": f"₹{buy_price:,.2f}",
                    "Entry Date": position["Entry Date"],
                    "CMP": f"₹{current_price:,.2f}",
                    "Current Value": f"₹{current_value:,.2f}",
                    "Current P&L": f"₹{position_pnl:,.2f}",
                    "Current P&L %": f"{position_pnl_pct:.2f}%",
                }
            )

    cash_reserve = (
        max(0.0, allocated - total_cost_basis)
        if total_cost_basis > 0
        else allocated
    )

    total_pnl = realized_pnl + unrealized_pnl

    returns_pct = (
        total_pnl / allocated * 100
        if allocated > 0
        else 0.0
    )

    current_total_value = (
        cash_reserve + current_holding_value + realized_pnl
    )

    return {
        "allocated": allocated,
        "cash_reserve": cash_reserve,
        "current_holding_val": current_holding_value,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "returns_pct": returns_pct,
        "current_total_value": current_total_value,
        "positions_data": positions_data,
    }


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
        "Strategy 1": {
            "created_date": (
                datetime.now() - timedelta(days=43)
            ).strftime("%Y-%m-%d"),
            "status": "Active",
            "type": "Real",
            "allocated": 100000.0,
            "realized_pnl": 0.0,
            "rebalance_freq": "Monthly",
            "rebalance_day": 1,
            "groups": [
                "Nifty 500",
                "All ETFs",
            ],
            "entry_rank": 10,
            "exit_rank": 20,
            "pct_from_high": 15.0,
            "pct_from_low": 0.0,
            "moving_average": "200 EMA",
            "defensive_options": {"LiquidBEES": [1], "G-Sec": [1], "Gold": True},
            "use_rs": True,
            "rs_benchmark": "Nifty 500 / G-Sec",
            "signal_type": "Ranking",
            "positions": [],
        }
    }


# ==========================================
# NAVIGATION HEADER
# ==========================================
nav_col1, nav_col2, nav_col3, nav_col4 = st.columns([1.5, 2, 1.5, 1.2])

with nav_col1:
    if st.button(
        "💻 DASHBOARD",
        use_container_width=True,
        type=(
            "primary"
            if st.session_state.navigation_tab == "DASHBOARD"
            else "secondary"
        ),
    ):
        st.session_state.navigation_tab = "DASHBOARD"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col2:
    if st.button(
        "➕ INVESTING STRATEGY",
        use_container_width=True,
        type=(
            "primary"
            if st.session_state.navigation_tab == "STRATEGY_BUILDER"
            else "secondary"
        ),
    ):
        st.session_state.navigation_tab = "STRATEGY_BUILDER"
        st.session_state.editing_strategy_name = None
        st.rerun()

with nav_col3:
    if st.button(
        "📈 BACKTEST ENGINE",
        use_container_width=True,
        type=(
            "primary"
            if st.session_state.navigation_tab == "BACKTEST"
            else "secondary"
        ),
    ):
        st.session_state.navigation_tab = "BACKTEST"
        st.rerun()

with nav_col4:
    if st.button("⋯ MORE", use_container_width=True, type=("primary" if st.session_state.navigation_tab == "MORE" else "secondary")):
        st.session_state.navigation_tab = "MORE"
        st.rerun()

st.markdown("---")


# ==========================================
# BACKTEST DATASET
# ==========================================
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_data_chunked(tickers, period="5y", chunk_size=50):
    """Fetch larger universes in smaller Yahoo Finance batches."""
    requested = _clean_tickers(tickers)
    frames = []
    for start in range(0, len(requested), chunk_size):
        chunk = requested[start:start + chunk_size]
        data = fetch_market_data(chunk, period=period)
        if not data.empty:
            frames.append(data)
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, axis=1)
    return result.loc[:, ~result.columns.duplicated()].sort_index().ffill().bfill()


@st.cache_data(ttl=86400, show_spinner=False)
def load_historical_release_dataset():
    """Load the public Nifty 500 historical dataset used by the backtest."""
    try:
        response = requests.get(DATA_URL_PARQUET, timeout=30)
        response.raise_for_status()
        frame = pd.read_parquet(io.BytesIO(response.content))
    except Exception:
        try:
            response = requests.get(DATA_URL_CSV, timeout=30)
            response.raise_for_status()
            frame = pd.read_csv(io.BytesIO(response.content))
        except Exception:
            return pd.DataFrame()

    if frame.empty:
        return frame

    # Normalize the date index.
    date_column = next((c for c in frame.columns if str(c).lower() in {"date", "datetime"}), None)
    if date_column is not None:
        frame[date_column] = pd.to_datetime(frame[date_column], errors="coerce")
        frame = frame.dropna(subset=[date_column]).set_index(date_column)
    elif not isinstance(frame.index, pd.DatetimeIndex):
        frame.index = pd.to_datetime(frame.index, errors="coerce")
        frame = frame[~frame.index.isna()]

    frame.index = pd.to_datetime(frame.index).tz_localize(None) if getattr(frame.index, "tz", None) is not None else pd.to_datetime(frame.index)
    frame = frame.sort_index()

    # Flatten possible MultiIndex columns.
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[-1] if str(c[-1]) not in {"Close", "Adj Close"} else c[0]) for c in frame.columns]

    # Keep close-price columns and normalize NSE symbols.
    normalized = {}
    for col in frame.columns:
        name = str(col).strip().upper()
        if name in {"DATE", "DATETIME"}:
            continue
        if name.endswith(".NS") or name.startswith("^"):
            normalized[name] = frame[col]
        else:
            normalized[f"{name}.NS"] = frame[col]

    result = pd.DataFrame(normalized, index=frame.index)
    return result.apply(pd.to_numeric, errors="coerce").sort_index().ffill().bfill()




# ==========================================
# DASHBOARD
# ==========================================
if st.session_state.navigation_tab == "DASHBOARD":
    if st.session_state.active_strategy_view is not None:
        strat_name = st.session_state.active_strategy_view
        strat = st.session_state.strategies.get(strat_name)

        if not strat:
            st.session_state.active_strategy_view = None
            st.rerun()

        metrics = calculate_strategy_metrics(strat)
        days_left = calculate_days_to_rebalance(
            strat.get("rebalance_day", 1)
        )

        top_col1, _, top_col3 = st.columns([2, 5, 3])

        with top_col1:
            if st.button("❮ Back to Dashboard"):
                st.session_state.active_strategy_view = None
                st.rerun()

        with top_col3:
            current_status = strat["status"]
            button_label = (
                "⏸️ Pause Strategy"
                if current_status == "Active"
                else "▶️ Start Strategy"
            )

            if st.button(button_label):
                strat["status"] = (
                    "Paused"
                    if current_status == "Active"
                    else "Active"
                )
                st.rerun()

        st.subheader(f"Strategy: {strat_name}")

        status_class = (
            "status-active"
            if strat["status"] == "Active"
            else "status-paused"
        )

        st.markdown(
            f"Status: <span class='{status_class}'>"
            f"{strat['status']}</span> | **Groups:** "
            f"{', '.join(strat.get('groups', []))} | "
            f"**Entry Rank:** {strat.get('entry_rank', 10)} | "
            f"**Exit Rank:** {strat.get('exit_rank', 20)} | "
            f"**Rebalance Day:** {strat.get('rebalance_day', 1)} | "
            f"**Days left:** {days_left}",
            unsafe_allow_html=True,
        )

        m1, m2, m3, m4 = st.columns(4)

        m1.metric("Allocated Capital", f"₹{metrics['allocated']:,.2f}")
        m2.metric("Uninvested Cash", f"₹{metrics['cash_reserve']:,.2f}")
        m3.metric("Unrealized P&L", f"₹{metrics['unrealized_pnl']:,.2f}")
        m4.metric("Total P&L & Returns", f"₹{metrics['total_pnl']:,.2f}", delta=f"{metrics['returns_pct']:.2f}%")

        st.markdown("---")
        st.subheader("🔍 Live Stock Scanner & Rebalance Engine")

        # Automatic daily check on the scheduled rebalance day.
        # Streamlit reruns this page whenever the app is opened/refreshed
        # or an interaction occurs; the same day is checked only once.
        today_key = datetime.now().strftime("%Y-%m-%d")
        if (
            strat.get("status") == "Active"
            and days_left == 0
            and strat.get("last_auto_check") != today_key
        ):
            with st.spinner("Scheduled rebalance check..."):
                auto_scanned = run_strategy_stock_scanner(strat)
                st.session_state[f"scanned_results_{strat_name}"] = auto_scanned
                st.session_state[f"scan_timestamp_{strat_name}"] = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                st.session_state[f"scan_alert_pending_{strat_name}"] = True
                strat["last_auto_check"] = today_key

        if st.button(
            "🚀 Run Live Stock Scan for this Strategy",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Running scanner..."):
                scanned = run_strategy_stock_scanner(strat)
                st.session_state[f"scanned_results_{strat_name}"] = scanned
                st.session_state[f"scan_timestamp_{strat_name}"] = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                st.session_state[f"scan_alert_pending_{strat_name}"] = True

        scanned_results = st.session_state.get(f"scanned_results_{strat_name}")

        last_scan_time = st.session_state.get(
            f"scan_timestamp_{strat_name}"
        )
        if last_scan_time:
            st.caption(f"Last scan: {last_scan_time}")

        if (
            scanned_results is not None
            and st.session_state.get(
                f"scan_alert_pending_{strat_name}",
                False,
            )
        ):
            show_portfolio_change_alert(
                strat_name,
                strat,
                scanned_results,
                "Manual / Rebalance",
            )
            st.session_state[f"scan_alert_pending_{strat_name}"] = False
            st.success(
                f"Scan complete. Found **{len(scanned_results)}** target assets."
            )
            st.caption(
                f"Qualifying securities: "
                f"{st.session_state.get('last_scan_stock_count', 0)} | "
                f"Defensive fallback: "
                f"{st.session_state.get('last_scan_fallback_count', 0)}"
            )

            sc_col1, sc_col2 = st.columns(2)

            with sc_col1:
                st.markdown("#### 📌 Current Holdings")
                if metrics["positions_data"]:
                    st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
                else:
                    st.info("No active holdings.")

            with sc_col2:
                st.markdown("#### 🔄 Scanned Portfolio")
                prices_df = fetch_market_data(scanned_results, period="5d")

                rows = []

                for ticker in scanned_results:
                    if not prices_df.empty and ticker in prices_df.columns:
                        current_price = float(prices_df[ticker].dropna().iloc[-1])
                    else:
                        current_price = 0.0

                    rows.append(
                        {
                            "Symbol": ticker.replace(".NS", ""),
                            "CMP": f"₹{current_price:,.2f}",
                            "Status": "Entry Target",
                        }
                    )

                st.dataframe(pd.DataFrame(rows), use_container_width=True)

            defensive_ranking = st.session_state.get("last_defensive_ranking", [])

            if defensive_ranking:
                st.markdown("#### Defensive ETF Ranking Details")

                ranking_rows = []

                for result in defensive_ranking:
                    ranking_rows.append(
                        {
                            "Symbol": result["Symbol"].replace(".NS", ""),
                            "Raw Return": f"{result['Raw Return'] * 100:.2f}%",
                            "Ranking Multiplier": f"{result['Multiplier']}x",
                            "Adjusted Return": f"{result['Adjusted Return'] * 100:.2f}%",
                        }
                    )

                st.dataframe(pd.DataFrame(ranking_rows), use_container_width=True)

            if st.button("⚡ Execute Rebalance & Reinvest Capital", type="primary", use_container_width=True):
                reinvest_pool = metrics["current_total_value"]
                allocation_per_stock = (
                    reinvest_pool / len(scanned_results)
                    if scanned_results
                    else 0.0
                )

                prices_df = fetch_market_data(scanned_results, period="5d")
                new_positions = []

                for ticker in scanned_results:
                    if not prices_df.empty and ticker in prices_df.columns:
                        current_price = float(prices_df[ticker].dropna().iloc[-1])
                    else:
                        current_price = 0.0

                    quantity = int(allocation_per_stock // current_price) if current_price > 0 else 0

                    if quantity > 0:
                        new_positions.append(
                            {
                                "Symbol": ticker,
                                "Buy Qty": quantity,
                                "Buy Price": current_price,
                                "Entry Date": datetime.now().strftime("%Y-%m-%d"),
                            }
                        )

                old_symbols = get_current_holding_symbols(strat)
                new_symbols = [
                    position["Symbol"]
                    for position in new_positions
                ]

                strat["positions"] = new_positions
                strat["realized_pnl"] = metrics["total_pnl"]
                strat["last_rebalance"] = datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                strat["last_rebalance_symbols"] = new_symbols

                st.session_state[f"scanned_results_{strat_name}"] = None

                change = compare_portfolio_symbols(
                    old_symbols,
                    new_symbols,
                )

                if change["changed"]:
                    st.toast(
                        f"Rebalance completed: "
                        f"{len(change['added'])} added, "
                        f"{len(change['removed'])} removed",
                        icon="🔔",
                    )

                st.success(
                    f"Rebalanced across {len(new_positions)} assets."
                )
                st.rerun()

        else:
            st.markdown("#### 📋 Current Holdings")
            if metrics["positions_data"]:
                st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
            else:
                st.info("No active holdings. Run the scanner.")

    else:
        st.title("MY PORTFOLIO")

        total_allocated = 0.0
        total_holding_value = 0.0
        total_realized_pnl = 0.0
        total_unrealized_pnl = 0.0

        for strategy in st.session_state.strategies.values():
            metrics = calculate_strategy_metrics(strategy)
            total_allocated += metrics["allocated"]
            total_holding_value += metrics["current_holding_val"]
            total_realized_pnl += metrics["realized_pnl"]
            total_unrealized_pnl += metrics["unrealized_pnl"]

        total_pnl = total_realized_pnl + total_unrealized_pnl

        total_return_pct = (
            total_pnl / total_allocated * 100
            if total_allocated > 0
            else 0.0
        )

        total_portfolio_value = total_allocated + total_pnl

        kpi1, kpi2, kpi3 = st.columns(3)

        kpi1.metric("Total Allocated Capital", f"₹{total_allocated:,.2f}")
        kpi2.metric("Total Realized P&L", f"₹{total_realized_pnl:,.2f}")
        kpi3.metric("Total Portfolio P&L", f"₹{total_pnl:,.2f}", delta=f"{total_return_pct:.2f}%")

        kpi4, kpi5, kpi6 = st.columns(3)

        kpi4.metric("Current Holdings Value", f"₹{total_holding_value:,.2f}")
        kpi5.metric("Total Unrealized P&L", f"₹{total_unrealized_pnl:,.2f}")
        kpi6.metric("Total Portfolio Balance", f"₹{total_portfolio_value:,.2f}")

        st.markdown("---")
        st.subheader("Real Strategies")

        strategy_columns = st.columns(3)

        for i, name in enumerate(st.session_state.strategies.keys()):
            strat = st.session_state.strategies[name]
            metrics = calculate_strategy_metrics(strat)
            days_left = calculate_days_to_rebalance(strat.get("rebalance_day", 1))
            column = strategy_columns[i % 3]

            with column:
                with st.container(border=True):
                    c_head1, c_head2 = st.columns([3, 2])

                    with c_head1:
                        st.markdown(f"### **{name}**")
                        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
                        st.markdown(f"<span class='{status_class}'>{strat['status']}</span>", unsafe_allow_html=True)

                    with c_head2:
                        action1, action2, action3 = st.columns(3)

                        with action1:
                            if st.button("⏸️" if strat["status"] == "Active" else "▶️", key=f"toggle_{name}"):
                                strat["status"] = "Paused" if strat["status"] == "Active" else "Active"
                                st.rerun()

                        with action2:
                            if st.button("✏️", key=f"edit_{name}"):
                                st.session_state.editing_strategy_name = name
                                st.session_state.navigation_tab = "STRATEGY_BUILDER"
                                st.rerun()

                        with action3:
                            if st.button("🗑️", key=f"del_{name}"):
                                del st.session_state.strategies[name]
                                st.rerun()

                    st.caption(f"Groups: {', '.join(strat.get('groups', ['Nifty 500']))}")
                    st.caption(
                        f"Rebalance Date: Day {strat.get('rebalance_day', 1)} | "
                        f"{days_left} days left"
                    )
                    if strat.get("last_rebalance"):
                        st.caption(
                            f"Last rebalance: {strat.get('last_rebalance')}"
                        )
                    st.markdown(f"**Allocated:** ₹{metrics['allocated']:,.2f}")
                    st.markdown(f"**Realized P&L:** ₹{metrics['realized_pnl']:,.2f}")
                    st.markdown(f"**Unrealized P&L:** ₹{metrics['unrealized_pnl']:,.2f}")

                    pnl_color = "green" if metrics["total_pnl"] >= 0 else "red"
                    st.markdown(f"**P&L / Returns:** <span style='color:{pnl_color}; font-weight:bold;'>₹{metrics['total_pnl']:,.2f} ({metrics['returns_pct']:.2f}%)</span>", unsafe_allow_html=True)

                    if st.button("🔍 Open Scanner & Details", key=f"view_detail_{name}", use_container_width=True):
                        st.session_state.active_strategy_view = name
                        st.rerun()


# ==========================================
# STRATEGY BUILDER
# ==========================================
elif st.session_state.navigation_tab == "STRATEGY_BUILDER":
    edit_mode = st.session_state.editing_strategy_name is not None
    edit_strat = st.session_state.strategies.get(st.session_state.editing_strategy_name, {}) if edit_mode else {}

    st.subheader("✏️ EDIT STRATEGY ENGINE" if edit_mode else "➕ CREATE STRATEGY ENGINE")

    inv1, inv2, inv3 = st.columns(3)

    with inv1:
        strategy_type = st.radio(
            "Strategy Type:",
            ["Real", "Virtual"],
            index=0 if edit_strat.get("type") == "Real" else 1,
            horizontal=True,
        )

        default_name = (
            st.session_state.editing_strategy_name
            if edit_mode
            else "New Momentum Strategy"
        )
        strategy_name_input = st.text_input(
            "Strategy Name:",
            value=default_name,
            disabled=edit_mode,
        )

    with inv2:
        allocated = st.number_input(
            "Total Allocation Capital (₹):",
            value=float(edit_strat.get("allocated", 100000.0)),
            step=10000.0,
        )
        rebalance_frequency = st.selectbox(
            "Rebalance Frequency:",
            ["Monthly", "Weekly", "Quarterly"],
            index=["Monthly", "Weekly", "Quarterly"].index(
                edit_strat.get("rebalance_freq", "Monthly")
            ),
        )

    with inv3:
        rebalance_day = st.number_input(
            "Rebalance Day (1-28):",
            value=int(edit_strat.get("rebalance_day", 1)),
            min_value=1,
            max_value=28,
        )

    st.markdown("---")
    st.markdown("#### Group 1 — Main Ranking Universe")
    existing_groups = edit_strat.get("groups", ["Nifty 500"])

    g1 = st.columns(5)
    with g1[0]:
        nifty500 = st.checkbox("Nifty 500", value="Nifty 500" in existing_groups)
    with g1[1]:
        nifty200 = st.checkbox("Nifty 200", value="Nifty 200" in existing_groups)
    with g1[2]:
        domestic = st.checkbox("Domestic ETF", value="Domestic ETFs" in existing_groups)
    with g1[3]:
        international = st.checkbox("International ETF", value="International ETFs" in existing_groups)
    with g1[4]:
        all_etfs = st.checkbox("All ETFs", value="All ETFs" in existing_groups)

    st.markdown("#### Group 2 — Defensive Ranking")
    existing_def = edit_strat.get("defensive_options", {"LiquidBEES": [1], "G-Sec": [1], "Gold": True})
    d1, d2, d3 = st.columns(3)
    with d1:
        st.markdown("**LiquidBEES**")
        liquid_1x = st.checkbox("1x", value=1 in existing_def.get("LiquidBEES", []), key="def_liq_1")
        liquid_2x = st.checkbox("2x", value=2 in existing_def.get("LiquidBEES", []), key="def_liq_2")
        liquid_3x = st.checkbox("3x", value=3 in existing_def.get("LiquidBEES", []), key="def_liq_3")
    with d2:
        st.markdown("**G-Sec ETF**")
        gsec_1x = st.checkbox("1x", value=1 in existing_def.get("G-Sec", []), key="def_gsec_1")
        gsec_2x = st.checkbox("2x", value=2 in existing_def.get("G-Sec", []), key="def_gsec_2")
        gsec_3x = st.checkbox("3x", value=3 in existing_def.get("G-Sec", []), key="def_gsec_3")
    with d3:
        gold_defensive = st.checkbox("Gold ETF", value=bool(existing_def.get("Gold", True)), key="def_gold")

    with st.expander("📚 ETF Catalogue & Group Membership", expanded=False):
        catalog = get_etf_group_catalog()
        if not catalog.empty:
            category_values = [
                x for x in catalog.get("etf_category", pd.Series(dtype=str)).dropna().unique()
            ]
            category_tabs = ["All ETFs"] + sorted(category_values)
            tabs = st.tabs(category_tabs)
            for tab, category in zip(tabs, category_tabs):
                with tab:
                    if category == "All ETFs":
                        frame = catalog
                    else:
                        frame = catalog[catalog["etf_category"] == category]
                    st.dataframe(frame, use_container_width=True, hide_index=True)

        st.markdown("### App Group Membership")
        membership = get_group_membership_catalog()
        if not membership.empty:
            membership_tabs = st.tabs([
                "Nifty 500", "Nifty 200", "Domestic ETFs",
                "International ETFs", "All ETFs", "Defensive ETFs",
            ])
            membership_groups = [
                "Nifty 500", "Nifty 200", "Domestic ETFs",
                "International ETFs", "All ETFs", "Defensive ETFs",
            ]
            for tab, group in zip(membership_tabs, membership_groups):
                with tab:
                    if group == "All ETFs":
                        frame = membership[membership["Group Type"] == "ETF Universe"]
                    elif group == "Defensive ETFs":
                        frame = membership[
                            (membership["Group Type"] == "ETF Universe")
                            & (membership["App Group"] == "Defensive ETFs")
                        ]
                    else:
                        frame = membership[membership["App Group"] == group]
                    st.write(f"**{len(frame):,} securities** in {group}")
                    st.dataframe(frame, use_container_width=True, hide_index=True)
        else:
            st.warning("Group membership data could not be loaded right now.")

    st.markdown("---")
    st.markdown("#### Selection Criteria")

    f1, f2, f3 = st.columns(3)

    with f1:
        moving_average = st.selectbox(
            "EMA Filter:",
            ["None", "10 EMA", "20 EMA", "50 EMA", "100 EMA", "200 EMA"],
            index=(
                ["None", "10 EMA", "20 EMA", "50 EMA", "100 EMA", "200 EMA"].index(
                    edit_strat.get("moving_average", "200 EMA")
                )
                if edit_strat.get("moving_average", "200 EMA")
                in ["None", "10 EMA", "20 EMA", "50 EMA", "100 EMA", "200 EMA"]
                else 3
            ),
        )

    with f2:
        pct_from_high = st.number_input(
            "Max Retracement from 52-Week High (%):",
            value=float(edit_strat.get("pct_from_high", 15.0)),
            min_value=0.0,
            max_value=100.0,
            step=1.0,
        )

    with f3:
        signal_type = st.selectbox(
            "Momentum Signal:",
            ["Ranking", "Volatility-Adjusted"],
            index=(
                0
                if edit_strat.get("signal_type", "Ranking") == "Ranking"
                else 1
            ),
        )

    st.markdown("---")
    st.markdown("#### Ranking & Execution")

    r1, r2, r3 = st.columns(3)

    with r1:
        entry_rank = st.number_input(
            "Entry Rank:",
            value=int(edit_strat.get("entry_rank", 10)),
            min_value=1,
            max_value=50,
        )

    with r2:
        exit_rank = st.number_input(
            "Exit Rank:",
            value=int(edit_strat.get("exit_rank", 20)),
            min_value=1,
            max_value=100,
        )


    st.markdown("---")
    save_label = (
        "💾 Update Strategy Config"
        if edit_mode
        else "💾 Deploy New Strategy Profile"
    )

    if st.button(
        save_label,
        type="primary",
        use_container_width=True,
    ):
        selected_groups = []

        if nifty500:
            selected_groups.append("Nifty 500")
        if nifty200:
            selected_groups.append("Nifty 200")
        if domestic:
            selected_groups.append("Domestic ETFs")
        if international:
            selected_groups.append("International ETFs")
        if all_etfs:
            selected_groups.append("All ETFs")

        defensive_options = {
            "LiquidBEES": [x for x, enabled in [(1, liquid_1x), (2, liquid_2x), (3, liquid_3x)] if enabled],
            "G-Sec": [x for x, enabled in [(1, gsec_1x), (2, gsec_2x), (3, gsec_3x)] if enabled],
            "Gold": gold_defensive,
        }

        target_name = (
            st.session_state.editing_strategy_name
            if edit_mode
            else strategy_name_input
        )

        # Keep Exit Rank at or above Entry Rank for a sensible
        # hysteresis rule.
        if exit_rank < entry_rank:
            st.error("Exit Rank should be greater than or equal to Entry Rank.")
            st.stop()

        st.session_state.strategies[target_name] = {
            "created_date": edit_strat.get(
                "created_date",
                datetime.now().strftime("%Y-%m-%d"),
            ),
            "status": edit_strat.get("status", "Active"),
            "type": strategy_type,
            "allocated": float(allocated),
            "realized_pnl": edit_strat.get("realized_pnl", 0.0),
            "rebalance_freq": rebalance_frequency,
            "rebalance_day": rebalance_day,
            "groups": selected_groups if selected_groups else ["Nifty 500"],
            "entry_rank": int(entry_rank),
            "exit_rank": int(exit_rank),
            "signal_type": signal_type,
            "pct_from_high": float(pct_from_high),
            "moving_average": moving_average,
            "defensive_options": defensive_options,
            "use_rs": True,
            "rs_benchmark": "Nifty 500 / G-Sec",
            "positions": edit_strat.get("positions", []),
        }

        st.session_state.editing_strategy_name = None
        st.session_state.navigation_tab = "DASHBOARD"
        st.success("Strategy saved.")
        st.rerun()



# ==========================================
# MORE / GROUPS
# ==========================================
elif st.session_state.navigation_tab == "MORE":
    st.title("⋯ MORE")
    st.subheader("Group Membership")

    membership = get_group_membership_catalog()
    if membership.empty:
        st.warning("Group membership data could not be loaded right now.")
    else:
        group_names = ["Nifty 500", "Nifty 200", "Domestic ETFs", "International ETFs", "All ETFs", "Defensive ETFs"]
        tabs = st.tabs(group_names)
        for tab, group in zip(tabs, group_names):
            with tab:
                if group == "All ETFs":
                    frame = membership[membership["Group Type"] == "ETF Universe"]
                elif group == "Defensive ETFs":
                    frame = membership[(membership["Group Type"] == "ETF Universe") & (membership["App Group"] == "Defensive ETFs")]
                else:
                    frame = membership[membership["App Group"] == group]
                st.metric("Members", len(frame))
                st.dataframe(frame, use_container_width=True, hide_index=True)

    st.markdown("### ETF Categories")
    catalog = load_nse_etf_catalog()
    if not catalog.empty:
        categories = sorted(catalog["etf_category"].dropna().unique().tolist())
        category_tabs = st.tabs(["All"] + categories)
        for tab, category in zip(category_tabs, ["All"] + categories):
            with tab:
                frame = catalog if category == "All" else catalog[catalog["etf_category"] == category]
                st.metric("ETFs", len(frame))
                st.dataframe(frame, use_container_width=True, hide_index=True)




# ==========================================
# BACKTEST ENGINE
# ==========================================
elif st.session_state.navigation_tab == "BACKTEST":
    st.title("📈 BACKTEST STUDIO")

    strategy_names = list(st.session_state.strategies.keys())
    selected_strategy = st.selectbox("Select Base Strategy:", strategy_names)

    strategy_config = st.session_state.strategies[selected_strategy]

    st.markdown("---")

    bt1, bt2, bt3 = st.columns(3)

    with bt1:
        initial_capital = st.number_input(
            "Initial Backtest Capital (₹):",
            value=100000.0,
            step=10000.0,
        )
        signal_model = st.selectbox(
            "Momentum Signal:",
            ["Ranking", "Volatility-Adjusted"],
            index=(
                0
                if strategy_config.get("signal_type", "Ranking") == "Ranking"
                else 1
            ),
        )

    with bt2:
        start_date = st.date_input(
            "Start Date:",
            value=datetime.now() - timedelta(days=365 * 3),
        )

    with bt3:
        end_date = st.date_input(
            "End Date:",
            value=datetime.now(),
        )
        st.metric(
            "Entry / Exit Rank",
            f"{strategy_config.get('entry_rank', 10)} / "
            f"{strategy_config.get('exit_rank', 20)}",
        )


    if st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True):
        with st.spinner("Running backtest..."):
            release_df = load_historical_release_dataset()
            benchmark_series = release_df.get("^NSEI") if not release_df.empty else None
            stock_prices = release_df.drop(columns=["^NSEI"], errors="ignore") if not release_df.empty else pd.DataFrame()

            selected_groups = strategy_config.get("groups", ["Nifty 500"])
            required_tickers = get_trusted_tickers_by_group(selected_groups)

            # The release dataset is used when available; Yahoo fills any missing selected assets.
            missing_tickers = [t for t in required_tickers if t not in stock_prices.columns]
            if missing_tickers:
                yahoo_prices = fetch_market_data_chunked(missing_tickers, period="5y", chunk_size=50)
                if not yahoo_prices.empty:
                    stock_prices = pd.concat([stock_prices, yahoo_prices], axis=1)
                    stock_prices = stock_prices.loc[:, ~stock_prices.columns.duplicated()]

            if stock_prices.empty:
                st.error("Historical market data could not be loaded. Please try again.")
                st.stop()

            result = run_backtest_simulation(
                strat_config=strategy_config,
                initial_capital=initial_capital,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                price_df=stock_prices,
                benchmark_series=benchmark_series,
                signal_type=signal_model,
            )

            if result is None or result.empty:
                st.error("Insufficient market data for the selected timeframe.")
            else:
                st.success("Backtest completed successfully.")

                final_value = float(result["Portfolio Value"].iloc[-1])
                total_return_pct = ((final_value - initial_capital) / initial_capital) * 100

                peak = result["Portfolio Value"].cummax()
                drawdown = (result["Portfolio Value"] - peak) / peak
                max_drawdown_pct = drawdown.min() * 100

                r1, r2, r3 = st.columns(3)

                r1.metric("Final Portfolio Value", f"₹{final_value:,.2f}")
                r2.metric("Total Backtest Return", f"{total_return_pct:.2f}%")
                r3.metric("Max Drawdown", f"{max_drawdown_pct:.2f}%")

                st.markdown("#### Portfolio Value Curve")
                st.line_chart(result["Portfolio Value"])

                st.markdown("#### Historical Monthly Portfolio Data")
                st.dataframe(result, use_container_width=True)
