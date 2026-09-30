import os
import hashlib
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd

try:
    from fyers_apiv3 import fyersModel
except Exception:
    fyersModel = None


# ============================================================
# CONFIG
# ============================================================

FYERS_LOGIN_URL = "https://api-t1.fyers.in/api/v3/generate-authcode"


# ============================================================
# CREDENTIALS
# ============================================================

def get_app_id():
    return os.getenv("FYERS_APP_ID", "").strip()


def get_secret_id():
    return os.getenv("FYERS_SECRET_ID", "").strip()


def get_redirect_uri():
    return os.getenv("FYERS_REDIRECT_URI", "").strip()


def get_access_token():
    return os.getenv("FYERS_ACCESS_TOKEN", "").strip()


def credentials_available():
    return bool(
        get_app_id()
        and get_secret_id()
        and get_redirect_uri()
    )


# ============================================================
# FYERS LOGIN
# ============================================================

def build_login_url():
    app_id = get_app_id()
    redirect_uri = get_redirect_uri()

    if not app_id or not redirect_uri:
        return ""

    return (
        f"{FYERS_LOGIN_URL}"
        f"?client_id={quote(app_id)}"
        f"&redirect_uri={quote(redirect_uri)}"
        f"&response_type=code"
        f"&state=sample_state"
    )


def create_app_hash():
    app_id = get_app_id()
    secret = get_secret_id()

    if not app_id or not secret:
        raise ValueError("FYERS_APP_ID or FYERS_SECRET_ID is missing.")

    raw = app_id + secret
    return hashlib.sha256(raw.encode()).hexdigest()


def exchange_auth_code(auth_code):
    """
    Exchange FYERS auth_code for an access token.
    """

    if fyersModel is None:
        raise ImportError(
            "fyers-apiv3 is not installed. Check requirements.txt."
        )

    app_id = get_app_id()
    redirect_uri = get_redirect_uri()

    if not app_id:
        raise ValueError("FYERS_APP_ID is missing.")

    if not redirect_uri:
        raise ValueError("FYERS_REDIRECT_URI is missing.")

    if not auth_code:
        raise ValueError("No FYERS auth_code was supplied.")

    session = fyersModel.SessionModel(
        client_id=app_id,
        secret_key=get_secret_id(),
        redirect_uri=redirect_uri,
        response_type="code",
        grant_type="authorization_code",
    )

    session.set_token(auth_code)

    response = session.generate_token()

    if not isinstance(response, dict):
        raise RuntimeError(f"Unexpected FYERS authentication response: {response}")

    if response.get("s") != "ok":
        raise RuntimeError(
            f"FYERS authentication failed: {response}"
        )

    return response.get("access_token")


# ============================================================
# FYERS CLIENT
# ============================================================

def create_client(access_token=None):
    if fyersModel is None:
        raise ImportError(
            "fyers-apiv3 is not installed. Check requirements.txt."
        )

    app_id = get_app_id()

    token = access_token or get_access_token()

    if not app_id:
        raise ValueError("FYERS_APP_ID is missing.")

    if not token:
        raise ValueError(
            "FYERS access token is missing. Login through FYERS first."
        )

    return fyersModel.FyersModel(
        client_id=app_id,
        token=token,
        is_async=False,
        log_path=""
    )


# ============================================================
# SYMBOL HANDLING
# ============================================================

def clean_symbol(symbol):
    if symbol is None:
        return ""

    s = str(symbol).strip().upper()

    if not s:
        return ""

    # Already a FYERS symbol
    if ":" in s:
        return s

    # Indexes
    index_map = {
        "NIFTY": "NSE:NIFTY50-INDEX",
        "NIFTY 50": "NSE:NIFTY50-INDEX",
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "NIFTY 100": "NSE:NIFTY100-INDEX",
        "NIFTY100": "NSE:NIFTY100-INDEX",
        "NIFTY 200": "NSE:NIFTY200-INDEX",
        "NIFTY200": "NSE:NIFTY200-INDEX",
        "NIFTY 500": "NSE:NIFTY500-INDEX",
        "NIFTY500": "NSE:NIFTY500-INDEX",
        "BANK NIFTY": "NSE:NIFTYBANK-INDEX",
        "NIFTY BANK": "NSE:NIFTYBANK-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    }

    if s in index_map:
        return index_map[s]

    # Common ETF / equity symbols
    if s.endswith("-EQ"):
        return f"NSE:{s}"

    return f"NSE:{s}-EQ"


# ============================================================
# DATE HELPERS
# ============================================================

def _date_chunks(start_date, end_date, max_days=366):

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()

    current = start

    while current <= end:

        chunk_end = min(
            current + pd.Timedelta(days=max_days - 1),
            end
        )

        yield current, chunk_end

        current = chunk_end + pd.Timedelta(days=1)


# ============================================================
# HISTORY
# ============================================================

def fetch_symbol_history(
    symbol,
    start_date,
    end_date,
    resolution="1D",
    access_token=None,
):
    """
    Fetch historical OHLCV data from FYERS.

    FYERS daily/weekly/monthly history is requested in chunks
    so long backtests can be downloaded safely.
    """

    client = create_client(access_token)

    fyers_symbol = clean_symbol(symbol)

    all_rows = []

    for chunk_start, chunk_end in _date_chunks(
        start_date,
        end_date,
        max_days=366
    ):

        payload = {
            "symbol": fyers_symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": chunk_start.strftime("%Y-%m-%d"),
            "range_to": chunk_end.strftime("%Y-%m-%d"),
            "cont_flag": "1",
        }

        response = client.history(data=payload)

        if not isinstance(response, dict):
            continue

        if response.get("s") != "ok":
            continue

        candles = response.get("candles", [])

        for row in candles:

            if len(row) < 6:
                continue

            all_rows.append({
                "timestamp": row[0],
                "open": row[1],
                "high": row[2],
                "low": row[3],
                "close": row[4],
                "volume": row[5],
            })

    if not all_rows:
        return pd.DataFrame(
            columns=[
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(all_rows)

    df["date"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        errors="coerce"
    ).dt.tz_localize("UTC").dt.tz_convert(
        "Asia/Kolkata"
    ).dt.tz_localize(None).dt.normalize()

    df = df.drop(columns=["timestamp"])

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = (
        df.dropna(subset=["date", "close"])
          .drop_duplicates(subset=["date"])
          .sort_values("date")
          .reset_index(drop=True)
    )

    return df


def fetch_prices(
    symbols,
    start_date,
    end_date,
    resolution="1D",
    access_token=None,
):
    """
    Fetch multiple symbols.
    Returns:
        dict[symbol] = DataFrame
    """

    result = {}

    for symbol in symbols:

        try:

            df = fetch_symbol_history(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                resolution=resolution,
                access_token=access_token,
            )

            if not df.empty:
                result[symbol] = df

        except Exception as e:

            result[symbol] = pd.DataFrame({
                "error": [str(e)]
            })

    return result


# ============================================================
# QUOTE
# ============================================================

def get_quote(symbol, access_token=None):

    client = create_client(access_token)

    fyers_symbol = clean_symbol(symbol)

    response = client.quotes(
        data={
            "symbols": fyers_symbol
        }
    )

    return response


# ============================================================
# PROFILE
# ============================================================

def get_profile(access_token=None):

    client = create_client(access_token)

    return client.get_profile()
