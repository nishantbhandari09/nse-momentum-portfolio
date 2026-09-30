from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
import streamlit as st

try:
    from fyers_apiv3 import fyersModel
    from fyers_apiv3 import accessToken
except ImportError:
    fyersModel = None
    accessToken = None


def get_secret(name: str, default: str = "") -> str:
    """
    Read a value from Streamlit Secrets.

    Supports:
        st.secrets["KEY"]

    Returns an empty string when unavailable.
    """
    try:
        value = st.secrets.get(name, default)
        if value is None:
            return default
        return str(value).strip()
    except Exception:
        return default


def get_fyers_app_id() -> str:
    return get_secret("FYERS_APP_ID")


def get_fyers_secret() -> str:
    return get_secret("FYERS_SECRET_ID")


def get_fyers_redirect_uri() -> str:
    return get_secret("FYERS_REDIRECT_URI")


def credentials_available() -> bool:
    return bool(
        get_fyers_app_id()
        and get_fyers_secret()
        and get_fyers_redirect_uri()
        and fyersModel is not None
        and accessToken is not None
    )


def build_login_url() -> str:
    """
    Generate the FYERS OAuth login URL.
    """
    if not credentials_available():
        return ""

    session = accessToken.SessionModel(
        client_id=get_fyers_app_id(),
        secret_key=get_fyers_secret(),
        redirect_uri=get_fyers_redirect_uri(),
        response_type="code",
        grant_type="authorization_code",
        state="nse-momentum-portfolio",
    )

    return session.generate_authcode()


def exchange_auth_code(auth_code: str) -> Tuple[Optional[str], str]:
    """
    Convert the FYERS auth code into an access token.
    """
    if not credentials_available():
        return None, "FYERS credentials are missing."

    auth_code = str(auth_code).strip()

    if not auth_code:
        return None, "Auth code is empty."

    try:
        session = accessToken.SessionModel(
            client_id=get_fyers_app_id(),
            secret_key=get_fyers_secret(),
            redirect_uri=get_fyers_redirect_uri(),
            response_type="code",
            grant_type="authorization_code",
            state="nse-momentum-portfolio",
        )

        session.set_token(auth_code)

        response = session.generate_token()

        if isinstance(response, dict):
            token = response.get("access_token")

            if token:
                return token, ""

            return None, str(response)

        return None, str(response)

    except Exception as exc:
        return None, f"FYERS authentication error: {exc}"


def create_client(access_token: str):
    """
    Create the FYERS market-data client.
    """
    if not access_token:
        return None

    if fyersModel is None:
        return None

    return fyersModel.FyersModel(
        client_id=get_fyers_app_id(),
        token=access_token,
        log_path="",
    )


def normalize_fyers_symbol(symbol: str) -> str:
    """
    Convert:
        RELIANCE
        RELIANCE.NS
        NSE:RELIANCE-EQ

    into:
        NSE:RELIANCE-EQ
    """
    value = str(symbol).strip().upper()

    if not value:
        return ""

    value = value.replace(".NS", "")

    if value.startswith("NSE:"):
        if value.endswith("-EQ"):
            return value
        return f"{value}-EQ"

    if value.endswith("-EQ"):
        return f"NSE:{value}"

    return f"NSE:{value}-EQ"


def clean_symbol(symbol: str) -> str:
    """
    Convert FYERS symbol back into the application's simple symbol.
    """
    value = str(symbol).strip().upper()

    value = value.replace("NSE:", "")
    value = value.replace("-EQ", "")
    value = value.replace(".NS", "")

    return value


def _date_chunks(
    start_date: str,
    end_date: str,
    max_days: int = 366,
) -> Iterable[Tuple[str, str]]:

    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()

    current = start

    while current <= end:
        chunk_end = min(
            current + timedelta(days=max_days - 1),
            end,
        )

        yield (
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
        )

        current = chunk_end + timedelta(days=1)


def _request_history(
    client,
    fyers_symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:

    all_rows: List[list] = []

    for chunk_start, chunk_end in _date_chunks(
        start_date,
        end_date,
        max_days=366,
    ):

        data = {
            "symbol": fyers_symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": chunk_start,
            "range_to": chunk_end,
            "cont_flag": "1",
        }

        response = client.history(data=data)

        if not isinstance(response, dict):
            continue

        if response.get("s") != "ok":
            continue

        candles = response.get("candles", [])

        if candles:
            all_rows.extend(candles)

        time.sleep(0.05)

    if not all_rows:
        return pd.DataFrame()

    rows = []

    for candle in all_rows:
        if len(candle) < 6:
            continue

        rows.append(
            {
                "timestamp": candle[0],
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
                "volume": candle[5],
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df["date"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        errors="coerce",
    )

    df = df.dropna(subset=["date"])

    df = df.drop_duplicates(
        subset=["date"],
        keep="last",
    )

    df = df.sort_values("date")

    df = df.set_index("date")

    return df


@st.cache_data(
    ttl=60 * 60 * 24,
    show_spinner=False,
)
def fetch_symbol_history(
    access_token: str,
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:

    client = create_client(access_token)

    if client is None:
        return pd.DataFrame()

    fyers_symbol = normalize_fyers_symbol(symbol)

    if not fyers_symbol:
        return pd.DataFrame()

    try:
        return _request_history(
            client,
            fyers_symbol,
            start_date,
            end_date,
        )
    except Exception:
        return pd.DataFrame()


@st.cache_data(
    ttl=60 * 60 * 24,
    show_spinner=False,
)
def fetch_prices(
    access_token: str,
    symbols: Tuple[str, ...],
    start_date: str,
    end_date: str,
    delay: float = 0.05,
) -> Tuple[pd.DataFrame, List[str]]:

    prices: Dict[str, pd.Series] = {}

    unavailable: List[str] = []

    for symbol in symbols:

        clean = clean_symbol(symbol)

        try:
            history = fetch_symbol_history(
                access_token,
                clean,
                start_date,
                end_date,
            )

            if history.empty:
                unavailable.append(clean)
                continue

            if "close" not in history.columns:
                unavailable.append(clean)
                continue

            series = history["close"].copy()

            series.index = pd.to_datetime(
                series.index
            ).tz_localize(None)

            series = series.astype(float)

            prices[clean] = series

        except Exception:
            unavailable.append(clean)

        time.sleep(max(float(delay), 0.0))

    if not prices:
        return pd.DataFrame(), unavailable

    frame = pd.DataFrame(prices)

    frame = frame.sort_index()

    frame = frame.loc[
        :,
        ~frame.columns.duplicated(),
    ]

    frame = frame.replace(
        [float("inf"), float("-inf")],
        pd.NA,
    )

    return frame, unavailable


def get_profile(access_token: str):
    """
    Simple authentication test.
    """
    client = create_client(access_token)

    if client is None:
        return None

    try:
        return client.get_profile()
    except Exception as exc:
        return {
            "s": "error",
            "message": str(exc),
        }


def get_quote(
    access_token: str,
    symbols: List[str],
):
    """
    Retrieve current quote snapshots.

    FYERS supports up to 50 symbols per quotes request.
    """
    client = create_client(access_token)

    if client is None:
        return None

    fyers_symbols = [
        normalize_fyers_symbol(symbol)
        for symbol in symbols
    ]

    fyers_symbols = [
        symbol for symbol in fyers_symbols
        if symbol
    ]

    if not fyers_symbols:
        return None

    try:
        data = {
            "symbols": ",".join(fyers_symbols)
        }

        return client.quotes(data)

    except Exception as exc:
        return {
            "s": "error",
            "message": str(exc),
        }
