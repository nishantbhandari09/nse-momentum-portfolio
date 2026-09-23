import io
import re
import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

from kite_auth import get_authenticated_kite


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
        background-color: #F4F7FE;
    }

    .stMetric {
        background-color: #FFFFFF;
        padding: 15px;
        border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05);
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
    "Defensive ETFs",
    "Domestic ETFs",
    "International ETFs",
    "All ETFs",
]

ETF_FALLBACK_TICKERS = [
    "GOLDBEES.NS",
    "LIQUIDCASE.NS",
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
# ZERODHA CONNECTION
# ==========================================
@st.cache_resource(ttl=1200, show_spinner=False)
def get_kite_client():
    try:
        missing = [
            key
            for key in KITE_SECRET_NAMES
            if not st.secrets.get(key)
        ]

        if missing:
            st.session_state["kite_error"] = (
                "Missing Streamlit secrets: "
                + ", ".join(missing)
            )
            return None

        return get_authenticated_kite(
            api_key=st.secrets["KITE_API_KEY"],
            api_secret=st.secrets["KITE_API_SECRET"],
            user_id=st.secrets["KITE_USER_ID"],
            password=st.secrets["KITE_PASSWORD"],
            totp_secret=st.secrets["KITE_TOTP_SECRET"],
        )

    except Exception as exc:
        st.session_state["kite_error"] = str(exc)
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_nse_instrument_map():
    kite = get_kite_client()

    if kite is None:
        return pd.DataFrame()

    try:
        instruments = kite.instruments("NSE")
        instrument_df = pd.DataFrame(instruments)

        if instrument_df.empty:
            return pd.DataFrame()

        required_columns = [
            "tradingsymbol",
            "instrument_token",
        ]

        if any(
            column not in instrument_df.columns
            for column in required_columns
        ):
            return pd.DataFrame()

        if "segment" in instrument_df.columns:
            instrument_df = instrument_df[
                instrument_df["segment"].isin(
                    ["NSE", "NSE-ETF"]
                )
            ].copy()

        instrument_df["yahoo_symbol"] = (
            instrument_df["tradingsymbol"]
            .astype(str)
            .str.upper()
            + ".NS"
        )

        return instrument_df

    except Exception as exc:
        st.session_state["kite_error"] = str(exc)
        return pd.DataFrame()


def _period_to_days(period):
    return {
        "1d": 3,
        "5d": 10,
        "1mo": 31,
        "3mo": 93,
        "6mo": 186,
        "1y": 366,
        "2y": 731,
        "5y": 1826,
        "10y": 3652,
    }.get(period, 1826)


def _clean_tickers(tickers):
    cleaned = []

    for ticker in tickers:
        if ticker is None:
            continue

        ticker = str(ticker).strip().upper()

        if not ticker:
            continue

        if not ticker.endswith(".NS"):
            ticker = f"{ticker}.NS"

        if ticker not in cleaned:
            cleaned.append(ticker)

    return cleaned


def fetch_from_zerodha(tickers, period="5y"):
    requested = _clean_tickers(tickers)
    kite = get_kite_client()
    instruments = get_nse_instrument_map()

    if kite is None or instruments.empty:
        return pd.DataFrame(), requested

    end_date = datetime.now()
    start_date = end_date - timedelta(
        days=_period_to_days(period)
    )

    prices = {}
    unavailable = []

    for ticker in requested:
        matching = instruments[
            instruments["yahoo_symbol"] == ticker
        ]

        if matching.empty:
            unavailable.append(ticker)
            continue

        try:
            instrument_token = int(
                matching.iloc[0]["instrument_token"]
            )

            candles = kite.historical_data(
                instrument_token=instrument_token,
                from_date=start_date,
                to_date=end_date,
                interval="day",
                continuous=False,
                oi=False,
            )

            if not candles:
                unavailable.append(ticker)
                continue

            candle_df = pd.DataFrame(candles)

            if "date" not in candle_df.columns:
                unavailable.append(ticker)
                continue

            if "close" not in candle_df.columns:
                unavailable.append(ticker)
                continue

            candle_df["date"] = pd.to_datetime(
                candle_df["date"]
            )

            candle_df = candle_df.set_index("date").sort_index()

            prices[ticker] = candle_df["close"]

            time.sleep(0.15)

        except Exception:
            unavailable.append(ticker)

    if not prices:
        return pd.DataFrame(), unavailable

    return pd.DataFrame(prices).sort_index(), unavailable


def fetch_from_yahoo(tickers, period="5y"):
    requested = _clean_tickers(tickers)

    if not requested:
        return pd.DataFrame()

    try:
        data = yf.download(
            tickers=requested,
            period=period,
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="column",
        )

        if data.empty:
            return pd.DataFrame()

        if isinstance(data.columns, pd.MultiIndex):
            levels = data.columns.get_level_values(0)

            if "Close" in levels:
                prices = data["Close"]
            elif "Adj Close" in levels:
                prices = data["Adj Close"]
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

        return prices

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_data(tickers, period="5y"):
    """
    Data priority:
    1. Zerodha
    2. Yahoo Finance fallback
    """
    requested = _clean_tickers(tickers)

    if not requested:
        return pd.DataFrame()

    zerodha_prices, unavailable = fetch_from_zerodha(
        requested,
        period=period,
    )

    yahoo_prices = fetch_from_yahoo(
        unavailable,
        period=period,
    )

    if zerodha_prices.empty and yahoo_prices.empty:
        return pd.DataFrame()

    if zerodha_prices.empty:
        combined = yahoo_prices
    elif yahoo_prices.empty:
        combined = zerodha_prices
    else:
        combined = pd.concat(
            [zerodha_prices, yahoo_prices],
            axis=1,
        )

    combined = combined.loc[
        :,
        ~combined.columns.duplicated(),
    ]

    return combined.sort_index().ffill().bfill()


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


def classify_etf(name, symbol):
    text = f"{name} {symbol}".upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)

    defensive_terms = [
        "GOLD",
        "SILVER",
        "LIQUID",
        "GILT",
        "GSEC",
        "GOVERNMENT",
        "GOVT",
        "TREASURY",
        "BOND",
        "DEBT",
        "OVERNIGHT",
        "MONEY MARKET",
        "CORPORATE BOND",
        "AAA",
    ]

    international_terms = [
        "INTERNATIONAL",
        "GLOBAL",
        "WORLD",
        "NASDAQ",
        "S&P 500",
        "SP 500",
        "S&P500",
        "MSCI",
        "HANG SENG",
        "CHINA",
        "JAPAN",
        "EUROPE",
        "US EQUITY",
        "USA",
        "UNITED STATES",
        "UNITED KINGDOM",
        "UK EQUITY",
        "GERMANY",
        "FRANCE",
        "TAIWAN",
        "BRAZIL",
        "EMERGING MARKET",
        "NYSE",
    ]

    if any(term in text for term in defensive_terms):
        return "Defensive ETFs"

    if any(term in text for term in international_terms):
        return "International ETFs"

    return "Domestic ETFs"


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

            records.append(
                {
                    "symbol": yahoo_symbol,
                    "name": name,
                    "group": classify_etf(
                        name,
                        symbol,
                    ),
                }
            )

        catalog = pd.DataFrame(
            records,
            columns=["symbol", "name", "group"],
        )

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
    ASSET_GROUPS_MAPPING["LiquidBEES"] = ["LIQUIDCASE.NS"]

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
    signal_type="Simple Momentum",
    lookback_days=126,
):
    if signal_type == "Volatility-Adjusted Momentum":
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
):
    """
    Rank only LiquidBEES and G-Sec ETFs using 3x, 2x and 1x
    momentum scores, for ranking only.

    Example:
      6% return -> 1x = 6%, 2x = 12%, 3x = 18%
    """
    defensive_tickers = get_defensive_etf_tickers()

    available = [
        ticker
        for ticker in defensive_tickers
        if ticker in prices_df.columns
    ]

    if not available:
        return []

    defensive_prices = prices_df[available].copy()

    raw_returns = calculate_simple_momentum(
        defensive_prices,
        lookback_days=lookback_days,
    )

    if raw_returns.empty:
        return []

    raw_returns = raw_returns.dropna()

    candidates = []

    for multiplier in [3, 2, 1]:
        for ticker in raw_returns.index:
            raw_return = float(raw_returns[ticker])
            adjusted_return = raw_return * multiplier

            candidates.append(
                {
                    "Symbol": ticker,
                    "Multiplier": multiplier,
                    "Raw Return": raw_return,
                    "Adjusted Return": adjusted_return,
                }
            )

    # Higher multiplier first, then higher adjusted return.
    candidates.sort(
        key=lambda row: (
            row["Multiplier"],
            row["Adjusted Return"],
        ),
        reverse=True,
    )

    selected = []
    seen = set()

    for candidate in candidates:
        symbol = candidate["Symbol"]

        if symbol in seen:
            continue

        seen.add(symbol)
        selected.append(candidate)

        if len(selected) >= target_count:
            break

    return selected


