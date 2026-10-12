"""Read persistent FYERS-sourced Nifty index OHLCV and point-in-time membership.

Index prices are written by scripts/update_fyers_history.py using FYERS API v3.
Legacy index CSVs without the explicit FYERS source marker are ignored, so the
backtest will not silently mix price vendors. Membership intervals remain a
separate reconstructed dataset with the caveats in data/INDEX_DATA_SOURCES.md.
"""
from pathlib import Path
from io import StringIO

import pandas as pd
import numpy as np
import requests

ROOT = Path(__file__).resolve().parent
INDEX_PRICE_DIR = ROOT / "data" / "index_prices"
MEMBERSHIP_PATH = ROOT / "data" / "index_membership_history.csv"
MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/"
    "main/index_history/data/index_membership_history.csv"
)

# display name -> official Nifty Indices history name, app-compatible Yahoo alias,
# and earliest date to request. The official files are written using ISO dates.
INDEX_HISTORY_SPECS = {
    "Nifty 50": {
        "official_name": "NIFTY 50", "ticker": "^NSEI",
        "start_date": "1996-04-22", "filename": "nifty_50.csv",
    },
    "Nifty Bank": {
        "official_name": "NIFTY BANK", "ticker": "^NSEBANK",
        "start_date": "2000-01-01", "filename": "nifty_bank.csv",
    },
    "Nifty 500": {
        "official_name": "NIFTY 500", "ticker": "^CRSLDX",
        "start_date": "1995-01-01", "filename": "nifty_500.csv",
    },
    "Nifty 200": {
        "official_name": "NIFTY 200", "ticker": "^CNX200",
        "start_date": "2004-01-01", "filename": "nifty_200.csv",
    },
    "Nifty IT": {
        "official_name": "NIFTY IT", "ticker": "^CNXIT",
        "start_date": "1996-01-01", "filename": "nifty_it.csv",
    },
    "Nifty Pharma": {
        "official_name": "NIFTY PHARMA", "ticker": "^CNXPHARMA",
        "start_date": "2001-01-01", "filename": "nifty_pharma.csv",
    },
    "Nifty Auto": {
        "official_name": "NIFTY AUTO", "ticker": "^CNXAUTO",
        "start_date": "2004-01-01", "filename": "nifty_auto.csv",
    },
    "Nifty FMCG": {
        "official_name": "NIFTY FMCG", "ticker": "^CNXFMCG",
        "start_date": "1996-01-01", "filename": "nifty_fmcg.csv",
    },
    "Nifty Metal": {
        "official_name": "NIFTY METAL", "ticker": "^CNXMETAL",
        "start_date": "2004-01-01", "filename": "nifty_metal.csv",
    },
}

TICKER_TO_INDEX_NAME = {
    spec["ticker"]: name for name, spec in INDEX_HISTORY_SPECS.items()
}
EMPTY_CALENDAR_COLUMNS = ["symbol", "start", "end"]


