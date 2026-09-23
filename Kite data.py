from __future__ import annotations

import time
from datetime import datetime, timedelta

import pandas as pd
import streamlit as st
import yfinance as yf

from kite_auth import get_authenticated_kite


KITE_SECRET_NAMES = [
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_USER_ID",
    "KITE_PASSWORD",
    "KITE_TOTP_SECRET",
]


@st.cache_resource(ttl=1200, show_spinner=False)
def get_kite_client():
    """
    Log in to Zerodha and return a KiteConnect client.

    Returns None when credentials are missing or Zerodha is unavailable.
    """
    try:
        missing = []

        for name in KITE_SECRET_NAMES:
            if not st.secrets.get(name):
                missing.append(name)

        if missing:
            return None

        return get_authenticated_kite(
            api_key=st.secrets["KITE_API_KEY"],
            api_secret=st.secrets["KITE_API_SECRET"],
            user_id=st.secrets["KITE_USER_ID"],
            password=st.secrets["KITE_PASSWORD"],
            totp_secret=st.secrets["KITE_TOTP_SECRET"],
        )

    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False)
def get_nse_instruments():
    """
    Download all NSE instruments from Zerodha.

    This includes normal NSE shares and NSE ETFs.
    """
    kite = get_kite_client()

    if kite is None:
        return pd.DataFrame()

    try:
        instruments = kite.instruments("NSE")
        df = pd.DataFrame(instruments)

        if df.empty:
            return pd.DataFrame()

        required_columns = [
            "tradingsymbol",
            "instrument_token",
            "exchange",
        ]

        for column in required_columns:
            if column not in df.columns:
                return pd.DataFrame()

        # Keep NSE shares and NSE ETFs.
        if "segment" in df.columns:
            df = df[
                df["segment"].isin(["NSE", "NSE-ETF"])
            ].copy()

        df["yahoo_symbol"] = (
            df["tradingsymbol"].astype(str).str.upper() + ".NS"
        )

        return df

    except Exception:
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
    result = []

    for ticker in tickers:
        if ticker is None:
            continue

        value = str(ticker).strip().upper()

        if not value:
            continue

        if not value.endswith(".NS"):
            value = f"{value}.NS"

        if value not in result:
            result.append(value)

    return result


def fetch_from_zerodha(tickers, period="5y"):
    """
    Fetch daily closing prices from Zerodha.

    Returns:
        prices_df, unavailable_tickers
    """
    requested = _clean_tickers(tickers)
    kite = get_kite_client()
    instruments = get_nse_instruments()

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

        instrument_token = int(
            matching.iloc[0]["instrument_token"]
        )

        try:
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

            # Avoid excessive API requests.
            time.sleep(0.15)

        except Exception:
            unavailable.append(ticker)

    if not prices:
        return pd.DataFrame(), unavailable

    return pd.DataFrame(prices).sort_index(), unavailable


def fetch_from_yahoo(tickers, period="5y"):
    """
    Fetch data from Yahoo Finance for symbols unavailable from Zerodha.
    """
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
            level_zero = data.columns.get_level_values(0)

            if "Close" in level_zero:
                prices = data["Close"]
            elif "Adj Close" in level_zero:
                prices = data["Adj Close"]
            else:
                return pd.DataFrame()
        else:
            if "Close" in data.columns:
                prices = data[["Close"]].copy()

                if len(requested) == 1:
                    prices.columns = [requested[0]]
            else:
                prices = data.copy()

        if isinstance(prices, pd.Series):
            prices = prices.to_frame(name=requested[0])

        return prices

    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_data(tickers, period="5y"):
    """
    Fetch market data with this priority:

    1. Zerodha Kite Connect
    2. Yahoo Finance for missing symbols
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


def get_data_provider_status():
    """Return a simple provider status for display in the app."""
    if get_kite_client() is not None:
        return "Zerodha Kite Connect"

    return "Yahoo Finance fallback"
