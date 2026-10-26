#!/usr/bin/env python3
"""Build compact, adjusted yearly NSE OHLCV panels for release download by Streamlit.

Input: nse_data/yearly/nse_prices_adj_YYYY.parquet (built by download_nse_history.py).
Output: nse_data/derived/prices_adj_YYYY.parquet with adjusted OHLC, adjusted close,
raw volume, turnover and trade counts, plus symbols.parquet and manifest.json.
The archive is stored as Release assets and downloaded by the app on demand.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
YEARLY = ROOT / "nse_data" / "yearly"
OUT = ROOT / "nse_data" / "derived"
EQUITY_SERIES = {"EQ", "BE", "BZ", "BT", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "NA", "SM", "ST"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalise_dates(frame: pd.DataFrame) -> pd.DataFrame:
    parsed = pd.to_datetime(frame["date"], errors="coerce", utc=True)
    frame = frame.copy()
    frame["date"] = parsed.dt.tz_localize(None).dt.normalize()
    return frame.dropna(subset=["date"])


def _symbol_aliases(adjusted_files: list[Path]) -> tuple[dict[str, str], pd.DataFrame]:
    parts = []
    for path in adjusted_files:
        meta = pd.read_parquet(path, columns=["date", "symbol", "series", "isin"])
        meta = _normalise_dates(meta)
        meta["symbol"] = meta["symbol"].astype("string").str.strip().str.upper()
        meta["isin"] = meta["isin"].astype("string").str.strip()
        meta["series"] = meta["series"].astype("string").str.strip().str.upper()
        meta = meta[meta["series"].isin(EQUITY_SERIES) & meta["symbol"].notna()]
        meta = meta.dropna(subset=["isin"])
        if meta.empty:
            continue
        parts.append(
            meta.groupby(["isin", "symbol"], as_index=False, dropna=True)["date"]
            .agg(first_date="min", last_date="max")
        )
    if not parts:
        raise RuntimeError("No adjusted NSE equity rows with ISINs were found.")
    stats = pd.concat(parts, ignore_index=True)
    stats = stats.groupby(["isin", "symbol"], as_index=False, dropna=True).agg(
        first_date=("first_date", "min"), last_date=("last_date", "max")
    )

    aliases: dict[str, str] = {}
    # Chain symbols that traded under the same ISIN in successive, non-overlapping periods.
    for _, group in stats.sort_values(["first_date", "last_date"]).groupby("isin", sort=False):
        rows = group.sort_values("first_date").to_dict("records")
        for left, right in zip(rows, rows[1:]):
            old, new = str(left["symbol"]), str(right["symbol"])
            if old != new and pd.Timestamp(left["last_date"]) <= pd.Timestamp(right["first_date"]) + pd.Timedelta(days=7):
                aliases[old] = new

    def latest_symbol(symbol: str) -> str:
        current = symbol
        seen = set()
        for _ in range(20):
            if current not in aliases or current in seen:
                break
            seen.add(current)
            current = aliases[current]
        return current

    stats["sid"] = stats["symbol"].astype(str).map(latest_symbol)
    symbol_rows = stats.sort_values("last_date").drop_duplicates("symbol", keep="last")
    symbols = symbol_rows[["symbol", "sid", "isin", "first_date", "last_date"]].copy()
    symbols["first_date"] = pd.to_datetime(symbols["first_date"]).dt.strftime("%Y-%m-%d")
    symbols["last_date"] = pd.to_datetime(symbols["last_date"]).dt.strftime("%Y-%m-%d")
    return aliases, symbols


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    adjusted_files = sorted(
        path for path in YEARLY.glob("nse_prices_adj_*.parquet")
        if path.is_file()
    )
    if not adjusted_files:
        raise FileNotFoundError(
            f"No adjusted yearly NSE partitions found in {YEARLY}. Run scripts/download_nse_history.py first."
        )

    aliases, symbols = _symbol_aliases(adjusted_files)
    symbols_path = OUT / "symbols.parquet"
    symbols.to_parquet(symbols_path, index=False, compression="zstd")
    years: dict[str, dict] = {}
    all_first, all_last = [], []
    total_rows = 0

    for source_path in adjusted_files:
        year = source_path.stem.rsplit("_", 1)[-1]
        frame = pd.read_parquet(
            source_path,
            columns=[
                "date", "symbol", "series", "isin", "adj_open", "adj_high",
                "adj_low", "adj_close", "open", "high", "low", "close",
                "volume", "value", "trades",
            ],
        )
        frame = _normalise_dates(frame)
        frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
        frame["series"] = frame["series"].astype("string").str.strip().str.upper()
        frame = frame[frame["series"].isin(EQUITY_SERIES)].copy()

        # Store compact, split/bonus-adjusted OHLC along with close prices,
        # raw traded volume, turnover and trade counts. This lets scans and
        # in-app charts reuse one all-NSE archive rather than one CSV per stock.
        for adjusted_col, raw_col in (
            ("adj_open", "open"), ("adj_high", "high"),
            ("adj_low", "low"), ("adj_close", "close"),
        ):
            if adjusted_col not in frame.columns:
                frame[adjusted_col] = pd.to_numeric(frame.get(raw_col), errors="coerce")
            else:
                frame[adjusted_col] = pd.to_numeric(frame[adjusted_col], errors="coerce")
        for column in ("volume", "value", "trades"):
            if column not in frame.columns:
                frame[column] = float("nan")
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["turnover"] = frame["value"]
        frame = frame.dropna(subset=["date", "symbol", "adj_close"])
        frame = frame[frame["adj_close"] > 0]
        if frame.empty:
            continue
        frame["sid"] = frame["symbol"].astype(str).map(aliases).fillna(frame["symbol"].astype(str))
        frame = frame.sort_values(["date", "turnover"]).drop_duplicates(["date", "sid"], keep="last")
        compact = frame[[
            "date", "sid", "adj_open", "adj_high", "adj_low", "adj_close",
            "volume", "turnover", "trades",
        ]].copy()
        compact["sid"] = compact["sid"].astype("string")
        for column in ("adj_open", "adj_high", "adj_low", "adj_close"):
            compact[column] = compact[column].astype("float32")
        for column in ("volume", "turnover", "trades"):
            compact[column] = compact[column].astype("float64")
        compact = compact.sort_values(["date", "sid"]).reset_index(drop=True)
        out_path = OUT / f"prices_adj_{year}.parquet"
        compact.to_parquet(out_path, index=False, compression="zstd", row_group_size=250_000)
        digest = sha256_file(out_path)
        years[year] = {
            "asset": out_path.name,
            "sha256": digest,
            "rows": int(len(compact)),
            "symbols": int(compact["sid"].nunique()),
            "first_date": str(compact["date"].min().date()),
            "last_date": str(compact["date"].max().date()),
            "bytes": int(out_path.stat().st_size),
        }
        total_rows += len(compact)
        all_first.append(compact["date"].min())
        all_last.append(compact["date"].max())
        print(f"{year}: {len(compact):,} adjusted rows, {compact['sid'].nunique():,} symbol series, {out_path.stat().st_size:,} bytes")

    manifest = {
        "release_tag": "nse-data",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "NSE daily bhavcopy archive; split/bonus adjustment proxy; cash dividends not included",
        "symbols_asset": symbols_path.name,
        "symbols_sha256": sha256_file(symbols_path),
        "symbols_rows": int(len(symbols)),
        "first_date": str(min(all_first).date()) if all_first else None,
        "last_date": str(max(all_last).date()) if all_last else None,
        "total_rows": int(total_rows),
        "total_symbols": int(symbols["sid"].nunique()),
        "years": years,
    }
    manifest_path = OUT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"Built release manifest: {len(years)} years; {total_rows:,} rows; "
        f"{manifest['total_symbols']:,} symbol series; {manifest['first_date']} to {manifest['last_date']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
