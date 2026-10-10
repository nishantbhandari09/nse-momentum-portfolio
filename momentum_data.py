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


def _extract_yahoo_close_frame(data, tickers):
    """Extract close prices robustly from yfinance's single/multi-ticker output."""
    if data is None or data.empty:
        return pd.DataFrame()

    tickers = list(dict.fromkeys(tickers))
    if isinstance(data.columns, pd.MultiIndex):
        result = {}
        level0 = set(data.columns.get_level_values(0))
        level1 = set(data.columns.get_level_values(1))
        if "Close" in level1:
            for ticker in tickers:
                if ticker in level0 and "Close" in data[ticker].columns:
                    result[ticker] = data[ticker]["Close"]
        elif "Close" in level0:
            close_data = data["Close"]
            for ticker in tickers:
                if ticker in close_data.columns:
                    result[ticker] = close_data[ticker]
        return pd.DataFrame(result, index=data.index)

    if "Close" in data.columns and len(tickers) == 1:
        return data[["Close"]].rename(columns={"Close": tickers[0]})
    if "Close" in data.columns and not tickers:
        return pd.DataFrame(index=data.index)
    return pd.DataFrame()


def _download_yahoo_close(tickers, period="5y", start=None, chunk_size=60):
    """Fetch close history in moderate batches so large ETF universes are manageable."""
    tickers = list(dict.fromkeys(ticker for ticker in tickers if ticker))
    if not tickers:
        return pd.DataFrame()

    import yfinance as yf

    frames = []
    for offset in range(0, len(tickers), chunk_size):
        batch = tickers[offset:offset + chunk_size]
        try:
            kwargs = {
                "tickers": batch,
                "interval": "1d",
                "auto_adjust": True,
                "progress": False,
                "group_by": "ticker",
                "threads": True,
            }
            if start is None:
                kwargs["period"] = period
            else:
                kwargs["start"] = start
            downloaded = yf.download(**kwargs)
            close_frame = _extract_yahoo_close_frame(downloaded, batch)
            if not close_frame.empty:
                frames.append(close_frame)
        except Exception:
            # A bad/renamed symbol should not prevent other symbols from loading.
            continue

    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, axis=1)
    result = result.loc[:, ~result.columns.duplicated()]
    return result.sort_index()


def get_prices_with_live_topup(tickers, extra_tickers=None, missing_period="5y") -> pd.DataFrame:
    """Load archive history, fetch full history for missing symbols, and refresh stale prices.

    ETFs often don't exist in the stock archive. They still need a multi-year
    history for momentum ranking and backtesting, not just a five-day price top-up.
    """
    archive = load_archive_prices("Close")
    if archive.empty:
        return pd.DataFrame()

    requested = list(dict.fromkeys(ticker for ticker in tickers if ticker))
    extras = list(dict.fromkeys(ticker for ticker in (extra_tickers or []) if ticker))
    all_needed = list(dict.fromkeys(requested + extras))
    archived_cols = [ticker for ticker in all_needed if ticker in archive.columns]
    relevant = archive[archived_cols].copy()
    last_archive_date = archive.index.max()
    if pd.isna(last_archive_date):
        return relevant

    missing_symbols = [ticker for ticker in all_needed if ticker not in archive.columns]

    # Backtests can request max available Yahoo history for non-archive symbols;
    # live scans keep the lighter five-year fetch by default.
    missing_history = _download_yahoo_close(missing_symbols, period=missing_period)

    # Incremental updates are only needed for symbols already covered by archive.
    gap_days = (pd.Timestamp.today().normalize() - last_archive_date).days
    incremental = pd.DataFrame()
    if gap_days > 1 and archived_cols:
        incremental = _download_yahoo_close(
            archived_cols,
            start=(last_archive_date - pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
        )

    combined = relevant
    if not missing_history.empty:
        combined = combined.combine_first(missing_history)
        combined.update(missing_history)
    if not incremental.empty:
        combined = combined.combine_first(incremental)
        combined.update(incremental)

    return combined.sort_index()
