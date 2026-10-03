from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Optional

import pandas as pd

from fyers_data import FyersClient, get_history_cached as _fyers_history_cached

ROOT = Path("data")
CACHE_DIRS = {
    "STOCK": ROOT / "prices",
    "INDEX": ROOT / "indices",
    "SECTOR": ROOT / "sectors",
    "ETF": ROOT / "etfs",
    "OTHER": ROOT / "prices",
}

# Legacy locations are read as fallback so existing downloaded history is preserved.
LEGACY_DIRS = {
    "STOCK": [ROOT / "prices"],
    "INDEX": [ROOT / "indices", ROOT / "prices"],
    "SECTOR": [ROOT / "indices", ROOT / "sectors", ROOT / "prices"],
    "ETF": [ROOT / "prices", ROOT / "etfs"],
    "OTHER": [ROOT / "prices"],
}

SECTOR_PATTERNS = (
    "AUTO", "AUTOMOBILE", "BANK", "FIN", "FINANCIAL", "FMCG", "IT",
    "INFORMATION TECHNOLOGY", "MEDIA", "METAL", "PHARMA", "HEALTHCARE",
    "REALTY", "ENERGY", "OIL", "GAS", "CONSUMER", "DEFENCE", "DEFENSE",
    "PSU BANK", "PVT BANK", "PRIVATE BANK", "INFRA", "COMMODITIES",
)


def clean_symbol(symbol: str) -> str:
    return str(symbol).replace(":", "_").replace("/", "_").replace("?", "_")


def classify_symbol(symbol: str, description: str = "", asset_type: str = "") -> str:
    """Classify a market instrument without needing a file per asset."""
    explicit = str(asset_type or "").upper().strip()
    if explicit in {"STOCK", "INDEX", "SECTOR", "ETF", "OTHER"}:
        if explicit == "INDEX":
            text = f"{symbol} {description}".upper()
            return "SECTOR" if any(term in text for term in SECTOR_PATTERNS) else "INDEX"
        return explicit

    text = f"{symbol} {description}".upper()
    if "-INDEX" in text or " INDEX" in text or text.endswith("INDEX"):
        return "SECTOR" if any(term in text for term in SECTOR_PATTERNS) else "INDEX"
    if "ETF" in text or "BEES" in text or "EXCHANGE TRADED" in text:
        return "ETF"
    if str(symbol).upper().endswith("-EQ"):
        return "STOCK"
    return "OTHER"


def cache_dir_for_symbol(symbol: str, description: str = "", asset_type: str = "") -> Path:
    return CACHE_DIRS[classify_symbol(symbol, description, asset_type)]


def _paths_for_symbol(symbol: str, description: str = "", asset_type: str = "") -> list[Path]:
    kind = classify_symbol(symbol, description, asset_type)
    stem = clean_symbol(symbol)
    primary = CACHE_DIRS[kind] / f"{stem}.parquet"
    primary_csv = CACHE_DIRS[kind] / f"{stem}.csv"
    paths = [primary, primary_csv]
    for directory in LEGACY_DIRS[kind]:
        paths.extend([directory / f"{stem}.parquet", directory / f"{stem}.csv"])
    # Preserve order and remove duplicates.
    seen = set()
    return [p for p in paths if not (str(p) in seen or seen.add(str(p)))]


def load_cached_history(symbol: str, description: str = "", asset_type: str = "") -> pd.DataFrame:
    for path in _paths_for_symbol(symbol, description, asset_type):
        try:
            if path.exists():
                if path.suffix == ".parquet":
                    from fyers_data import normalize_history_dates
                    return normalize_history_dates(pd.read_parquet(path))
                from fyers_data import normalize_history_dates
                return normalize_history_dates(pd.read_csv(path))
        except Exception:
            continue
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def save_cached_history(
    df: pd.DataFrame,
    symbol: str,
    description: str = "",
    asset_type: str = "",
) -> Path:
    target_dir = cache_dir_for_symbol(symbol, description, asset_type)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{clean_symbol(symbol)}.parquet"
    try:
        df.to_parquet(path, index=False)
        return path
    except Exception:
        csv_path = target_dir / f"{clean_symbol(symbol)}.csv"
        df.to_csv(csv_path, index=False)
        return csv_path


def get_history_cached(
    client: Optional[FyersClient],
    symbol: str,
    start,
    end,
    *,
    description: str = "",
    asset_type: str = "",
    force: bool = False,
) -> pd.DataFrame:
    """Provider-neutral cached history API.

    FYERS remains the primary provider in the current app. The public function is
    intentionally provider-neutral so another adapter can be added later without
    changing the scanner/strategy layer.
    """
    kind = classify_symbol(symbol, description, asset_type)
    target_dir = cache_dir_for_symbol(symbol, description, asset_type)
    target_dir.mkdir(parents=True, exist_ok=True)

    # Reuse the existing robust FYERS cache/fallback implementation. For sector
    # and ETF instruments, write into the new dedicated cache location.
    if client is None:
        return load_cached_history(symbol, description, asset_type)

    # If an older cache exists under data/prices or data/indices, seed the new
    # asset-specific location before asking the provider for missing ranges.
    legacy = load_cached_history(symbol, description, asset_type)
    if not legacy.empty:
        save_cached_history(legacy, symbol, description, kind)

    df = _fyers_history_cached(
        client,
        symbol,
        start,
        end,
        str(target_dir),
        force=force,
    )
    if df is not None and not df.empty:
        save_cached_history(df, symbol, description, kind)
        return df

    # If the new provider-specific location has no data, search legacy caches.
    return load_cached_history(symbol, description, asset_type)


def quotes(client: FyersClient, symbols: Iterable[str]) -> dict[str, Any]:
    """Common quote interface; the scanner never needs to know the provider."""
    return client.quotes(symbols)


def history(
    client: Optional[FyersClient],
    symbol: str,
    start,
    end,
    *,
    description: str = "",
    asset_type: str = "",
) -> pd.DataFrame:
    return get_history_cached(
        client,
        symbol,
        start,
        end,
        description=description,
        asset_type=asset_type,
    )
