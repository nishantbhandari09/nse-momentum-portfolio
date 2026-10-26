"""Authenticated FYERS history fetch for native in-app index charts.

This module is chart-only. It does not change the stored-data readers used for
backtests and never treats an unverified legacy CSV as trusted price history.
"""
from __future__ import annotations

from datetime import date, timedelta
import pandas as pd


INDEX_FYERS_SYMBOLS = {
    "^NSEI": "NSE:NIFTY50-INDEX",
    "^NSEBANK": "NSE:NIFTYBANK-INDEX",
    "^CRSLDX": "NSE:NIFTY500-INDEX",
    "^CNX200": "NSE:NIFTY200-INDEX",
    "^CNXIT": "NSE:NIFTYIT-INDEX",
    "^CNXPHARMA": "NSE:NIFTYPHARMA-INDEX",
    "^CNXAUTO": "NSE:NIFTYAUTO-INDEX",
    "^CNXFMCG": "NSE:NIFTYFMCG-INDEX",
    "^CNXMETAL": "NSE:NIFTYMETAL-INDEX",
}

OHLCV_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def normalize_history_response(response: dict) -> pd.DataFrame:
    """Convert one successful FYERS history response into a clean OHLCV frame."""
    if not isinstance(response, dict):
        raise RuntimeError("FYERS returned an unexpected history response.")
    if response.get("s") != "ok":
        message = str(response.get("message") or response.get("error") or "request was not successful")
        code = response.get("code")
        suffix = f" (code {code})" if code is not None else ""
        raise RuntimeError(f"FYERS history request failed{suffix}: {message[:400]}")
    candles = response.get("candles") or []
    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS)

    frame = pd.DataFrame(candles)
    if frame.shape[1] < 5:
        raise RuntimeError("FYERS returned candle rows with fewer than five OHLC fields.")
    names = ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    frame = frame.iloc[:, :6].copy()
    frame.columns = names[:frame.shape[1]]
    frame["Date"] = (
        pd.to_datetime(pd.to_numeric(frame["Timestamp"], errors="coerce"), unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    for column in ("Open", "High", "Low", "Close", "Volume"):
        if column not in frame.columns:
            frame[column] = float("nan")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["Date", "Open", "High", "Low", "Close"])
    frame = frame[
        (frame["Open"] > 0)
        & (frame["High"] > 0)
        & (frame["Low"] > 0)
        & (frame["Close"] > 0)
        & (frame["High"] >= frame[["Open", "Close"]].max(axis=1))
        & (frame["Low"] <= frame[["Open", "Close"]].min(axis=1))
        & (frame["High"] >= frame["Low"])
    ]
    return (
        frame[OHLCV_COLUMNS]
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")
        .reset_index(drop=True)
    )


def fetch_index_history(
    fyers_client,
    app_ticker: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Fetch daily candles from FYERS in <=366-calendar-day chunks."""
    ticker = str(app_ticker or "").strip()
    if ticker not in INDEX_FYERS_SYMBOLS:
        raise ValueError(f"Unsupported FYERS index ticker: {ticker or '(blank)'}")
    if start_date > end_date:
        raise ValueError("Start date must be on or before end date.")

    frames = []
    cursor = start_date
    fyers_symbol = INDEX_FYERS_SYMBOLS[ticker]
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=365))
        response = fyers_client.history(data={
            "symbol": fyers_symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": cursor.isoformat(),
            "range_to": chunk_end.isoformat(),
        })
        frame = normalize_history_response(response)
        if not frame.empty:
            frames.append(frame)
        cursor = chunk_end + timedelta(days=1)

    if not frames:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    result["Date"] = pd.to_datetime(result["Date"], errors="coerce")
    return (
        result.dropna(subset=["Date"])
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")
        .reset_index(drop=True)[OHLCV_COLUMNS]
    )