def is_regime_bullish(
    benchmark_series,
    current_date,
    sma_period=200,
):
    sub = benchmark_series.loc[:current_date].dropna()

    if len(sub) < sma_period:
        return True

    return sub.iloc[-1] >= sub.tail(sma_period).mean()


def apply_rank_buffering(
    current_holdings,
    ranked_candidates,
    target_size,
    buffer_rank,
):
    new_portfolio = []

    for stock in current_holdings:
        if stock in ranked_candidates:
            stock_rank = ranked_candidates.index(stock) + 1
            if (
                stock_rank <= buffer_rank
                and len(new_portfolio) < target_size
            ):
                new_portfolio.append(stock)

    for stock in ranked_candidates:
        if len(new_portfolio) >= target_size:
            break

        if stock not in new_portfolio:
            new_portfolio.append(stock)

    return new_portfolio


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
    buffer_threshold=20,
):
    if price_df is None or price_df.empty:
        groups = strat_config.get(
            "groups",
            ["Nifty 500"],
        )

        tickers = get_trusted_tickers_by_group(groups)
        price_df = fetch_market_data(
            tickers,
            period="5y",
        )

    sub_prices = price_df.loc[start_date:end_date]

    if len(sub_prices) < 252:
        return None

    try:
        rebalance_dates = (
            sub_prices
            .resample("ME")
            .first()
            .index
        )
    except ValueError:
        rebalance_dates = (
            sub_prices
            .resample("MS")
            .first()
            .index
        )

    portfolio_history = []
    current_cash = initial_capital
    current_holdings = {}

    multiplier = strat_config.get(
        "allocation_multiplier",
        1.0,
    )

    for i in range(len(rebalance_dates) - 1):
        current_date = rebalance_dates[i]
        history = sub_prices.loc[:current_date]

        if len(history) < 126:
            continue

        regime_ok = True

        if (
            use_regime_filter
            and benchmark_series is not None
        ):
            regime_ok = is_regime_bullish(
                benchmark_series,
                current_date,
                sma_period=200,
            )

        total_value = current_cash

        for symbol, quantity in current_holdings.items():
            if (
                symbol in history.columns
                and not np.isnan(
                    history[symbol].iloc[-1]
                )
            ):
                total_value += (
                    quantity * history[symbol].iloc[-1]
                )

        ranking_prices = history.drop(
            columns=[
                NIFTY_REGIME_TICKER,
                GSEC_REGIME_TICKER,
            ],
            errors="ignore",
        )

        scores = get_momentum_scores(
            ranking_prices,
            signal_type=signal_type,
            lookback_days=126,
        )

        ranked_stocks = (
            scores
            .sort_values(ascending=False)
            .dropna()
            .index
            .tolist()
        )

        if not regime_ok:
            current_holdings = {}
            current_cash = total_value
        else:
            top_n = strat_config.get(
                "entry_rank",
                10,
            )

            if use_rank_buffer and current_holdings:
                selected_stocks = apply_rank_buffering(
                    list(current_holdings.keys()),
                    ranked_stocks,
                    top_n,
                    buffer_threshold,
                )
            else:
                selected_stocks = ranked_stocks[:top_n]

            if selected_stocks:
                effective_pool = total_value * multiplier
                allocation_per_stock = (
                    effective_pool / len(selected_stocks)
                )

                current_holdings = {}
                current_cash = total_value

                for symbol in selected_stocks:
                    if symbol not in history.columns:
                        continue

                    stock_price = history[symbol].iloc[-1]

                    if stock_price > 0:
                        quantity = int(
                            allocation_per_stock // stock_price
                        )

                        if quantity > 0:
                            current_holdings[symbol] = quantity
                            current_cash -= (
                                quantity * stock_price
                            )

        portfolio_history.append(
            {
                "Date": current_date,
                "Portfolio Value": total_value,
            }
        )

    if not portfolio_history:
        return None

    return pd.DataFrame(
        portfolio_history
    ).set_index("Date")