def _normalise_ohlc_frame(frame):
    """Normalize local or downloaded index records into Date/Open/High/Low/Close."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    lookup = {str(c).strip().lower().replace("_", " "): c for c in frame.columns}
    date_col = next(
        (lookup[k] for k in ("date", "historicaldate", "historical date") if k in lookup),
        None,
    )
    if date_col is None:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])

    result = pd.DataFrame()
    result["Date"] = pd.to_datetime(frame[date_col], errors="coerce", dayfirst=True)
    for target in ("Open", "High", "Low", "Close", "Volume"):
        col = lookup.get(target.lower())
        result[target] = pd.to_numeric(
            frame[col].astype(str).str.replace(",", "", regex=False), errors="coerce"
        ) if col is not None else float("nan")
    result = result.dropna(subset=["Date", "Close"])
    result["Date"] = result["Date"].dt.tz_localize(None).dt.normalize()
    return result.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def load_index_ohlc(ticker):
    """Return locally stored official OHLC history for a supported index ticker."""
    name = TICKER_TO_INDEX_NAME.get(ticker)
    if not name:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    path = INDEX_PRICE_DIR / INDEX_HISTORY_SPECS[name]["filename"]
    if not path.exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    try:
        raw = pd.read_csv(path)
        if "Source" not in raw.columns:
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        source = raw["Source"].fillna("").astype(str).str.strip().str.upper()
        if not source.eq("FYERS API").all():
            return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        return _normalise_ohlc_frame(raw)
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def load_index_close_prices():
    """Return a date x ticker close-price matrix from checked-in history CSVs."""
    series = {}
    for ticker in TICKER_TO_INDEX_NAME:
        ohlc = load_index_ohlc(ticker)
        if not ohlc.empty:
            series[ticker] = ohlc.set_index("Date")["Close"]
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def get_index_history_coverage(today=None, max_stale_days=10):
    """Audit index date coverage against the configured earliest target date.

    This checks date range and unusually long internal gaps, not price values
    against an official exchange file.
    """
    today = pd.Timestamp(today or pd.Timestamp.now().date()).normalize()
    records = []
    for name, spec in INDEX_HISTORY_SPECS.items():
        frame = load_index_ohlc(spec["ticker"])
        expected = pd.Timestamp(spec["start_date"]).normalize()
        if frame.empty:
            records.append({
                "Index": name, "Target Start": expected.date().isoformat(),
                "First Stored Date": None, "Last Stored Date": None,
                "Candles": 0, "Internal Gaps (>7d)": None,
                "Invalid OHLC Rows": None, "Close Moves >15%": None,
                "Start Covered": False, "Recent Data": False,
                "Coverage Status": "MISSING",
            })
            continue
        dates = pd.to_datetime(frame["Date"], errors="coerce").dropna().sort_values().drop_duplicates()
        first, last = dates.iloc[0].normalize(), dates.iloc[-1].normalize()
        start_ok = first <= expected + pd.Timedelta(days=7)
        recent_ok = (today - last).days <= max_stale_days
        gaps = int((dates.diff().dropna() > pd.Timedelta(days=7)).sum())
        ohlc = frame.reindex(columns=["Open", "High", "Low", "Close"])
        valid_ohlc = (
            ohlc.notna().all(axis=1)
            & np.isfinite(ohlc).all(axis=1)
            & ohlc.gt(0).all(axis=1)
            & (ohlc["High"] >= ohlc[["Open", "Close"]].max(axis=1))
            & (ohlc["Low"] <= ohlc[["Open", "Close"]].min(axis=1))
            & (ohlc["High"] >= ohlc["Low"])
        )
        invalid_ohlc_rows = int((~valid_ohlc).sum())
        large_close_moves = int(
            (ohlc["Close"].pct_change().abs() > 0.15).fillna(False).sum()
        )
        if not start_ok:
            status = "INCOMPLETE — starts after target"
        elif not recent_ok:
            status = "INCOMPLETE — latest data is stale"
        elif invalid_ohlc_rows:
            status = f"CHECK — {invalid_ohlc_rows} invalid OHLC row(s)"
        elif gaps:
            status = f"CHECK — {gaps} gap(s) over 7 days"
        elif large_close_moves:
            status = f"REVIEW — {large_close_moves} close move(s) over 15%"
        else:
            status = "RANGE COVERED — price values not reference-validated"
        records.append({
            "Index": name, "Target Start": expected.date().isoformat(),
            "First Stored Date": first.date().isoformat(),
            "Last Stored Date": last.date().isoformat(),
            "Candles": int(len(dates)), "Internal Gaps (>7d)": gaps,
            "Invalid OHLC Rows": invalid_ohlc_rows,
            "Close Moves >15%": large_close_moves,
            "Start Covered": bool(start_ok), "Recent Data": bool(recent_ok),
            "Coverage Status": status,
        })
    return pd.DataFrame(records)


def load_membership_history():
    """Read the checked-in PIT membership table; use its public source as a fallback."""
    frame = None
    if MEMBERSHIP_PATH.exists():
        try:
            frame = pd.read_csv(MEMBERSHIP_PATH, dtype={"symbol": str, "index_name": str})
        except (OSError, ValueError, pd.errors.ParserError):
            frame = None
    if frame is None or frame.empty:
        try:
            response = requests.get(MEMBERSHIP_URL, timeout=30)
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text), dtype={"symbol": str, "index_name": str})
        except Exception:
            return pd.DataFrame(
                columns=["index_id", "index_name", "symbol", "valid_from", "valid_to"]
            )
    required = {"index_name", "symbol", "valid_from", "valid_to"}
    if not required.issubset(frame.columns):
        return pd.DataFrame(columns=sorted(required))
    frame["index_name"] = frame["index_name"].astype(str).str.strip()
    frame["symbol"] = frame["symbol"].astype(str).str.strip().str.upper()
    frame["valid_from"] = pd.to_datetime(frame["valid_from"], errors="coerce")
    frame["valid_to"] = pd.to_datetime(frame["valid_to"], errors="coerce")
    return frame.dropna(subset=["symbol", "valid_from"])


def get_membership_calendar(index_name):
    """Return a symbol/start/end calendar accepted by momentum_data.was_member.

    Use the locally stored reconstructed interval ledger. It has documented
    limitations, but avoids applying unvalidated modern constituent snapshots
    to earlier years. Nifty 200 has no exact interval series in this source and
    is handled by the app's explicitly labelled broader Nifty 500 fallback.
    Legacy interval valid_to dates are exclusive, so valid_to is shifted back
    one day for the existing inclusive membership test.
    """
    frame = load_membership_history()
    if frame.empty:
        return pd.DataFrame(columns=EMPTY_CALENDAR_COLUMNS)
    rows = frame[frame["index_name"].str.casefold() == str(index_name).strip().casefold()].copy()
    if rows.empty:
        return pd.DataFrame(columns=EMPTY_CALENDAR_COLUMNS)
    rows["symbol"] = rows["symbol"].str.replace(r"\.NS$", "", regex=True) + ".NS"
    rows["start"] = rows["valid_from"]
    rows["end"] = rows["valid_to"] - pd.Timedelta(days=1)
    return rows[["symbol", "start", "end"]].drop_duplicates().reset_index(drop=True)


def get_available_membership_index_names():
    """Return index names actually represented in the checked-in membership table."""
    frame = load_membership_history()
    if frame.empty or "index_name" not in frame:
        return []
    return sorted(frame["index_name"].dropna().astype(str).unique().tolist())
