"""
Historical Nifty 500 (current + delisted) daily price data and point-in-time
index membership, sourced from the published dataset at:
https://github.com/rishabhjain0295-web/nifty500-momentum-backtest

That project downloaded and packaged: daily OHLCV back to 2008 for all 500
current Nifty 500 constituents plus ~470 additional symbols that were Nifty
500 members at some point since 1998 but have since been dropped from the
index (still tradable, so Yahoo Finance has their data) -- 970 symbols
total. It also built a point-in-time membership calendar from NSE's own
inclusion/exclusion log plus Wayback Machine snapshots, so a backtest can
know which stocks were ACTUALLY in the index on any given historical date,
rather than applying today's list backward across all history (which is
survivorship-biased -- stocks that got dropped from the index become
invisible, so the backtest only ever sees winners).

This module downloads that dataset once (cached to local disk + a Parquet
cache for fast reloads), then tops it up with a small incremental Yahoo
Finance fetch for whatever days have elapsed since the archive was last
built, so rankings reflect current data even though the bulk history is a
periodic snapshot someone else maintains.

Honest caveat: this makes the app depend on a third party's personal GitHub
repo staying up. If they ever delete or rename it, this stops working --
every function here fails open (returns empty / treats as eligible) rather
than crashing the app, and callers fall back to the original live-yfinance
approach when the archive isn't available.
"""
from io import BytesIO
from pathlib import Path
import zipfile

import pandas as pd
import requests
import streamlit as st

ARCHIVE_ZIP_URL = "https://github.com/rishabhjain0295-web/nifty500-momentum-backtest/releases/download/data-v1/stocks.zip"
MEMBERSHIP_CSV_URL = "https://raw.githubusercontent.com/rishabhjain0295-web/nifty500-momentum-backtest/master/data/nifty500_membership_calendar.csv"

DATA_DIR = Path("data/momentum_archive")
STOCKS_DIR = DATA_DIR / "stocks"
PARQUET_PATH = DATA_DIR / "_consolidated_close.parquet"


def ensure_archive_downloaded(timeout: int = 180) -> bool:
    """Downloads + extracts stocks.zip if not already present locally (a real
    download -- the archive is on the order of tens of MB, expect this to
    take a while the first time). Returns True if the archive ends up
    available (already-cached or freshly downloaded), False on any failure."""
    if STOCKS_DIR.exists() and any(STOCKS_DIR.glob("*.csv")):
        return True
    try:
        STOCKS_DIR.mkdir(parents=True, exist_ok=True)
        resp = requests.get(ARCHIVE_ZIP_URL, timeout=timeout)
        resp.raise_for_status()
        with zipfile.ZipFile(BytesIO(resp.content)) as zf:
            zf.extractall(STOCKS_DIR)
        return any(STOCKS_DIR.glob("*.csv"))
    except Exception as e:
        st.warning(
            f"Couldn't download the historical price archive ({e}); falling back "
            f"to live Yahoo Finance fetching only -- less history, no point-in-time membership."
        )
        return False


@st.cache_data(ttl=86400, show_spinner="Loading 2008-present price archive (one-time download, can take a minute)...")
def load_archive_prices(field: str = "Close") -> pd.DataFrame:
    """Wide (date x symbol) DataFrame built from the extracted archive CSVs,
    with ticker symbols suffixed '.NS' to match this app's existing
    convention. Empty DataFrame if the archive isn't available."""
    if not ensure_archive_downloaded():
        return pd.DataFrame()

    if field == "Close" and PARQUET_PATH.exists():
        try:
            return pd.read_parquet(PARQUET_PATH)
        except Exception:
            pass  # fall through and rebuild if the cache is somehow unreadable

    frames = {}
    for f in STOCKS_DIR.rglob("*.csv"):
        sym = f.stem.upper()
        try:
            df = pd.read_csv(f, index_col=0, parse_dates=True)
        except Exception:
            continue
        if field not in df.columns:
            continue
        s = df[field].dropna()
        if not s.empty:
            frames[sym + ".NS"] = s

    if not frames:
        return pd.DataFrame()
    wide = pd.DataFrame(frames).sort_index()

    if field == "Close":
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            wide.to_parquet(PARQUET_PATH, compression="zstd")
        except Exception:
            pass  # best-effort cache; a read-only filesystem shouldn't break loading
    return wide