# ==========================================
# STOCK SCANNER
# ==========================================
@st.cache_data(ttl=86400)
def get_trusted_tickers_by_group(groups):
    tickers = []

    if (
        "Nifty 500" in groups
        or "Nifty 200" in groups
    ):
        official_url = (
            "https://niftyindices.com/IndexConstituent/"
            "ind_nifty500list.csv"
        )

        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0"})

        try:
            response = session.get(
                official_url,
                timeout=10,
            )

            if response.status_code == 200:
                df = pd.read_csv(io.StringIO(response.text))

                if "Symbol" in df.columns:
                    n500 = [
                        f"{str(sym).strip()}.NS"
                        for sym in df["Symbol"]
                        .dropna()
                        .unique()
                    ]

                    if (
                        "Nifty 200" in groups
                        and "Nifty 500" not in groups
                    ):
                        tickers.extend(n500[:200])
                    else:
                        tickers.extend(n500)
        except Exception:
            mirror_url = (
                "https://raw.githubusercontent.com/"
                "indian-stock-market/"
                "nifty-500-constituents/"
                "main/nifty500.csv"
            )

            try:
                df = pd.read_csv(mirror_url)

                if "Symbol" in df.columns:
                    tickers.extend(
                        [
                            f"{str(sym).strip()}.NS"
                            for sym in df["Symbol"]
                            .dropna()
                            .unique()
                        ]
                    )
            except Exception:
                tickers.extend(
                    [
                        "RELIANCE.NS",
                        "TCS.NS",
                        "INFY.NS",
                        "HDFCBANK.NS",
                        "ICICIBANK.NS",
                        "LT.NS",
                        "SBIN.NS",
                    ]
                )

    for group in groups:
        if group in ETF_GROUP_NAMES:
            tickers.extend(
                get_etf_tickers(group)
            )
        elif group in ASSET_GROUPS_MAPPING:
            tickers.extend(
                ASSET_GROUPS_MAPPING[group]
            )

    if not tickers:
        tickers = [
            "RELIANCE.NS",
            "TCS.NS",
            "INFY.NS",
            "HDFCBANK.NS",
            "ICICIBANK.NS",
        ]

    return sorted(set(tickers))


