"""Optional reader for the supplemental NSE bhavcopy archive.

The archive is produced by scripts/download_nse_history.py. FYERS remains the
primary saved data source; NSE data is used only when the parquet archive exists.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
NSE_DATA_DIR = ROOT / "nse_data"
RAW_PATH = NSE_DATA_DIR / "nse_prices.parquet"
ADJ_PATH = NSE_DATA_DIR / "nse_prices_adj.parquet"


def available() -> bool:
    return RAW_PATH.exists() and RAW_PATH.stat().st_size > 0


def list_symbols() -> list[str]:
    if not available():
        return []
    try:
        frame = pd.read_parquet(RAW_PATH, columns=["symbol", "series"])
        if "series" in frame:
            frame = frame[frame["series"].astype(str).str.upper().isin(
                ["EQ", "BE", "BZ", "BT", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "NA"]
            )]
        return sorted(set(frame["symbol"].dropna().astype(str).str.strip().str.upper()))
    except Exception:
        return []


def load_stock_ohlcv(ticker: str, adjusted: bool = True) -> pd.DataFrame:
    """Load a symbol's daily OHLCV/trading statistics from the NSE archive."""
    path = ADJ_PATH if adjusted and ADJ_PATH.exists() else RAW_PATH
    if not path.exists():
        return pd.DataFrame()
    symbol = str(ticker).strip().upper()
    if symbol.endswith(".NS"):
        symbol = symbol[:-3]
    try:
        frame = pd.read_parquet(path, filters=[("symbol", "==", symbol)])
    except Exception:
        try:
            frame = pd.read_parquet(path)
            frame = frame[frame["symbol"].astype(str).str.upper().eq(symbol)]
        except Exception:
            return pd.DataFrame()
    if frame.empty:
        return pd.DataFrame()
    frame["Date"] = pd.to_datetime(frame["date"], errors="coerce")
    mapping = {
        "open": "Open", "high": "High", "low": "Low", "close": "Close",
        "volume": "Volume", "value": "Traded Value", "trades": "Trades",
        "prev_close": "Previous Close", "adj_close": "Adjusted Close",
        "adj_open": "Adjusted Open", "adj_high": "Adjusted High", "adj_low": "Adjusted Low",
        "isin": "ISIN", "series": "Series", "symbol": "Symbol",
    }
    frame = frame.rename(columns=mapping)
    cols = [c for c in [
        "Date", "Symbol", "ISIN", "Series", "Open", "High", "Low", "Close",
        "Adjusted Open", "Adjusted High", "Adjusted Low", "Adjusted Close",
        "Previous Close", "Volume", "Traded Value", "Trades",
    ] if c in frame.columns]
    return frame[cols].dropna(subset=["Date", "Close"]).sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)
