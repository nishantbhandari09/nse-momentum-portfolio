"""Read the repository's durable daily OHLCV archive downloaded only from FYERS API."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote
import pandas as pd

ROOT = Path(__file__).resolve().parent
PRICE_DIR = ROOT / "data" / "fyers_history"
MANIFEST_PATH = ROOT / "data" / "fyers_history_manifest.csv"
SOURCE_NAME = "FYERS API"


def _file_stem(ticker: str) -> str:
    value = str(ticker).strip()
    if value.endswith(".NS"):
        value = value[:-3]
    if value.startswith("^"):
        return "INDEX_" + quote(value[1:], safe="-_.")
    return quote(value, safe="-_.")


def price_file_for_ticker(ticker: str) -> Path:
    """Return the per-symbol file path used by the updater."""
    return PRICE_DIR / f"{_file_stem(ticker)}.csv"


def _read_fyers_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "Source"])
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "Source"])
    if "Source" not in frame.columns:
        # Do not accidentally mix previously cached Yahoo/NSE prices into a
        # backtest that is meant to use only the FYERS-sourced dataset.
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "Source"])
    source = frame["Source"].fillna("").astype(str).str.strip().str.upper()
    if not source.eq(SOURCE_NAME.upper()).all():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "Source"])
    if "Date" not in frame.columns or "Close" not in frame.columns:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume", "Source"])
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        else:
            frame[col] = float("nan")
    frame = frame.dropna(subset=["Date", "Close"])
    return frame.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def _load_index_ohlc(ticker: str) -> pd.DataFrame:
    try:
        import index_history_data
        return index_history_data.load_index_ohlc(ticker)
    except Exception:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])


def load_ohlcv(ticker: str) -> pd.DataFrame:
    """Return stored FYERS daily OHLCV for an app ticker (e.g. RELIANCE.NS or ^NSEI)."""
    value = str(ticker).strip()
    if value.startswith("^"):
        return _load_index_ohlc(value)
    return _read_fyers_csv(price_file_for_ticker(value))


def load_close_prices(tickers=None) -> pd.DataFrame:
    """Return a date x app-ticker matrix of stored FYERS closes; never fetches Yahoo data."""
    if tickers is None:
        tickers = load_all_symbols()
    unique = list(dict.fromkeys(str(t).strip() for t in tickers if str(t).strip()))
    columns = {}
    for ticker in unique:
        data = load_ohlcv(ticker)
        if data.empty:
            continue
        series = data.set_index("Date")["Close"].dropna()
        if not series.empty:
            columns[ticker] = series
    if not columns:
        return pd.DataFrame()
    return pd.DataFrame(columns).sort_index()


def load_all_symbols() -> list[str]:
    """List all symbols with a valid FYERS history file, including stored indices."""
    symbols = []
    if PRICE_DIR.exists():
        for path in PRICE_DIR.glob("*.csv"):
            frame = _read_fyers_csv(path)
            if frame.empty:
                continue
            stem = path.stem
            symbols.append(f"{stem}.NS")
    try:
        import index_history_data
        for ticker in index_history_data.TICKER_TO_INDEX_NAME:
            if not index_history_data.load_index_ohlc(ticker).empty:
                symbols.append(ticker)
    except Exception:
        pass
    return sorted(set(symbols))


def get_data_summary() -> dict:
    """Small summary suitable for the Streamlit dashboard."""
    if not MANIFEST_PATH.exists():
        return {
            "symbols": 0, "rows": 0, "oldest_date": None,
            "latest_date": None, "backfill_complete": 0, "backfill_pending": 0,
        }
    try:
        manifest = pd.read_csv(MANIFEST_PATH)
    except (OSError, ValueError, pd.errors.ParserError):
        manifest = pd.DataFrame()
    if manifest.empty:
        return {
            "symbols": 0, "rows": 0, "oldest_date": None,
            "latest_date": None, "backfill_complete": 0, "backfill_pending": 0,
        }
    for col in ("First Date", "Last Date"):
        if col in manifest.columns:
            manifest[col] = pd.to_datetime(manifest[col], errors="coerce")
    complete = (
        manifest["Backfill Complete"].astype(str).str.lower().isin(["true", "1", "yes"])
        if "Backfill Complete" in manifest.columns else pd.Series(False, index=manifest.index)
    )
    return {
        "symbols": int(manifest.get("Rows", pd.Series(dtype=float)).gt(0).sum()) if "Rows" in manifest else 0,
        "rows": int(pd.to_numeric(manifest.get("Rows", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()),
        "oldest_date": manifest["First Date"].min().date().isoformat() if "First Date" in manifest and manifest["First Date"].notna().any() else None,
        "latest_date": manifest["Last Date"].max().date().isoformat() if "Last Date" in manifest and manifest["Last Date"].notna().any() else None,
        "backfill_complete": int(complete.sum()),
        "backfill_pending": int((~complete).sum()),
    }