def run_strategy_stock_scanner(
    strat,
    price_subset=None,
):
    selected_groups = strat.get(
        "groups",
        ["Nifty 500"],
    )

    if price_subset is None:
        group_tickers = get_trusted_tickers_by_group(
            selected_groups
        )

        group_tickers.extend(
            [
                NIFTY_REGIME_TICKER,
                GSEC_REGIME_TICKER,
            ]
        )

        prices_df = fetch_market_data(
            group_tickers,
            period="2y",
        )
    else:
        prices_df = price_subset

    if prices_df.empty:
        return []

    latest_prices = prices_df.iloc[-1]
    filtered_df = prices_df.copy()

    ma_config = strat.get(
        "moving_average",
        "200 EMA",
    )

    if ma_config != "None":
        period = (
            200
            if "200" in ma_config
            else 100
            if "100" in ma_config
            else 50
        )

        if "EMA" in ma_config:
            ma_values = (
                prices_df
                .ewm(span=period, adjust=False)
                .mean()
                .iloc[-1]
            )
        else:
            ma_values = (
                prices_df
                .rolling(window=period)
                .mean()
                .iloc[-1]
            )

        valid_columns = latest_prices > ma_values
        filtered_df = filtered_df.loc[
            :,
            valid_columns[
                valid_columns.index.isin(
                    filtered_df.columns
                )
            ],
        ]

    pct_high = strat.get("pct_from_high", 15.0)

    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = (
            (period_high - latest_prices)
            / period_high
        ) * 100

        valid_columns = pct_diff <= pct_high
        filtered_df = filtered_df.loc[
            :,
            valid_columns[
                valid_columns.index.isin(
                    filtered_df.columns
                )
            ],
        ]

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

            if rs_ratio.iloc[-1] < rs_sma.iloc[-1]:
                return [ASSET_GROUPS_MAPPING["Gov Bond"][0]]

    # Special defensive ETF ranking: LiquidBEES and G-Sec only.
    target_count = strat.get("entry_rank", 10)
    selected_groups = strat.get("groups", [])

    defensive_groups_selected = any(
        group in selected_groups
        for group in [
            "LiquidBEES",
            "Gov Bond",
            "Defensive ETFs",
        ]
    )

    if defensive_groups_selected:
        defensive_results = rank_defensive_etfs_with_multipliers(
            prices_df=filtered_df,
            target_count=target_count,
            lookback_days=126,
        )

        st.session_state["last_defensive_ranking"] = (
            defensive_results
        )

        if defensive_results:
            return [
                result["Symbol"]
                for result in defensive_results
            ]

    # Standard logic for non-defensive universe.
    filtered_df = filtered_df.drop(
        columns=[
            NIFTY_REGIME_TICKER,
            GSEC_REGIME_TICKER,
        ],
        errors="ignore",
    )

    signal_model = strat.get(
        "signal_type",
        "Simple Momentum",
    )

    scores = get_momentum_scores(
        filtered_df,
        signal_type=signal_model,
        lookback_days=126,
    )

    ranked = (
        scores
        .sort_values(ascending=False)
        .dropna()
        .index
        .tolist()
    )

    return ranked[:target_count]


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
        "Alpha Momentum Multiplier": {
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
                "Defensive ETFs",
            ],
            "allocation_multiplier": 1.0,
            "entry_rank": 10,
            "exit_rank": 20,
            "pct_from_high": 15.0,
            "pct_from_low": 0.0,
            "moving_average": "200 EMA",
            "use_rs": True,
            "rs_benchmark": "Nifty 500 / G-Sec",
            "signal_type": "Simple Momentum",
            "use_regime_filter": True,
            "use_rank_buffer": True,
            "positions": [],
        }
    }