@st.cache_data(ttl=86400, show_spinner="Loading point-in-time Nifty 500 membership calendar...")
def load_membership_calendar() -> pd.DataFrame:
    """(symbol, start, end) intervals -- end is NaT if still a current
    member. Empty DataFrame if it can't be fetched; callers should treat an
    empty calendar as 'no membership restriction available' rather than
    erroring out."""
    try:
        resp = requests.get(MEMBERSHIP_CSV_URL, timeout=30)
        resp.raise_for_status()
        cal = pd.read_csv(BytesIO(resp.content), parse_dates=["start", "end"])
        cal["symbol"] = cal["symbol"].str.upper() + ".NS"
        return cal
    except Exception as e:
        st.warning(
            f"Couldn't load the point-in-time membership calendar ({e}); backtests "
            f"will use today's Nifty 500 list applied across all history instead."
        )
        return pd.DataFrame(columns=["symbol", "start", "end"])


def was_member(symbol: str, as_of, calendar: pd.DataFrame) -> bool:
    """True if `symbol` was a Nifty 500 constituent on date `as_of`, per the
    calendar. Fails open to True if the calendar is empty (couldn't be
    loaded) or doesn't mention this symbol at all -- so a gap in the
    calendar excludes nothing by default, rather than silently dropping
    stocks it simply doesn't cover."""
    if calendar.empty:
        return True
    rows = calendar[calendar["symbol"] == symbol]
    if rows.empty:
        return True
    as_of = pd.Timestamp(as_of)
    for _, row in rows.iterrows():
        end = row["end"]
        if row["start"] <= as_of and (pd.isna(end) or as_of <= end):
            return True
    return False


def load_all_archive_symbols() -> list:
    """Every symbol with data in the archive (970 current + historical/
    delisted names) -- the right ticker universe for point-in-time-aware
    backtesting, as opposed to the ~500 names in today's live constituent
    list (which is all that's relevant for live scanning)."""
    return list(load_archive_prices("Close").columns)


def get_prices_with_live_topup(tickers, extra_tickers=None) -> pd.DataFrame:
    """Archive history for `tickers`, topped up with a small incremental
    Yahoo Finance fetch for whatever's happened since the archive's last
    date, so momentum rankings reflect current prices even though the bulk
    history is a periodic snapshot. `extra_tickers` (e.g. regime/defensive
    tickers not in the archive at all, like '^CRSLDX') are fetched live-only
    and merged in. Falls back to archive-only data if the top-up fetch fails
    -- a stale-by-a-few-days ranking beats a crashed page."""
    archive = load_archive_prices("Close")
    if archive.empty:
        return pd.DataFrame()

    cols = [t for t in tickers if t in archive.columns]
    relevant = archive[cols]
    last_archive_date = relevant.index.max()
    gap_days = (pd.Timestamp.today().normalize() - last_archive_date).days

    fetch_list = list(dict.fromkeys(cols + list(extra_tickers or [])))
    needs_topup = gap_days > 1 or extra_tickers

    if not needs_topup:
        return relevant

    try:
        import yfinance as yf
        topup = yf.download(
            tickers=fetch_list,
            start=(last_archive_date - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
            interval="1d", auto_adjust=True, progress=False, group_by="ticker", threads=True,
        )
        if isinstance(topup.columns, pd.MultiIndex):
            topup_close = pd.DataFrame({t: topup[t]["Close"] for t in fetch_list if t in topup.columns.get_level_values(0)})
        else:
            topup_close = topup[["Close"]].rename(columns={"Close": fetch_list[0]})

        combined = relevant.combine_first(topup_close)
        combined.update(topup_close)
        for extra in (extra_tickers or []):
            if extra in topup_close.columns and extra not in combined.columns:
                combined[extra] = topup_close[extra]
        return combined.sort_index()
    except Exception:
        return relevant
