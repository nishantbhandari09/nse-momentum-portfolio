"""Reader for the supplemental NSE bhavcopy archive.

The archive is produced by scripts/download_nse_history.py. Yearly parquet
partitions keep memory use bounded. FYERS remains the app's primary source;
NSE partitions add historical traded value/trade counts and securities that
may no longer be present in the current broker symbol master.
"""
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent
NSE_DATA_DIR = ROOT / "nse_data"
YEARLY_DIR = NSE_DATA_DIR / "yearly"
RAW_PATH = NSE_DATA_DIR / "nse_prices.parquet"
ADJ_PATH = NSE_DATA_DIR / "nse_prices_adj.parquet"


def _partition_paths(adjusted: bool = False) -> list[Path]:
    if YEARLY_DIR.exists():
        prefix = "nse_prices_adj_" if adjusted else "nse_prices_"
        paths = sorted(
            p for p in YEARLY_DIR.glob(f"{prefix}*.parquet")
            if p.is_file() and (adjusted or not p.name.startswith("nse_prices_adj_"))
        )
        if paths:
            return paths
    fallback = ADJ_PATH if adjusted and ADJ_PATH.exists() else RAW_PATH
    return [fallback] if fallback.exists() else []


def available() -> bool:
    return bool(_partition_paths(False))


def list_symbols() -> list[str]:
    symbols = set()
    for path in _partition_paths(False):
        try:
            frame = pd.read_parquet(path, columns=["symbol", "series"])
            if "series" in frame:
                frame = frame[frame["series"].astype(str).str.upper().isin(
                    ["EQ", "BE", "BZ", "BT", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "NA"]
                )]
            symbols.update(frame["symbol"].dropna().astype(str).str.strip().str.upper())
        except Exception:
            continue
    return sorted(symbols)


def load_stock_ohlcv(ticker: str, adjusted: bool = True) -> pd.DataFrame:
    """Load a symbol's daily OHLCV/trading statistics from the NSE archive."""
    symbol = str(ticker).strip().upper()
    if symbol.endswith(".NS"):
        symbol = symbol[:-3]
    frames = []
    for path in _partition_paths(adjusted):
        try:
            frame = pd.read_parquet(path, filters=[("symbol", "==", symbol)])
        except Exception:
            try:
                frame = pd.read_parquet(path)
                frame = frame[frame["symbol"].astype(str).str.upper().eq(symbol)]
            except Exception:
                continue
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    frame = pd.concat(frames, ignore_index=True)
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
