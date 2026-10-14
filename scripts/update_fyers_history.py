#!/usr/bin/env python3
"""Resumable FYERS API v3 daily OHLCV downloader for stocks, ETFs and Nifty indices.

The updater writes only FYERS-returned candles to data/fyers_history and
data/index_prices. It intentionally does not substitute Yahoo/NSE price data.
It is safe to run repeatedly: recent dates are refreshed with overlap, older
history is backfilled in 366-calendar-day windows, and a manifest records
coverage and completion for each symbol.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import index_history_data
from etf_group_universe import ETF_GROUP_SYMBOLS, get_etf_group_symbols

DATA_API_URL = "https://api-t1.fyers.in/data/history"
AUTH_API_URL = "https://api-t1.fyers.in/api/v3/validate-refresh-token"
SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/NSE_CM.csv"
MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/aditya-jha/nse-historical-membership/"
    "main/index_history/data/index_membership_history.csv"
)
LEGACY_MEMBERSHIP_URL = (
    "https://raw.githubusercontent.com/rishabhjain0295-web/"
    "nifty500-momentum-backtest/master/data/nifty500_membership_calendar.csv"
)
PRICE_DIR = ROOT / "data" / "fyers_history"
INDEX_DIR = ROOT / "data" / "index_prices"
MANIFEST_PATH = ROOT / "data" / "fyers_history_manifest.csv"
FAILURES_PATH = ROOT / "data" / "fyers_history_failures.csv"
SOURCE_NAME = "FYERS API"

# Common API symbols are used as a fallback only; the current FYERS symbol
# master is searched first to handle changes in symbol naming.
INDEX_NAMES = {
    "^NSEI": "NIFTY 50",
    "^NSEBANK": "NIFTY BANK",
    "^CRSLDX": "NIFTY 500",
    "^CNX200": "NIFTY 200",
    "^CNXIT": "NIFTY IT",
    "^CNXPHARMA": "NIFTY PHARMA",
    "^CNXAUTO": "NIFTY AUTO",
    "^CNXFMCG": "NIFTY FMCG",
    "^CNXMETAL": "NIFTY METAL",
}
INDEX_FALLBACK_SYMBOLS = {
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
# The app always loads these liquid/defensive overlays even when a strategy
# selects only equities. Include them in the same persistent FYERS cache.
ALWAYS_FETCH_TICKERS = [
    "GOLDBEES.NS", "LIQUIDBEES.NS", "SETFGSEC.NS",
    "NIFTYBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS",
]
INDEX_FILE_BY_TICKER = {
    ticker: index_history_data.INDEX_HISTORY_SPECS[name]["filename"]
    for ticker, name in index_history_data.TICKER_TO_INDEX_NAME.items()
}

OHLCV_COLUMNS = ["Date", "Open", "High", "Low", "Close", "Volume", "Source"]
MANIFEST_COLUMNS = [
    "App Ticker", "FYERS Symbol", "Asset Type", "First Date", "Last Date",
    "Rows", "Backfill Complete", "Next Backfill End", "Last Updated", "Status", "Message",
    "Expected Start", "Covers Expected Start",
    "Expected Launch", "Covers Launch",
]


class FatalAuthError(RuntimeError):
    """The configured FYERS credentials are missing/invalid."""


class HistoryRateLimit(RuntimeError):
    """FYERS has refused more history requests for the current time window."""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.0, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def get_access_token(app_id: str) -> str:
    """Use a direct access token, or attempt FYERS' refresh-token flow."""
    direct_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    refresh_token = os.getenv("FYERS_REFRESH_TOKEN", "").strip()
    secret_id = (
        os.getenv("FYERS_SECRET_ID", "").strip()
        or os.getenv("FYERS_SECRET_KEY", "").strip()
    )
    pin = os.getenv("FYERS_PIN", "").strip()

    # The Streamlit app writes the fresh access token into the GitHub Actions
    # secret immediately after OAuth login and dispatches this workflow. Prefer
    # that token so historical downloads need no separately stored FYERS PIN.
    if direct_token:
        print("Using the current FYERS access token supplied by the latest app login.")
        return direct_token

    # Legacy fallback remains available for a manually configured refresh-token flow.
    if refresh_token and secret_id and pin:
        app_hash = hashlib.sha256(f"{app_id}:{secret_id}".encode("utf-8")).hexdigest()
        try:
            response = requests.post(
                AUTH_API_URL,
                json={
                    "grant_type": "refresh_token",
                    "appIdHash": app_hash,
                    "refresh_token": refresh_token,
                    "pin": pin,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            body = response.json() if response.text else {}
            if response.ok and body.get("access_token"):
                print("FYERS access token refreshed successfully.")
                return str(body["access_token"])
            refresh_message = str(body.get("message") or response.text or "empty response")
            print(f"FYERS refresh-token flow was not accepted: {refresh_message[:500]}")
        except (requests.RequestException, ValueError) as exc:
            print(f"FYERS refresh-token request failed: {exc}")

    if direct_token:
        print("Using the FYERS_ACCESS_TOKEN secret (make sure it has not expired).")
        return direct_token

    if refresh_token:
        raise FatalAuthError(
            "FYERS refresh-token authentication failed. Verify FYERS_APP_ID, "
            "FYERS_SECRET_ID, FYERS_REFRESH_TOKEN and FYERS_PIN in GitHub Actions Secrets. "
            "FYERS refresh-token support/validity can change; renew credentials by signing in again."
        )
    raise FatalAuthError(
        "Missing FYERS credentials. Configure FYERS_APP_ID and either FYERS_ACCESS_TOKEN, "
        "or FYERS_SECRET_ID + FYERS_REFRESH_TOKEN + FYERS_PIN as GitHub Actions Secrets."
    )


def normalize_app_ticker(value) -> str | None:
    """Convert source membership symbols to the app's .NS ticker format."""
    if value is None or pd.isna(value):
        return None
    symbol = str(value).strip().upper()
    if not symbol or symbol in {"NAN", "NONE", "-", "N/A"}:
        return None
    if symbol.startswith("^"):
        return symbol
    if symbol.startswith("NSE:") or symbol.startswith("BSE:"):
        symbol = symbol.split(":", 1)[1]
    symbol = re.sub(r"\.(NS|BO)$", "", symbol)
    symbol = re.sub(r"-(EQ|BE|SM|INDEX)$", "", symbol)
    if not symbol:
        return None
    return f"{symbol}.NS"


def data_file_for_ticker(ticker: str) -> Path:
    """Return the durable path for either a stock/ETF or one of the indices."""
    if ticker in INDEX_FILE_BY_TICKER:
        return INDEX_DIR / INDEX_FILE_BY_TICKER[ticker]
    base = ticker[:-3] if ticker.endswith(".NS") else ticker
    stem = quote(base, safe="-_.")
    return PRICE_DIR / f"{stem}.csv"


def read_local_history(ticker: str) -> pd.DataFrame:
    path = data_file_for_ticker(ticker)
    if not path.exists():
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    # Refuse legacy official-index/Yahoo files. Only the explicit source stamp
    # proves that this history came through FYERS API.
    if "Source" not in frame.columns or not frame["Source"].fillna("").astype(str).str.upper().eq(SOURCE_NAME.upper()).all():
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce") if col in frame.columns else float("nan")
    frame["Source"] = SOURCE_NAME
    return (
        frame.dropna(subset=["Date", "Close"])
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")[OHLCV_COLUMNS]
        .reset_index(drop=True)
    )


def merge_and_write(ticker: str, old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """Merge candles by trading date, preferring newly returned FYERS values."""
    pieces = []
    if old is not None and not old.empty:
        pieces.append(old)
    if new is not None and not new.empty:
        pieces.append(new)
    if not pieces:
        return old if old is not None else pd.DataFrame(columns=OHLCV_COLUMNS)
    merged = pd.concat(pieces, ignore_index=True)
    merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce") if col in merged.columns else float("nan")
    merged["Source"] = SOURCE_NAME
    merged = (
        merged.dropna(subset=["Date", "Close"])
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")[OHLCV_COLUMNS]
        .reset_index(drop=True)
    )
    path = data_file_for_ticker(ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    merged.to_csv(tmp, index=False, date_format="%Y-%m-%d", float_format="%.6f")
    tmp.replace(path)
    return merged


def _normalize_candles(candles) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    frame = pd.DataFrame(candles)
    if frame.shape[1] < 5:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    names = ["Timestamp", "Open", "High", "Low", "Close", "Volume"]
    frame = frame.iloc[:, :6]
    frame.columns = names[:frame.shape[1]]
    frame["Date"] = (
        pd.to_datetime(pd.to_numeric(frame["Timestamp"], errors="coerce"), unit="s", utc=True)
        .dt.tz_convert("Asia/Kolkata")
        .dt.tz_localize(None)
        .dt.normalize()
    )
    for col in ("Open", "High", "Low", "Close"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    if "Volume" not in frame.columns:
        frame["Volume"] = float("nan")
    frame["Volume"] = pd.to_numeric(frame["Volume"], errors="coerce")
    frame["Source"] = SOURCE_NAME
    return frame.dropna(subset=["Date", "Close"])[OHLCV_COLUMNS].sort_values("Date").reset_index(drop=True)


class FyersHistoryClient:
    def __init__(self, app_id: str, token: str, max_requests: int, delay_seconds: float):
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"{app_id}:{token}",
            "Accept": "application/json",
            "User-Agent": "NSE-Momentum-Portfolio/1.0",
        })
        self.max_requests = max_requests
        self.delay_seconds = delay_seconds
        self.request_count = 0
        self.last_request_at = 0.0

    def history(self, fyers_symbol: str, start_date: date, end_date: date):
        """Return (candles dataframe, outcome, message); never silently treats auth errors as no data."""
        if self.request_count >= self.max_requests:
            raise HistoryRateLimit("Configured FYERS request budget for this run is exhausted.")
        now = time.monotonic()
        wait = self.delay_seconds - (now - self.last_request_at)
        if wait > 0:
            time.sleep(wait)
        self.last_request_at = time.monotonic()
        self.request_count += 1

        params = {
            "symbol": fyers_symbol,
            "resolution": "D",
            "date_format": "1",
            "range_from": start_date.isoformat(),
            "range_to": end_date.isoformat(),
        }
        for attempt in range(3):
            try:
                response = self.session.get(DATA_API_URL, params=params, timeout=45)
            except requests.RequestException as exc:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                return pd.DataFrame(columns=OHLCV_COLUMNS), "error", f"network: {exc}"
            if response.status_code == 429:
                return pd.DataFrame(columns=OHLCV_COLUMNS), "rate_limit", "HTTP 429 rate limit"
            if response.status_code in (401, 403):
                raise FatalAuthError(
                    f"FYERS rejected the token (HTTP {response.status_code}). Reconnect FYERS and update GitHub Actions Secrets."
                )
            if response.status_code >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            try:
                payload = response.json() if response.text else {}
            except ValueError:
                payload = {}
            if response.status_code >= 500:
                return pd.DataFrame(columns=OHLCV_COLUMNS), "error", f"HTTP {response.status_code}: {response.text[:300]}"
            state = str(payload.get("s", "")).lower()
            if response.ok and state == "ok":
                candles = _normalize_candles(payload.get("candles", []))
                return candles, ("ok" if not candles.empty else "empty"), ""
            message = str(payload.get("message") or payload.get("error") or response.text or "unknown FYERS API error")
            lower = message.lower()
            if any(x in lower for x in ("token", "unauthorised", "unauthorized", "authentication")):
                raise FatalAuthError(f"FYERS authentication error: {message[:500]}")
            if any(x in lower for x in ("rate limit", "request limit", "too many requests", "limit exceeded")):
                return pd.DataFrame(columns=OHLCV_COLUMNS), "rate_limit", message
            if any(x in lower for x in ("no data", "data not available", "invalid symbol", "symbol provided is invalid", "no candle")):
                return pd.DataFrame(columns=OHLCV_COLUMNS), "empty", message
            return pd.DataFrame(columns=OHLCV_COLUMNS), "error", message[:500]
        return pd.DataFrame(columns=OHLCV_COLUMNS), "error", "request retries exhausted"


def download_symbol_master() -> pd.DataFrame:
    try:
        response = requests.get(SYMBOL_MASTER_URL, timeout=45)
        response.raise_for_status()
        raw = pd.read_csv(pd.io.common.StringIO(response.text), header=None, dtype=str, on_bad_lines="skip")
        if raw.shape[1] <= 9:
            raise ValueError(f"Unexpected FYERS symbol-master width: {raw.shape[1]}")
        return pd.DataFrame({
            "Name": raw.iloc[:, 1].fillna("").astype(str).str.strip(),
            "FYERS Symbol": raw.iloc[:, 9].fillna("").astype(str).str.strip(),
        })
    except Exception as exc:
        print(f"Could not read FYERS symbol master; will use documented symbol-pattern fallbacks ({exc}).")
        return pd.DataFrame(columns=["Name", "FYERS Symbol"])


def _normal_name(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def build_symbol_map(master: pd.DataFrame) -> dict[str, str]:
    app_to_fyers = {}
    names = master["Name"].astype(str) if not master.empty else pd.Series(dtype=str)
    symbols = master["FYERS Symbol"].astype(str) if not master.empty else pd.Series(dtype=str)
    index_rows = [(n, s) for n, s in zip(names, symbols) if s.startswith("NSE:") and s.endswith("-INDEX")]
    for app_ticker, official_name in INDEX_NAMES.items():
        wanted = _normal_name(official_name)
        match = next((symbol for name, symbol in index_rows if _normal_name(name) == wanted), None)
        app_to_fyers[app_ticker] = match or INDEX_FALLBACK_SYMBOLS[app_ticker]

    by_base = {}
    for symbol in symbols:
        if not symbol.startswith("NSE:"):
            continue
        m = re.match(r"NSE:(.+)-(EQ|BE|SM)$", symbol)
        if not m:
            continue
        base, series = m.groups()
        rank = {"EQ": 0, "BE": 1, "SM": 2}[series]
        if base not in by_base or rank < by_base[base][0]:
            by_base[base] = (rank, symbol)
    for base, (_rank, symbol) in by_base.items():
        app_to_fyers[f"{base}.NS"] = symbol
    return app_to_fyers


def fetch_current_nifty500_symbols() -> list[str]:
    urls = [
        "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
        "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
        "https://archives.nseindia.com/content/indices/ind_nifty500list.csv",
    ]
    for url in urls:
        try:
            response = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.niftyindices.com/"},
                timeout=20,
            )
            response.raise_for_status()
            df = pd.read_csv(pd.io.common.StringIO(response.text), dtype=str)
            symbol_col = next((c for c in df.columns if str(c).strip().lower() == "symbol"), None)
            if symbol_col:
                return [f"{str(s).strip().upper()}.NS" for s in df[symbol_col].dropna() if str(s).strip()]
        except Exception:
            continue
    print("Current Nifty 500 constituent file was unavailable; using the stored history and ETF lists.")
    return []


def refresh_membership_ledger() -> bool:
    """Refresh point-in-time membership metadata; this is not a price source."""
    path = ROOT / "data" / "index_membership_history.csv"
    try:
        response = requests.get(MEMBERSHIP_URL, timeout=45)
        response.raise_for_status()
        candidate = pd.read_csv(pd.io.common.StringIO(response.text), dtype=str)
        required = {"index_name", "symbol", "valid_from", "valid_to"}
        if len(candidate) < 1000 or not required.issubset(candidate.columns):
            raise ValueError("Membership CSV did not pass structural checks.")
        parsed_dates = pd.to_datetime(candidate["valid_from"], errors="coerce")
        if parsed_dates.notna().sum() < 1000:
            raise ValueError("Membership CSV contains too few valid interval dates.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.text, encoding="utf-8")
        print(
            f"Membership metadata refreshed: {len(candidate)} intervals, "
            f"{candidate['index_name'].nunique()} indices."
        )
        return True
    except Exception as exc:
        print(f"Membership metadata refresh skipped; retaining existing file if present: {exc}")
        return path.exists()


def load_membership_symbols() -> list[str]:
    symbols = set()
    candidates = [
        (ROOT / "data" / "index_membership_history.csv", None),
        (None, MEMBERSHIP_URL),
        (None, LEGACY_MEMBERSHIP_URL),
    ]
    for local_path, url in candidates:
        try:
            if local_path is not None and local_path.exists():
                df = pd.read_csv(local_path, dtype=str)
            elif url:
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                df = pd.read_csv(pd.io.common.StringIO(response.text), dtype=str)
            else:
                continue
            col = next((c for c in df.columns if str(c).strip().lower() == "symbol"), None)
            if col:
                for value in df[col].dropna().tolist():
                    ticker = normalize_app_ticker(value)
                    if ticker and not ticker.startswith("^"):
                        symbols.add(ticker)
        except Exception as exc:
            print(f"Membership universe source unavailable ({url or local_path}): {exc}")
    return sorted(symbols)


def build_universe(master: pd.DataFrame):
    """Merge current Nifty 500, saved historical members, ETF groups and indices."""
    current = set(fetch_current_nifty500_symbols())
    historical = set(load_membership_symbols())
    etfs = set()
    for items in ETF_GROUP_SYMBOLS.values():
        for symbol in items:
            ticker = normalize_app_ticker(symbol)
            if ticker:
                etfs.add(ticker)
    for symbol in ALWAYS_FETCH_TICKERS:
        ticker = normalize_app_ticker(symbol)
        if ticker:
            etfs.add(ticker)

    # Include every ETF/BEES entry present in FYERS' current symbol master, not
    # only the user's curated ETF groups. The public master is refreshed daily.
    if not master.empty and {"Name", "FYERS Symbol"}.issubset(master.columns):
        for _name, raw_symbol in zip(master["Name"], master["FYERS Symbol"]):
            name = str(_name or "").strip()
            fyers_symbol = str(raw_symbol or "").strip().upper()
            if not fyers_symbol.startswith("NSE:"):
                continue
            name_match = bool(re.search(r"\bETF\b|BEES|EXCHANGE[\s-]*TRADED[\s-]*FUND", name, re.I))
            symbol_match = bool(re.search(r"ETF|BEES|LIQUIDCASE", fyers_symbol, re.I))
            if name_match or symbol_match:
                ticker = normalize_app_ticker(fyers_symbol)
                if ticker:
                    etfs.add(ticker)

    # Include any ticker represented in previous manifest, even if it has since
    # been delisted from the current symbol master.
    if MANIFEST_PATH.exists():
        try:
            old = pd.read_csv(MANIFEST_PATH, dtype=str)
            for ticker in old.get("App Ticker", pd.Series(dtype=str)).dropna():
                normalized = normalize_app_ticker(ticker)
                if normalized:
                    (historical if normalized not in current and normalized not in etfs else current).add(normalized)
        except Exception:
            pass
    indices = set(INDEX_NAMES)
    all_tickers = list(indices) + sorted(current - indices) + sorted(etfs - current) + sorted(historical - current - etfs - indices)
    priority = {}
    for ticker in indices:
        priority[ticker] = 0
    for ticker in current:
        priority[ticker] = 1
    for ticker in etfs:
        priority.setdefault(ticker, 2)
    for ticker in historical:
        priority.setdefault(ticker, 3)
    return list(dict.fromkeys(all_tickers)), priority


def read_manifest() -> dict[str, dict]:
    if not MANIFEST_PATH.exists():
        return {}
    try:
        frame = pd.read_csv(MANIFEST_PATH, dtype=str).fillna("")
    except (OSError, ValueError, pd.errors.ParserError):
        return {}
    return {str(row["App Ticker"]): row.to_dict() for _, row in frame.iterrows() if str(row.get("App Ticker", "")).strip()}


def manifest_row(ticker: str, fyers_symbol: str, asset_type: str, frame: pd.DataFrame,
                 backfill_complete: bool, status: str, message: str = "",
                 next_backfill_end: str = "") -> dict:
    if frame is None or frame.empty:
        first = last = ""
        rows = 0
    else:
        dates = pd.to_datetime(frame["Date"], errors="coerce").dropna()
        first = dates.min().date().isoformat() if not dates.empty else ""
        last = dates.max().date().isoformat() if not dates.empty else ""
        rows = len(frame)
    index_name = index_history_data.TICKER_TO_INDEX_NAME.get(ticker)
    expected_start = (
        str(index_history_data.INDEX_HISTORY_SPECS[index_name]["start_date"])
        if index_name else ""
    )
    expected_launch = (
        str(index_history_data.INDEX_HISTORY_SPECS[index_name].get("launch_date") or "")
        if index_name else ""
    )
    covers_expected_start = (
        bool(first)
        and bool(expected_start)
        and pd.Timestamp(first) <= pd.Timestamp(expected_start) + pd.Timedelta(days=7)
    )
    covers_launch = (
        bool(first)
        and bool(expected_launch)
        and pd.Timestamp(first) <= pd.Timestamp(expected_launch) + pd.Timedelta(days=7)
    )
    return {
        "App Ticker": ticker,
        "FYERS Symbol": fyers_symbol,
        "Asset Type": asset_type,
        "First Date": first,
        "Last Date": last,
        "Rows": rows,
        "Backfill Complete": bool(backfill_complete),
        "Next Backfill End": str(next_backfill_end or ""),
        "Last Updated": datetime.now().isoformat(timespec="seconds"),
        "Status": status,
        "Message": str(message)[:500],
        "Expected Start": expected_start,
        "Covers Expected Start": (
            "Yes" if covers_expected_start else ("No" if expected_start else "")
        ),
        "Expected Launch": expected_launch,
        "Covers Launch": (
            "Yes" if covers_launch else ("No" if expected_launch else "")
        ),
    }


def save_manifest(rows: dict[str, dict]):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(rows.values())
    for col in MANIFEST_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame = frame[MANIFEST_COLUMNS].sort_values("App Ticker")
    tmp = MANIFEST_PATH.with_suffix(".csv.tmp")
    frame.to_csv(tmp, index=False)
    tmp.replace(MANIFEST_PATH)


def main():
    app_id = os.getenv("FYERS_APP_ID", "").strip()
    if not app_id:
        raise FatalAuthError("Set FYERS_APP_ID in GitHub Actions Secrets before running the downloader.")
    token = get_access_token(app_id)

    max_requests = _env_int("FYERS_MAX_REQUESTS_PER_RUN", 4500)
    delay = _env_float("FYERS_REQUEST_DELAY_SECONDS", 1.25)
    today = date.today()
    configured_start = os.getenv("FYERS_HISTORY_START_DATE", "").strip()
    client = FyersHistoryClient(app_id, token, max_requests=max_requests, delay_seconds=delay)

    refresh_membership_ledger()
    print("Reading current FYERS symbol master...")
    master = download_symbol_master()
    ticker_map = build_symbol_map(master)
    universe, priority = build_universe(master)

    if configured_start:
        start_date = date.fromisoformat(configured_start)
        floor_note = "FYERS_HISTORY_START_DATE was explicitly configured."
    else:
        # Discover the account's accessible daily-history horizon once using
        # Nifty 50 data from 2010. Standard accounts should not return it; Prime
        # accounts with older-history access should. This avoids wasting thousands
        # of requests walking back through years the account cannot serve.
        probe_start, probe_end = date(2010, 1, 1), date(2010, 12, 31)
        try:
            probe, probe_outcome, probe_message = client.history(
                ticker_map.get("^NSEI", INDEX_FALLBACK_SYMBOLS["^NSEI"]),
                probe_start,
                probe_end,
            )
        except FatalAuthError:
            raise
        if probe_outcome == "ok" and not probe.empty and probe["Date"].min().date() < (pd.Timestamp(today) - pd.DateOffset(years=5)).date():
            start_date = date(1990, 1, 1)
            floor_note = "FYERS returned daily history from 2010; requesting the earliest available series back to 1990."
        else:
            start_date = (pd.Timestamp(today) - pd.DateOffset(years=5)).date()
            floor_note = (
                "FYERS did not return a 2010 Nifty 50 probe; using the rolling five-year floor. "
                "Set FYERS_HISTORY_START_DATE to override after confirming the account's history entitlement."
            )
            if probe_outcome == "error":
                print(f"History-window probe response: {probe_message}")

    def history_floor_for(ticker: str) -> date:
        """Use each index's earliest target when the account plan permits it."""
        index_name = index_history_data.TICKER_TO_INDEX_NAME.get(ticker)
        if not index_name:
            return start_date
        target = date.fromisoformat(index_history_data.INDEX_HISTORY_SPECS[index_name]["start_date"])
        # The plan's global history floor can be later than index inception.
        return max(start_date, target)

    print(f"Symbols queued: {len(universe)} (indices, current Nifty 500, all current FYERS ETFs, historical members).")
    print(f"Daily history request budget: {max_requests}; request spacing: {delay:.2f}s; earliest general history floor: {start_date}.")
    print(floor_note)
    print("Index backfills target their configured earliest series date when the FYERS plan exposes older history.")

    stored_manifest = read_manifest()
    run_manifest = dict(stored_manifest)
    # A previous run could have marked an index complete only at the Standard
    # plan's five-year floor. If this account now exposes older history, do not
    # let that old flag suppress the newly possible inception backfill.
    for ticker, entry in run_manifest.items():
        index_name = index_history_data.TICKER_TO_INDEX_NAME.get(ticker)
        if not index_name:
            continue
        expected_start = pd.Timestamp(
            index_history_data.INDEX_HISTORY_SPECS[index_name]["start_date"]
        )
        first_text = str(entry.get("First Date", "") or "").strip()
        try:
            first_saved = pd.Timestamp(first_text) if first_text else pd.NaT
        except (TypeError, ValueError):
            first_saved = pd.NaT
        covers_inception = pd.notna(first_saved) and (
            first_saved <= expected_start + pd.Timedelta(days=7)
        )
        if not covers_inception:
            entry["Backfill Complete"] = "False"
    local_frames = {}
    ticker_to_api = {}
    pending_retries = []
    failures = []
    stop_reason = ""

    def api_symbol_for(ticker: str) -> str:
        if ticker in ticker_map:
            return ticker_map[ticker]
        if ticker.startswith("^"):
            return INDEX_FALLBACK_SYMBOLS.get(ticker, "")
        base = ticker[:-3] if ticker.endswith(".NS") else ticker
        return f"NSE:{base}-EQ"

    def asset_type_for(ticker: str) -> str:
        if ticker.startswith("^"):
            return "Index"
        if ticker[:-3] in {str(s).upper() for group in ETF_GROUP_SYMBOLS.values() for s in group}:
            return "ETF"
        return "Equity / historical constituent"

    def get_frame(ticker: str) -> pd.DataFrame:
        if ticker not in local_frames:
            local_frames[ticker] = read_local_history(ticker)
        return local_frames[ticker]

    def process_request(ticker: str, from_date: date, to_date: date):
        fyers_symbol = ticker_to_api[ticker]
        if not fyers_symbol:
            return pd.DataFrame(columns=OHLCV_COLUMNS), "empty", "No FYERS symbol could be resolved."
        return client.history(fyers_symbol, from_date, to_date)

    # Phase 1: ensure recent data exists and keep current symbols up to date.
    # One recent call per symbol also seeds the latest year for brand-new files.
    for ticker in sorted(universe, key=lambda t: (priority.get(t, 4), t)):
        if client.request_count >= max_requests:
            stop_reason = "request budget reached during current/recent refresh"
            break
        fyers_symbol = api_symbol_for(ticker)
        ticker_to_api[ticker] = fyers_symbol
        old = get_frame(ticker)
        previous = run_manifest.get(ticker, {})
        complete = str(previous.get("Backfill Complete", "")).lower() in {"true", "1", "yes"}
        previous_cursor = str(previous.get("Next Backfill End", "") or "").strip()
        previous_status = str(previous.get("Status", "") or "").strip().lower()

        # A search cursor means this ticker has already proved it has no candles
        # in more recent windows. Keep searching backward instead of resetting the
        # cursor to the most recent year each time the daily workflow runs.
        if old.empty and previous_cursor:
            run_manifest[ticker] = manifest_row(
                ticker, fyers_symbol, asset_type_for(ticker), old, False,
                "search older history",
                str(previous.get("Message", "") or "Continue searching older FYERS history."),
                next_backfill_end=previous_cursor,
            )
            continue
        if old.empty and complete and previous_status == "no history":
            continue

        symbol_floor = history_floor_for(ticker)
        if old.empty:
            recent_start = max(symbol_floor, today - timedelta(days=365))
        else:
            latest = pd.to_datetime(old["Date"], errors="coerce").max().date()
            if (today - latest).days <= 10:
                recent_start = max(symbol_floor, latest - timedelta(days=7))
            elif (today - latest).days <= 365:
                recent_start = max(symbol_floor, latest - timedelta(days=7))
            else:
                # Bridge long outages forward in <=366-day windows. Repeated runs
                # continue from the latest stored date until current data is reached.
                recent_start = max(symbol_floor, latest - timedelta(days=7))
        recent_end = min(today, recent_start + timedelta(days=365))
        # If the selected span is older than the most recent data, a future run
        # will continue closing the gap. Do not request over 366 calendar days.
        try:
            new, outcome, message = process_request(ticker, recent_start, recent_end)
        except HistoryRateLimit:
            stop_reason = "FYERS reported its request limit"
            break
        if outcome == "rate_limit":
            stop_reason = f"FYERS rate limit reached: {message}"
            break
        if outcome == "error":
            failures.append({"App Ticker": ticker, "FYERS Symbol": fyers_symbol, "Stage": "recent", "Message": message})
            run_manifest[ticker] = manifest_row(ticker, fyers_symbol, asset_type_for(ticker), old, complete, "retry", message)
            pending_retries.append(ticker)
            continue
        if outcome == "empty":
            # Historical members may have been delisted years ago, so their
            # latest 365-day window can be empty even when older candles exist.
            # Persist a cursor and search older windows before declaring no history.
            if old.empty:
                if recent_start <= symbol_floor:
                    run_manifest[ticker] = manifest_row(
                        ticker, fyers_symbol, asset_type_for(ticker), old, True,
                        "no history", message or "No FYERS daily candles returned at the configured history floor.",
                    )
                else:
                    cursor = recent_start - timedelta(days=1)
                    run_manifest[ticker] = manifest_row(
                        ticker, fyers_symbol, asset_type_for(ticker), old, False,
                        "search older history",
                        message or "No candles in the recent window; continuing backward to find older listed history.",
                        next_backfill_end=cursor.isoformat(),
                    )
            else:
                run_manifest[ticker] = manifest_row(
                    ticker, fyers_symbol, asset_type_for(ticker), old, complete,
                    "retry", message or "No recent candles returned; existing data retained.",
                    next_backfill_end=str(previous.get("Next Backfill End", "") or ""),
                )
                pending_retries.append(ticker)
            continue
        local_frames[ticker] = merge_and_write(ticker, old, new)
        old = local_frames[ticker]
        if old.empty:
            run_manifest[ticker] = manifest_row(ticker, fyers_symbol, asset_type_for(ticker), old, True, "no history", "No usable daily candles.")
        else:
            first_date = pd.to_datetime(old["Date"], errors="coerce").min().date()
            reached_floor = first_date <= symbol_floor
            run_manifest[ticker] = manifest_row(ticker, fyers_symbol, asset_type_for(ticker), old, complete or reached_floor, "ok", "")
        if client.request_count and client.request_count % 100 == 0:
            print(f"Recent refresh progress: {client.request_count}/{max_requests} requests; {ticker} now has {len(old)} candles.")

    # Priority backfill: complete the Nifty 500 index-level series first.
    # This is a separate index series, not the 500 stock constituents. It is
    # requested from the official base date (1995-01-01) in <=366-day chunks,
    # before the request budget is spent on all other stock/ETF archives.
    nifty500_ticker = "^CRSLDX"
    if nifty500_ticker in universe and client.request_count < max_requests:
        nifty500_api_symbol = ticker_to_api.get(nifty500_ticker) or api_symbol_for(nifty500_ticker)
        ticker_to_api[nifty500_ticker] = nifty500_api_symbol
        nifty500_floor = history_floor_for(nifty500_ticker)
        frame = get_frame(nifty500_ticker)
        index_entry = run_manifest.get(nifty500_ticker, {})
        cursor_text = str(index_entry.get("Next Backfill End", "") or "").strip()

        if frame.empty and cursor_text:
            backfill_end = date.fromisoformat(cursor_text)
        elif not frame.empty:
            backfill_end = pd.to_datetime(frame["Date"], errors="coerce").min().date() - timedelta(days=1)
        else:
            backfill_end = today

        while client.request_count < max_requests:
            if not frame.empty:
                first_saved = pd.to_datetime(frame["Date"], errors="coerce").min().date()
                if first_saved <= nifty500_floor:
                    run_manifest[nifty500_ticker] = manifest_row(
                        nifty500_ticker, nifty500_api_symbol, "Index", frame,
                        True, "ok", "Configured Nifty 500 base date reached.",
                    )
                    save_manifest(run_manifest)
                    break
                backfill_end = first_saved - timedelta(days=1)

            from_date = max(nifty500_floor, backfill_end - timedelta(days=365))
            try:
                older, outcome, message = process_request(nifty500_ticker, from_date, backfill_end)
            except HistoryRateLimit:
                stop_reason = "FYERS request budget reached during priority Nifty 500 backfill"
                break

            if outcome == "rate_limit":
                stop_reason = f"FYERS rate limit during priority Nifty 500 backfill: {message}"
                break
            if outcome == "error":
                failures.append({
                    "App Ticker": nifty500_ticker,
                    "FYERS Symbol": nifty500_api_symbol,
                    "Stage": "Nifty 500 priority backfill",
                    "Message": message,
                })
                run_manifest[nifty500_ticker] = manifest_row(
                    nifty500_ticker, nifty500_api_symbol, "Index", frame,
                    False, "retry", message,
                    next_backfill_end=backfill_end.isoformat(),
                )
                save_manifest(run_manifest)
                break
            if outcome == "empty":
                # An empty old window may mean the account's history entitlement
                # stops here. Record the actual first date and let the coverage
                # audit prove that inception has not been reached.
                run_manifest[nifty500_ticker] = manifest_row(
                    nifty500_ticker, nifty500_api_symbol, "Index", frame,
                    True, "oldest available",
                    f"FYERS returned no Nifty 500 candles for {from_date} to {backfill_end}. "
                    f"Earliest saved date is {pd.to_datetime(frame['Date'], errors='coerce').min().date() if not frame.empty else 'none'}; "
                    "check the index coverage audit before treating the backfill as inception-complete. "
                    + str(message or ""),
                )
                save_manifest(run_manifest)
                break

            merged = merge_and_write(nifty500_ticker, frame, older)
            if merged.empty:
                run_manifest[nifty500_ticker] = manifest_row(
                    nifty500_ticker, nifty500_api_symbol, "Index", frame,
                    False, "retry", "API returned success but no usable Nifty 500 candles were parsed.",
                    next_backfill_end=backfill_end.isoformat(),
                )
                save_manifest(run_manifest)
                break

            new_first = pd.to_datetime(merged["Date"], errors="coerce").min().date()
            old_first = (
                pd.to_datetime(frame["Date"], errors="coerce").min().date()
                if not frame.empty else None
            )
            frame = merged
            local_frames[nifty500_ticker] = merged
            reached_base = new_first <= nifty500_floor
            run_manifest[nifty500_ticker] = manifest_row(
                nifty500_ticker, nifty500_api_symbol, "Index", merged,
                reached_base, "ok",
                "Configured Nifty 500 base date reached." if reached_base else "",
            )
            save_manifest(run_manifest)
            print(
                f"Nifty 500 priority backfill: {new_first} to {pd.to_datetime(merged['Date'], errors='coerce').max().date()} "
                f"({len(merged)} candles; {client.request_count}/{max_requests} API requests)."
            )
            if reached_base:
                break
            if old_first is not None and new_first >= old_first:
                run_manifest[nifty500_ticker] = manifest_row(
                    nifty500_ticker, nifty500_api_symbol, "Index", merged,
                    False, "retry", "Nifty 500 backfill made no backward progress.",
                    next_backfill_end=(new_first - timedelta(days=1)).isoformat(),
                )
                save_manifest(run_manifest)
                break
            backfill_end = new_first - timedelta(days=1)

    # Phase 2: backfill older history fairly, one <=366-day chunk per symbol per
    # pass. Re-running the workflow resumes from each file's earliest stored date.
    backfill_failed_this_run = set()
    while client.request_count < max_requests:
        pass_progress = False
        candidates = []
        for ticker in universe:
            frame = get_frame(ticker)
            entry = run_manifest.get(ticker, {})
            if ticker in backfill_failed_this_run:
                continue
            complete = str(entry.get("Backfill Complete", "")).lower() in {"true", "1", "yes"}
            if complete:
                continue
            if frame.empty:
                cursor = str(entry.get("Next Backfill End", "") or "").strip()
                if cursor:
                    candidates.append(ticker)
                continue
            first_date = pd.to_datetime(frame["Date"], errors="coerce").min().date()
            if first_date <= history_floor_for(ticker):
                run_manifest[ticker] = manifest_row(
                    ticker, ticker_to_api.get(ticker, api_symbol_for(ticker)),
                    asset_type_for(ticker), frame, True, "ok", "Configured earliest date reached.",
                )
                continue
            candidates.append(ticker)
        if not candidates:
            break
        candidates.sort(key=lambda t: (priority.get(t, 4), t))
        for ticker in candidates:
            if client.request_count >= max_requests:
                stop_reason = "request budget reached during historical backfill"
                break
            frame = get_frame(ticker)
            entry = run_manifest.get(ticker, {})
            if frame.empty:
                cursor_text = str(entry.get("Next Backfill End", "") or "").strip()
                if not cursor_text:
                    continue
                to_date = date.fromisoformat(cursor_text)
            else:
                first_date = pd.to_datetime(frame["Date"], errors="coerce").min().date()
                to_date = first_date - timedelta(days=1)
            from_date = max(history_floor_for(ticker), to_date - timedelta(days=365))
            try:
                older, outcome, message = process_request(ticker, from_date, to_date)
            except HistoryRateLimit:
                stop_reason = "request budget reached during historical backfill"
                break
            if outcome == "rate_limit":
                stop_reason = f"FYERS rate limit reached: {message}"
                break
            if outcome == "error":
                failures.append({"App Ticker": ticker, "FYERS Symbol": ticker_to_api[ticker], "Stage": "backfill", "Message": message})
                backfill_failed_this_run.add(ticker)
                run_manifest[ticker] = manifest_row(
                    ticker, ticker_to_api[ticker], asset_type_for(ticker), frame,
                    False, "retry", message,
                    next_backfill_end=str(entry.get("Next Backfill End", "") or ""),
                )
                continue
            if outcome == "empty":
                if frame.empty:
                    if from_date <= history_floor_for(ticker):
                        run_manifest[ticker] = manifest_row(
                            ticker, ticker_to_api[ticker], asset_type_for(ticker), frame,
                            True, "no history", message or "No older candles available at the configured floor.",
                        )
                    else:
                        next_cursor = from_date - timedelta(days=1)
                        run_manifest[ticker] = manifest_row(
                            ticker, ticker_to_api[ticker], asset_type_for(ticker), frame,
                            False, "search older history",
                            f"No candles from {from_date} to {to_date}; will search earlier history. {message}".strip(),
                            next_backfill_end=next_cursor.isoformat(),
                        )
                    pass_progress = True
                else:
                    run_manifest[ticker] = manifest_row(
                        ticker, ticker_to_api[ticker], asset_type_for(ticker), frame,
                        True, "oldest available", message or "No older FYERS candles in this window.",
                    )
                    pass_progress = True
                continue
            merged = merge_and_write(ticker, frame, older)
            local_frames[ticker] = merged
            first_merged = pd.to_datetime(merged["Date"], errors="coerce").min().date()
            reached_floor = first_merged <= history_floor_for(ticker) or from_date <= history_floor_for(ticker)
            run_manifest[ticker] = manifest_row(
                ticker, ticker_to_api[ticker], asset_type_for(ticker), merged,
                reached_floor, "ok", "Earliest configured date reached." if reached_floor else "",
            )
            pass_progress = True
        save_manifest(run_manifest)
        if stop_reason or not pass_progress:
            break

    # Save manifest for successful, empty, and deferred symbols alike.
    for ticker in universe:
        if ticker not in run_manifest:
            frame = get_frame(ticker)
            run_manifest[ticker] = manifest_row(
                ticker, ticker_to_api.get(ticker, api_symbol_for(ticker)), asset_type_for(ticker),
                frame, False, "pending", "Not processed during this run.",
            )
    save_manifest(run_manifest)

    if failures:
        failure_frame = pd.DataFrame(failures).drop_duplicates()
        if FAILURES_PATH.exists():
            try:
                old_failures = pd.read_csv(FAILURES_PATH)
                failure_frame = pd.concat([old_failures, failure_frame], ignore_index=True).drop_duplicates()
            except Exception:
                pass
        failure_frame.to_csv(FAILURES_PATH, index=False)

    summary = pd.read_csv(MANIFEST_PATH, dtype=str).fillna("")
    rows = pd.to_numeric(summary["Rows"], errors="coerce").fillna(0)
    complete = summary["Backfill Complete"].astype(str).str.lower().isin(["true", "1", "yes"])
    populated = rows.gt(0)
    print("")
    print("FYERS daily history update finished.")
    print(f"Requests this run: {client.request_count}/{max_requests}")
    print(f"Symbols with candles: {int(populated.sum())}; rows across symbols: {int(rows.sum()):,}")
    print(f"Backfill complete: {int((complete & populated).sum())}; still pending: {int((~complete & populated).sum())}")
    print(f"Manifest: {MANIFEST_PATH.relative_to(ROOT)}")
    if stop_reason:
        print(f"Run paused safely: {stop_reason}. Re-run the workflow to continue.")
    if not populated.any():
        raise RuntimeError(
            "No FYERS candles were stored. Check that FYERS historical-data permission is enabled, "
            "the token is valid, and the FYERS symbol mapping is accepted."
        )


if __name__ == "__main__":
    try:
        main()
    except FatalAuthError as exc:
        print(f"AUTHENTICATION ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
