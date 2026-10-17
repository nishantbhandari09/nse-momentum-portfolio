#!/usr/bin/env python3
"""Download NSE daily bhavcopy history for listed and delisted securities.

This is a supplemental NSE archive source, not the FYERS API. It is designed
for a separate historical research archive; the app's existing FYERS store
remains the primary broker-source dataset. The output includes daily OHLC,
volume, traded value and trade count. Adjusted close is a split/bonus-style
proxy derived from NSE previous close and is NOT total-return adjusted:
cash dividends are not included and corporate-action breaks need review.

Usage:
  python scripts/download_nse_history.py
  python scripts/download_nse_history.py --start-date 2010-01-01
  python scripts/download_nse_history.py --start-date 2026-10-01 --end-date 2026-10-16

The raw zip cache is resumable. Empty/failed days are NOT cached as permanent
misses, so transient errors and NSE holidays are retried on a later run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "nse_data"
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
SWITCH = dt.date(2024, 7, 8)
URL_OLD = "https://nsearchives.nseindia.com/content/historical/EQUITIES/{Y}/{M}/cm{D}{M}{Y}bhav.csv.zip"
URL_NEW = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{Y}{m}{D}_F_0000.csv.zip"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}
COLUMNS = [
    "date", "symbol", "series", "isin", "open", "high", "low", "close",
    "prev_close", "volume", "value", "trades",
]


def url_for(day: dt.date) -> str:
    if day >= SWITCH:
        return URL_NEW.format(Y=day.year, m=f"{day.month:02d}", D=f"{day.day:02d}")
    return URL_OLD.format(Y=day.year, M=day.strftime("%b").upper(), D=f"{day.day:02d}")


def fetch(day: dt.date) -> tuple[dt.date, bool, str]:
    path = RAW / f"{day.isoformat()}.zip"
    if path.exists() and path.stat().st_size > 0:
        try:
            with zipfile.ZipFile(path):
                return day, True, "cached"
        except zipfile.BadZipFile:
            path.unlink(missing_ok=True)
    session = requests.Session()
    session.headers.update(HEADERS)
    urls = [url_for(day), url_for(day).replace("nsearchives", "archives")]
    last_message = "No archive found (likely a non-trading day)"
    for url in urls:
        for attempt in range(3):
            try:
                response = session.get(url, timeout=35)
                if response.status_code == 200 and response.content[:2] == b"PK":
                    temp = path.with_suffix(".zip.tmp")
                    temp.write_bytes(response.content)
                    temp.replace(path)
                    time.sleep(0.12)
                    return day, True, "downloaded"
                if response.status_code in (403, 429):
                    last_message = f"HTTP {response.status_code}"
                    time.sleep(2 * (attempt + 1))
                    continue
                last_message = f"HTTP {response.status_code}"
                break
            except requests.RequestException as exc:
                last_message = str(exc)
                time.sleep(1 + attempt)
    # Do not create an empty marker: failures and holidays can be retried.
    return day, False, last_message


def parse(day: dt.date) -> pd.DataFrame | None:
    path = RAW / f"{day.isoformat()}.zip"
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names:
                return None
            frame = pd.read_csv(archive.open(names[0]), low_memory=False)
    except (OSError, zipfile.BadZipFile, pd.errors.ParserError, ValueError):
        return None
    frame.columns = [str(c).strip() for c in frame.columns]
    if "TckrSymb" in frame.columns:
        rename = {
            "TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin",
            "OpnPric": "open", "HghPric": "high", "LwPric": "low",
            "ClsPric": "close", "PrvsClsgPric": "prev_close",
            "TtlTradgVol": "volume", "TtlTrfVal": "value",
            "TtlNbOfTxsExctd": "trades",
        }
    else:
        rename = {
            "SYMBOL": "symbol", "SERIES": "series", "ISIN": "isin",
            "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close",
            "PREVCLOSE": "prev_close", "TOTTRDQTY": "volume",
            "TOTTRDVAL": "value", "TOTALTRADES": "trades",
        }
    frame = frame.rename(columns=rename)
    frame = frame.reindex(columns=COLUMNS[1:])
    frame.insert(0, "date", pd.Timestamp(day))
    for col in ("open", "high", "low", "close", "prev_close", "volume", "value", "trades"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    frame["series"] = frame["series"].astype("string").str.strip().str.upper()
    frame["isin"] = frame["isin"].astype("string").str.strip()
    return frame.dropna(subset=["symbol", "close"])


def adjust_equity_history(prices: pd.DataFrame) -> pd.DataFrame:
    """Create a split/bonus-adjusted OHLC close proxy per ISIN, without dividends."""
    eq_series = {"EQ", "BE", "BZ", "BT", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "NA"}
    eq = prices[prices["series"].isin(eq_series)].copy()
    eq = eq.dropna(subset=["isin", "close"]).sort_values(["isin", "date"])
    if eq.empty:
        eq["adj_factor"] = pd.Series(dtype=float)
        eq["adj_open"] = pd.Series(dtype=float)
        eq["adj_high"] = pd.Series(dtype=float)
        eq["adj_low"] = pd.Series(dtype=float)
        eq["adj_close"] = pd.Series(dtype=float)
        return eq
    prior_close = eq.groupby("isin", sort=False)["close"].shift(1)
    ratio = (eq["prev_close"] / prior_close).where(prior_close > 0)
    break_factor = ratio.where((ratio - 1).abs() > 0.02, 1.0).fillna(1.0)
    eq["_break_factor"] = break_factor
    eq["adj_factor"] = eq.groupby("isin", sort=False)["_break_factor"].transform(
        lambda values: values.iloc[::-1].shift(1, fill_value=1.0).cumprod().iloc[::-1]
    )
    for col in ("open", "high", "low", "close"):
        eq[f"adj_{col}"] = eq[col] * eq["adj_factor"]
    return eq.drop(columns="_break_factor")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="1994-11-03")
    parser.add_argument("--end-date", default=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    start, end = dt.date.fromisoformat(args.start_date), dt.date.fromisoformat(args.end_date)
    if end < start:
        parser.error("--end-date must be on or after --start-date")
    days = [
        start + dt.timedelta(days=n)
        for n in range((end - start).days + 1)
        if (start + dt.timedelta(days=n)).weekday() < 5
    ]
    todo = [day for day in days if not (RAW / f"{day.isoformat()}.zip").exists()]
    print(f"Weekdays in range: {len(days):,}; uncached dates: {len(todo):,}")
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = {pool.submit(fetch, day): day for day in todo}
        for count, future in enumerate(as_completed(futures), start=1):
            day, ok, message = future.result()
            if not ok:
                failures.append({"date": day.isoformat(), "message": message})
            if count % 250 == 0:
                print(f"Fetch progress: {count:,}/{len(todo):,}; failed dates: {len(failures):,}")

    frames = []
    for day in days:
        frame = parse(day)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        print("No bhavcopy files were parsed. Check NSE archive access and retry; no output was overwritten.")
        return 2
    prices = pd.concat(frames, ignore_index=True)
    prices = prices.drop_duplicates(["date", "isin", "symbol", "series"], keep="last")
    OUT.mkdir(parents=True, exist_ok=True)
    raw_path = OUT / "nse_prices.parquet"
    if raw_path.exists():
        try:
            previous = pd.read_parquet(raw_path)
            prices = pd.concat([previous, prices], ignore_index=True)
            prices = prices.drop_duplicates(["date", "isin", "symbol", "series"], keep="last")
        except Exception as exc:
            print(f"Existing parquet could not be merged ({exc}); using downloaded date range only.")
    prices = prices.sort_values(["date", "isin", "symbol"]).reset_index(drop=True)
    prices.to_parquet(raw_path, index=False)
    adjusted = adjust_equity_history(prices)
    adjusted.to_parquet(OUT / "nse_prices_adj.parquet", index=False)
    if failures:
        pd.DataFrame(failures).to_csv(OUT / "download_failures.csv", index=False)
    print(
        f"Saved {len(prices):,} daily security rows; {prices['isin'].nunique():,} ISINs; "
        f"{prices['symbol'].nunique():,} symbols; {prices['date'].min().date()} to {prices['date'].max().date()}."
    )
    print("Note: adjusted prices are a split/bonus proxy; dividends and every corporate action are not fully adjusted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
