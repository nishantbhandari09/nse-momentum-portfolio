"""Backfill/refresh official Nifty index history and sync PIT index membership.

Run by GitHub Actions after the market close. The script updates only CSV data;
it never changes strategy settings or app code.
"""
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import sys
import time

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from index_history_data import (
    INDEX_HISTORY_SPECS,
    INDEX_PRICE_DIR,
    MEMBERSHIP_PATH,
    MEMBERSHIP_URL,
    MEMBERSHIP_SNAPSHOT_DIR,
    MEMBERSHIP_SNAPSHOT_URLS,
)

TODAY = date.today()
MAX_RETRIES = 3


def fetch_official_index_year(index_name, start_date, end_date):
    """Fetch one year at a time; jugaad-data splits each year into monthly API calls."""
    from jugaad_data.nse import index_raw

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            rows = index_raw(index_name, start_date, end_date)
            if rows:
                return pd.DataFrame(rows)
            return pd.DataFrame()
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{index_name} {start_date} to {end_date}: {last_error}")


def normalise_api_rows(raw):
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    lower = {str(c).strip().lower().replace("_", " "): c for c in raw.columns}
    date_col = next((lower[k] for k in ("historicaldate", "historical date", "date") if k in lower), None)
    if date_col is None:
        raise ValueError(f"Official index response lacks a date column: {list(raw.columns)}")
    output = pd.DataFrame()
    output["Date"] = pd.to_datetime(raw[date_col], errors="coerce", dayfirst=True)
    for target in ("Open", "High", "Low", "Close"):
        col = lower.get(target.lower())
        if col is None:
            raise ValueError(f"Official index response lacks {target}: {list(raw.columns)}")
        output[target] = pd.to_numeric(
            raw[col].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    output = output.dropna(subset=["Date", "Close"])
    output["Date"] = output["Date"].dt.tz_localize(None).dt.normalize()
    return output.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def _read_existing(path):
    if not path.exists():
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close"])
    return normalise_api_rows(pd.read_csv(path))


def _year_chunks(start_date, end_date):
    cursor = start_date
    while cursor <= end_date:
        last_day = min(date(cursor.year, 12, 31), end_date)
        yield cursor, last_day
        cursor = last_day + timedelta(days=1)


def update_one_index(display_name, spec):
    path = INDEX_PRICE_DIR / spec["filename"]
    existing = _read_existing(path)
    configured_start = date.fromisoformat(spec["start_date"])
    if existing.empty:
        start_date = configured_start
    else:
        last_saved = pd.Timestamp(existing["Date"].max()).date()
        start_date = max(configured_start, last_saved - timedelta(days=14))

    fetched = []
    failed_chunks = []
    for chunk_start, chunk_end in _year_chunks(start_date, TODAY):
        try:
            raw = fetch_official_index_year(spec["official_name"], chunk_start, chunk_end)
            clean = normalise_api_rows(raw)
            if not clean.empty:
                fetched.append(clean)
        except Exception as exc:
            failed_chunks.append((chunk_start.isoformat(), str(exc)))
            print(f"WARNING: {display_name} {chunk_start.year} failed: {exc}", flush=True)

    # Retry incomplete historical years so one-off network failures can be repaired.
    if not existing.empty:
        first_year = max(configured_start.year, int(existing["Date"].dt.year.min()))
        last_full_year = TODAY.year - 1
        for year in range(first_year, last_full_year + 1):
            saved_count = int((existing["Date"].dt.year == year).sum())
            if saved_count >= 180:
                continue
            chunk_start = max(configured_start, date(year, 1, 1))
            chunk_end = date(year, 12, 31)
            try:
                clean = normalise_api_rows(
                    fetch_official_index_year(spec["official_name"], chunk_start, chunk_end)
                )
                if not clean.empty:
                    fetched.append(clean)
            except Exception as exc:
                print(f"WARNING: retry for {display_name} {year} failed: {exc}", flush=True)

    if fetched:
        merged = pd.concat([existing, *fetched], ignore_index=True)
        merged = merged.dropna(subset=["Date", "Close"])
        merged = merged.sort_values("Date").drop_duplicates("Date", keep="last")
        merged["Date"] = pd.to_datetime(merged["Date"]).dt.strftime("%Y-%m-%d")
        INDEX_PRICE_DIR.mkdir(parents=True, exist_ok=True)
        merged[["Date", "Open", "High", "Low", "Close"]].to_csv(path, index=False, float_format="%.4f")
        print(f"UPDATED {display_name}: {len(merged)} daily rows through {merged['Date'].max()}", flush=True)
    elif not existing.empty:
        print(f"UNCHANGED {display_name}: retaining {len(existing)} rows", flush=True)
    else:
        print(f"ERROR {display_name}: no usable historical prices returned", flush=True)
    if failed_chunks:
        print(f"WARNING {display_name}: {len(failed_chunks)} chunk(s) need retry", flush=True)
    return (not existing.empty) or bool(fetched)


def sync_membership_history():
    """Download PIT membership history; leave the current local file intact on failure."""
    try:
        response = requests.get(MEMBERSHIP_URL, timeout=60)
        response.raise_for_status()
        text = response.text
        candidate = pd.read_csv(StringIO(text))
        required = {"index_name", "symbol", "valid_from", "valid_to"}
        if len(candidate) < 1000 or not required.issubset(candidate.columns):
            raise ValueError("Downloaded membership file failed structural checks")
        MEMBERSHIP_PATH.parent.mkdir(parents=True, exist_ok=True)
        MEMBERSHIP_PATH.write_text(text, encoding="utf-8")
        print(
            f"UPDATED membership table: {len(candidate)} intervals across "
            f"{candidate['index_name'].nunique()} indices; latest interval date "
            f"{candidate['valid_from'].max()}",
            flush=True,
        )
        return True
    except Exception as exc:
        print(f"WARNING: membership source sync failed; using prior local file if present: {exc}", flush=True)
        return MEMBERSHIP_PATH.exists()


def sync_constituent_snapshots():
    """Sync periodic historical Nifty 200/500 member snapshots into the repo."""
    good = 0
    MEMBERSHIP_SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for index_name, url in MEMBERSHIP_SNAPSHOT_URLS.items():
        path = MEMBERSHIP_SNAPSHOT_DIR / f"{index_name.replace(' ', '_')}.csv"
        try:
            response = requests.get(url, timeout=60)
            response.raise_for_status()
            content = response.text
            candidate = pd.read_csv(StringIO(content), dtype={"date": str, "tickers": str})
            if not {"date", "tickers"}.issubset(candidate.columns) or len(candidate) < 100:
                raise ValueError("Snapshot file failed schema/row-count validation")
            parsed_dates = pd.to_datetime(candidate["date"], format="mixed", errors="coerce")
            if parsed_dates.notna().sum() < 100 or candidate["tickers"].fillna("").str.len().max() < 100:
                raise ValueError("Snapshot file contained too few valid dates or constituent lists")
            # Only replace local files after the complete response has passed validation.
            path.write_text(content, encoding="utf-8")
            latest_date = parsed_dates.max().date()
            print(
                f"UPDATED {index_name} constituent snapshots: {len(candidate)} rows; "
                f"latest snapshot {latest_date}",
                flush=True,
            )
            good += 1
        except Exception as exc:
            print(f"WARNING: {index_name} snapshot sync failed; preserving local file: {exc}", flush=True)
    return good > 0 or all(
        (MEMBERSHIP_SNAPSHOT_DIR / f"{name.replace(' ', '_')}.csv").exists()
        for name in MEMBERSHIP_SNAPSHOT_URLS
    )


def main():
    INDEX_PRICE_DIR.mkdir(parents=True, exist_ok=True)
    membership_ok = sync_membership_history()
    snapshot_ok = sync_constituent_snapshots()
    results = {}
    for display_name, spec in INDEX_HISTORY_SPECS.items():
        try:
            results[display_name] = update_one_index(display_name, spec)
        except Exception as exc:
            results[display_name] = False
            print(f"ERROR {display_name}: {exc}", flush=True)

    good_prices = sum(bool(v) for v in results.values())
    print(f"Index refresh result: {good_prices}/{len(results)} indices available.", flush=True)
    if good_prices == 0 and not membership_ok and not snapshot_ok:
        raise SystemExit("No index prices or membership data were available; refusing an empty commit.")
    if not any((INDEX_PRICE_DIR / spec["filename"]).exists() for spec in INDEX_HISTORY_SPECS.values()):
        raise SystemExit("No historical index price files exist after the refresh.")


if __name__ == "__main__":
    main()