# ==========================================
# SIDEBAR DATA PROVIDER STATUS
# ==========================================
primary_provider = (
    "Zerodha Kite Connect"
    if get_kite_client() is not None
    else "Yahoo Finance fallback"
)

if primary_provider == "Zerodha Kite Connect":
    st.sidebar.success("Primary data: Zerodha Kite")
else:
    st.sidebar.warning("Zerodha unavailable")
    st.sidebar.info("Using Yahoo Finance fallback")

if st.session_state.get("kite_error"):
    st.sidebar.code(st.session_state["kite_error"])


# ==========================================
# NAVIGATION HEADER
# ==========================================
nav_col1, nav_col2, nav_col3, _ = st.columns(
    [1.5, 2, 1.5, 3]
)

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

st.markdown("---")


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
            f"**Multiplier:** {strat.get('allocation_multiplier', 1.0)}x | "
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

        if st.button(
            "🚀 Run Live Stock Scan for this Strategy",
            type="primary",
            use_container_width=True,
        ):
            with st.spinner("Running scanner..."):
                scanned = run_strategy_stock_scanner(strat)
                st.session_state[f"scanned_results_{strat_name}"] = scanned

        scanned_results = st.session_state.get(f"scanned_results_{strat_name}")

        if scanned_results is not None:
            st.success(f"Scan complete. Found **{len(scanned_results)}** target assets.")

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
                reinvest_pool = metrics["current_total_value"] * strat.get("allocation_multiplier", 1.0)
                allocation_per_stock = reinvest_pool / len(scanned_results) if scanned_results else 0.0

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

                strat["positions"] = new_positions
                strat["realized_pnl"] = metrics["total_pnl"]
                st.session_state[f"scanned_results_{strat_name}"] = None
                st.success(f"Rebalanced across {len(new_positions)} assets.")
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

                    st.caption(f"Groups: {', '.join(strat.get('groups', ['Nifty 500']))} | Multiplier: {strat.get('allocation_multiplier', 1.0)}x")
                    st.caption(f"Rebalance Date: Day {strat.get('rebalance_day', 1)} | {days_left} days left")
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

        default_name = st.session_state.editing_strategy_name if edit_mode else "New Momentum Strategy"
        strategy_name_input = st.text_input("Strategy Name:", value=default_name, disabled=edit_mode)

    with inv2:
        allocated = st.number_input("Total Allocation Capital (₹):", value=float(edit_strat.get("allocated", 100000.0)), step=10000.0)
        rebalance_frequency = st.selectbox("Rebalance Frequency:", ["Monthly", "Weekly", "Quarterly"])

    with inv3:
        rebalance_day = st.number_input("Rebalance Day (1-28):", value=int(edit_strat.get("rebalance_day", 1)), min_value=1, max_value=28)
        allocation_multiplier = st.number_input("Allocation Multiplier:", value=float(edit_strat.get("allocation_multiplier", 1.0)), min_value=0.1, max_value=10.0, step=0.1)

    st.markdown("---")
    st.markdown("#### Asset Universe Selection")

    existing_groups = edit_strat.get("groups", ["Nifty 500"])

    gcol1, gcol2, gcol3 = st.columns(3)

    with gcol1:
        nifty500 = st.checkbox("Nifty 500 Universe", value="Nifty 500" in existing_groups)
        nifty200 = st.checkbox("Nifty 200 Universe", value="Nifty 200" in existing_groups)
        defensive = st.checkbox("Defensive ETFs", value="Defensive ETFs" in existing_groups)

    with gcol2:
        domestic = st.checkbox("Domestic ETFs", value="Domestic ETFs" in existing_groups)
        international = st.checkbox("International ETFs", value="International ETFs" in existing_groups)
        all_etfs = st.checkbox("All ETFs", value="All ETFs" in existing_groups)

    with gcol3:
        gold_only = st.checkbox("Gold ETF Only", value="Gold ETF" in existing_groups)
        liquid_only = st.checkbox("LiquidBEES Only", value="LiquidBEES" in existing_groups)
        gov_bond_only = st.checkbox("Gov Bond Only", value="Gov Bond" in existing_groups)

    with st.expander("View current ETF universe", expanded=False):
        counts = get_etf_group_counts()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Defensive ETFs", counts["Defensive ETFs"])
        c2.metric("Domestic ETFs", counts["Domestic ETFs"])
        c3.metric("International ETFs", counts["International ETFs"])
        c4.metric("All ETFs", counts["All ETFs"])

        catalog = get_etf_group_catalog()

        if not catalog.empty:
            st.dataframe(catalog, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### Signal & Execution Settings")

    sig1, sig2 = st.columns(2)

    with sig1:
        signal_type = st.selectbox(
            "Momentum Signal:",
            ["Simple Momentum", "Volatility-Adjusted Momentum"],
            index=0 if edit_strat.get("signal_type") == "Simple Momentum" else 1,
        )

        entry_rank = st.number_input("Top N Entry Stocks:", value=int(edit_strat.get("entry_rank", 10)), min_value=1, max_value=50)

    with sig2:
        use_regime_filter = st.checkbox("Enable 200 SMA Regime Filter", value=edit_strat.get("use_regime_filter", True))
        use_rank_buffer = st.checkbox("Enable Rank Buffer Rule", value=edit_strat.get("use_rank_buffer", True))
        exit_rank = st.number_input("Exit / Buffer Rank Cutoff:", value=int(edit_strat.get("exit_rank", 20)), min_value=1, max_value=100)

    st.markdown("---")

    save_label = "💾 Update Strategy Config" if edit_mode else "💾 Deploy New Strategy Profile"

    if st.button(save_label, type="primary", use_container_width=True):
        selected_groups = []

        if nifty500:
            selected_groups.append("Nifty 500")

        if nifty200:
            selected_groups.append("Nifty 200")

        if defensive:
            selected_groups.append("Defensive ETFs")

        if domestic:
            selected_groups.append("Domestic ETFs")

        if international:
            selected_groups.append("International ETFs")

        if all_etfs:
            selected_groups.append("All ETFs")

        if gold_only:
            selected_groups.append("Gold ETF")

        if liquid_only:
            selected_groups.append("LiquidBEES")

        if gov_bond_only:
            selected_groups.append("Gov Bond")

        target_name = st.session_state.editing_strategy_name if edit_mode else strategy_name_input

        st.session_state.strategies[target_name] = {
            "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
            "status": edit_strat.get("status", "Active"),
            "type": strategy_type,
            "allocated": float(allocated),
            "realized_pnl": edit_strat.get("realized_pnl", 0.0),
            "rebalance_freq": rebalance_frequency,
            "rebalance_day": rebalance_day,
            "groups": selected_groups if selected_groups else ["Nifty 500"],
            "allocation_multiplier": float(allocation_multiplier),
            "entry_rank": entry_rank,
            "exit_rank": exit_rank,
            "signal_type": signal_type,
            "use_regime_filter": use_regime_filter,
            "use_rank_buffer": use_rank_buffer,
            "pct_from_high": 15.0,
            "moving_average": "200 EMA",
            "use_rs": True,
            "positions": edit_strat.get("positions", []),
        }

        st.session_state.editing_strategy_name = None
        st.session_state.navigation_tab = "DASHBOARD"
        st.rerun()


# ==========================================
# BACKTEST ENGINE
# ==========================================
elif st.session_state.navigation_tab == "BACKTEST":
    st.title("📈 BACKTEST STUDIO")
    st.caption("Simulate historical momentum performance using the selected strategy universe.")

    strategy_names = list(st.session_state.strategies.keys())
    selected_strategy = st.selectbox("Select Base Strategy:", strategy_names)

    strategy_config = st.session_state.strategies[selected_strategy]

    st.markdown("---")

    bt1, bt2, bt3 = st.columns(3)

    with bt1:
        initial_capital = st.number_input("Initial Backtest Capital (₹):", value=100000.0, step=10000.0)
        signal_model = st.selectbox("Signal Layer Model:", ["Simple Momentum", "Volatility-Adjusted Momentum"])

    with bt2:
        start_date = st.date_input("Start Date:", value=datetime.now() - timedelta(days=365 * 3))
        regime_filter = st.checkbox("Enable 200 SMA Regime Filter", value=True)

    with bt3:
        end_date = st.date_input("End Date:", value=datetime.now())
        rank_buffer = st.checkbox("Enable Rank Buffer Rule", value=True)
        buffer_value = st.number_input("Buffer Rank Cutoff:", value=20, min_value=10, max_value=50)

    if st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True):
        with st.spinner("Running backtest..."):
            release_df = load_historical_release_dataset()

            benchmark_series = None

            if "^NSEI" in release_df.columns:
                benchmark_series = release_df["^NSEI"]
                stock_prices = release_df.drop(columns=["^NSEI"])
            else:
                stock_prices = release_df

            result = run_backtest_simulation(
                strat_config=strategy_config,
                initial_capital=initial_capital,
                start_date=start_date.strftime("%Y-%m-%d"),
                end_date=end_date.strftime("%Y-%m-%d"),
                price_df=stock_prices,
                benchmark_series=benchmark_series,
                signal_type=signal_model,
                use_regime_filter=regime_filter,
                use_rank_buffer=rank_buffer,
                buffer_threshold=buffer_value,
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
