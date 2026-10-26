"""Lazy downloader and reader for the permanent NSE adjusted-price Release.

Only the calendar years requested by a scan/backtest are downloaded. FYERS stays
primary; this archive fills historical gaps and includes securities no longer in
the current symbol master. Prices are split/bonus-adjustment proxies, not total
returns (cash dividends are not included).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DERIVED_DIR = ROOT / "nse_data" / "derived"
MANIFEST_PATH = DERIVED_DIR / "manifest.json"
RELEASE_BASE = os.environ.get(
    "NSE_RELEASE_BASE",
    "https://github.com/nishantbhandari09/nse-momentum-portfolio/releases/download/nse-data",
)
RELEASE_TAG = "nse-data"
USER_AGENT = "nse-momentum-portfolio-historical-data/1.0"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(filename: str, destination: Path, expected_sha256: str | None = None, timeout: int = 240) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    url = f"{RELEASE_BASE}/{filename}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/octet-stream"}
    with requests.get(url, headers=headers, stream=True, timeout=(15, timeout), allow_redirects=True) as response:
        if response.status_code == 404:
            raise FileNotFoundError(
                f"The NSE data Release asset '{filename}' does not exist yet. "
                "Run the 'Update all-stock NSE historical archive' workflow successfully first."
            )
        response.raise_for_status()
        with part.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    actual = _sha256(part)
    if expected_sha256 and actual.lower() != str(expected_sha256).lower():
        part.unlink(missing_ok=True)
        raise RuntimeError(f"Hash check failed for NSE data asset {filename}; please retry the download.")
    part.replace(destination)
    destination.with_suffix(destination.suffix + ".sha256").write_text(actual, encoding="ascii")
    return destination


def get_manifest(force_refresh: bool = False) -> dict:
    """Read a recent local manifest or refresh it from the public Release."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    stale_after = 6 * 60 * 60
    if MANIFEST_PATH.exists() and not force_refresh:
        age = max(0.0, time.time() - MANIFEST_PATH.stat().st_mtime)
        if age <= stale_after:
            try:
                return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
    try:
        _download("manifest.json", MANIFEST_PATH, timeout=60)
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        # Allow an older local manifest to support already-cached years during a
        # temporary outage, but never pretend that a missing archive is available.
        if MANIFEST_PATH.exists():
            try:
                return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        raise


def ensure_asset(filename: str, expected_sha256: str | None = None) -> Path:
    """Download an asset when missing or when its manifest hash has changed."""
    destination = DERIVED_DIR / filename
    digest_path = destination.with_suffix(destination.suffix + ".sha256")
    if destination.exists() and destination.stat().st_size > 0:
        try:
            recorded = digest_path.read_text(encoding="ascii").strip() if digest_path.exists() else _sha256(destination)
            if not digest_path.exists():
                digest_path.write_text(recorded, encoding="ascii")
            if not expected_sha256 or recorded.lower() == str(expected_sha256).lower():
                return destination
        except OSError:
            pass
    return _download(filename, destination, expected_sha256=expected_sha256)


def get_archive_status(refresh: bool = False) -> dict:
    """Small status dictionary used by the Streamlit UI."""
    try:
        manifest = get_manifest(force_refresh=refresh)
        years = manifest.get("years", {})
        downloaded = 0
        for year, row in years.items():
            p = DERIVED_DIR / str(row.get("asset", f"prices_adj_{year}.parquet"))
            sidecar = p.with_suffix(p.suffix + ".sha256")
            if p.exists() and sidecar.exists():
                try:
                    if sidecar.read_text(encoding="ascii").strip().lower() == str(row.get("sha256", "")).lower():
                        downloaded += 1
                except OSError:
                    pass
        return {
            "available": bool(years),
            "release_tag": manifest.get("release_tag", RELEASE_TAG),
            "years_published": len(years),
            "years_downloaded_locally": downloaded,
            "first_date": manifest.get("first_date"),
            "latest_date": manifest.get("last_date"),
            "total_symbols": manifest.get("total_symbols"),
            "total_rows": manifest.get("total_rows"),
            "generated_at": manifest.get("generated_at_utc"),
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False, "release_tag": RELEASE_TAG, "years_published": 0,
            "years_downloaded_locally": 0, "first_date": None, "latest_date": None,
            "total_symbols": 0, "total_rows": 0, "generated_at": None, "error": str(exc),
        }


def list_all_symbols() -> list[str]:
    """Return one canonical .NS ticker per security ID in the all-NSE archive.

    The source contains historical as well as current securities. When an ISIN
    changed ticker over time, the latest symbol is chosen so the archive's SID
    alias mapping can still recover that security's older price history.
    """
    manifest = get_manifest()
    symbols_path = ensure_asset(
        str(manifest.get("symbols_asset", "symbols.parquet")),
        manifest.get("symbols_sha256"),
    )
    try:
        frame = pd.read_parquet(symbols_path)
    except Exception as exc:
        raise RuntimeError(f"Could not read the NSE archive symbol index: {exc}") from exc
    required = {"symbol", "sid"}
    if not required.issubset(frame.columns):
        raise RuntimeError("The NSE archive symbol index is missing symbol or sid columns.")
    frame = frame.copy()
    frame["symbol"] = frame["symbol"].astype("string").str.strip().str.upper()
    frame["sid"] = frame["sid"].astype("string").str.strip().str.upper()
    frame = frame.dropna(subset=["symbol", "sid"])
    frame = frame[
        frame["symbol"].ne("")
        & frame["sid"].ne("")
        & ~frame["symbol"].isin(["NAN", "NONE", "N/A"])
    ]
    if frame.empty:
        return []
    if "last_date" in frame.columns:
        frame["last_date"] = pd.to_datetime(frame["last_date"], errors="coerce")
        frame = frame.sort_values(["last_date", "symbol"])
    # One label per underlying SID prevents old and new ticker aliases from
    # creating duplicate positions in a broad-universe backtest.
    frame = frame.drop_duplicates("sid", keep="last")
    symbols = frame["symbol"].drop_duplicates().tolist()
    return sorted(f"{symbol}.NS" for symbol in symbols if symbol and symbol != "NAN")


def _normalise_ticker(ticker: str) -> str:
    value = str(ticker).strip().upper()
    if value.endswith(".NS"):
        value = value[:-3]
    if value.startswith("NSE:"):
        value = value[4:]
    if value.endswith("-EQ"):
        value = value[:-3]
    return value.strip()


def load_close_prices(tickers: Iterable[str], start, end) -> pd.DataFrame:
    """Load adjusted closes for requested equity/ETF tickers from only needed years.

    Returned columns preserve the exact requested ticker labels (for example,
    RELIANCE.NS) so callers can merge the data with saved FYERS price panels.
    Index symbols beginning with '^' are not part of the NSE stock archive.
    """
    requested = list(dict.fromkeys(str(t).strip() for t in (tickers or []) if str(t).strip()))
    stock_requests = {t: _normalise_ticker(t) for t in requested if not str(t).strip().startswith("^")}
    stock_requests = {t: symbol for t, symbol in stock_requests.items() if symbol}
    if not stock_requests:
        return pd.DataFrame()
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if end_ts < start_ts:
        return pd.DataFrame()

    manifest = get_manifest()
    symbols_path = ensure_asset(
        str(manifest.get("symbols_asset", "symbols.parquet")),
        manifest.get("symbols_sha256"),
    )
    aliases_df = pd.read_parquet(symbols_path, columns=["symbol", "sid"])
    aliases_df["symbol"] = aliases_df["symbol"].astype("string").str.strip().str.upper()
    aliases_df["sid"] = aliases_df["sid"].astype("string").str.strip().str.upper()
    alias_map = dict(zip(aliases_df["symbol"], aliases_df["sid"]))
    sid_for_ticker = {ticker: alias_map.get(symbol, symbol) for ticker, symbol in stock_requests.items()}
    wanted_sids = sorted(set(sid_for_ticker.values()))
    if not wanted_sids:
        return pd.DataFrame()

    pieces = []
    years = manifest.get("years", {})
    for year in range(start_ts.year, end_ts.year + 1):
        info = years.get(str(year))
        if not info:
            continue
        asset = str(info.get("asset", f"prices_adj_{year}.parquet"))
        local = ensure_asset(asset, info.get("sha256"))
        try:
            frame = pd.read_parquet(
                local,
                columns=["date", "sid", "adj_close"],
                filters=[
                    ("date", ">=", start_ts.to_pydatetime()),
                    ("date", "<=", end_ts.to_pydatetime()),
                    ("sid", "in", wanted_sids),
                ],
            )
        except Exception:
            # Compatibility fallback if the Parquet engine cannot apply "in" filters.
            frame = pd.read_parquet(local, columns=["date", "sid", "adj_close"])
            date_values = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
            sid_values = frame["sid"].astype(str).str.upper()
            frame = frame.loc[
                date_values.between(start_ts, end_ts) & sid_values.isin(wanted_sids)
            ].copy()
            frame["date"] = date_values.loc[frame.index]
        if frame.empty:
            continue
        parsed = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
        frame = frame.copy()
        frame["date"] = parsed
        frame["sid"] = frame["sid"].astype(str).str.upper()
        frame["adj_close"] = pd.to_numeric(frame["adj_close"], errors="coerce")
        frame = frame.dropna(subset=["date", "adj_close"])
        frame = frame[frame["sid"].isin(wanted_sids)]
        if not frame.empty:
            pieces.append(frame)

    if not pieces:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="Date"))

    long = pd.concat(pieces, ignore_index=True).drop_duplicates(["date", "sid"], keep="last")
    wide = long.pivot(index="date", columns="sid", values="adj_close").sort_index()
    output = {}
    for ticker, sid in sid_for_ticker.items():
        if sid in wide.columns:
            output[ticker] = wide[sid]
    if not output:
        return pd.DataFrame(index=wide.index)
    result = pd.DataFrame(output, index=wide.index)
    result.index = pd.to_datetime(result.index, errors="coerce").tz_localize(None).normalize()
    result.index.name = "Date"
    return result.loc[(result.index >= start_ts) & (result.index <= end_ts)].sort_index()


def load_ohlcv(ticker: str, start=None, end=None) -> pd.DataFrame:
    """Load split/bonus-adjusted OHLC and raw volume/trades from the NSE release archive.

    The compact yearly assets are downloaded on demand and integrity-checked.
    Start defaults to five years before today, keeping per-symbol chart loads bounded.
    Legacy compact assets containing only adjusted closes remain usable.
    """
    value = str(ticker).strip().upper()
    output_columns = [
        "Date", "Symbol", "Open", "High", "Low", "Close", "Volume",
        "Traded Value", "Trades",
    ]
    if not value or value.startswith("^"):
        return pd.DataFrame(columns=output_columns)
    symbol = _normalise_ticker(value)
    manifest = get_manifest()
    symbols_path = ensure_asset(
        str(manifest.get("symbols_asset", "symbols.parquet")),
        manifest.get("symbols_sha256"),
    )
    aliases = pd.read_parquet(symbols_path, columns=["symbol", "sid"])
    aliases["symbol"] = aliases["symbol"].astype("string").str.strip().str.upper()
    aliases["sid"] = aliases["sid"].astype("string").str.strip().str.upper()
    alias_map = dict(zip(aliases["symbol"], aliases["sid"]))
    sid = alias_map.get(symbol, symbol)
    end_ts = pd.Timestamp(end).normalize() if end is not None else pd.Timestamp.today().normalize()
    start_ts = pd.Timestamp(start).normalize() if start is not None else end_ts - pd.DateOffset(years=5)
    if end_ts < start_ts:
        raise ValueError("End date must be on or after start date.")
    rows = []
    years = manifest.get("years", {})
    compact_columns = [
        "date", "sid", "adj_open", "adj_high", "adj_low", "adj_close",
        "volume", "turnover", "trades",
    ]
    for year in range(start_ts.year, end_ts.year + 1):
        info = years.get(str(year))
        if not info:
            continue
        path = ensure_asset(
            str(info.get("asset", f"prices_adj_{year}.parquet")),
            info.get("sha256"),
        )
        try:
            schema_frame = pd.read_parquet(path, engine="pyarrow")
            available = set(schema_frame.columns)
            del schema_frame
            columns = [column for column in compact_columns if column in available]
            if "date" not in columns or "sid" not in columns:
                continue
            frame = pd.read_parquet(
                path,
                columns=columns,
                filters=[
                    ("date", ">=", start_ts.to_pydatetime()),
                    ("date", "<=", end_ts.to_pydatetime()),
                    ("sid", "==", sid),
                ],
            )
        except Exception:
            try:
                frame = pd.read_parquet(path)
                if "sid" not in frame.columns or "date" not in frame.columns:
                    continue
                frame = frame.loc[
                    frame["sid"].astype(str).str.upper().eq(str(sid).upper())
                ].copy()
            except Exception:
                continue
        if not frame.empty:
            rows.append(frame)
    if not rows:
        return pd.DataFrame(columns=output_columns)
    frame = pd.concat(rows, ignore_index=True)
    frame["Date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True).dt.tz_localize(None).dt.normalize()
    frame["Symbol"] = value[:-3] if value.endswith(".NS") else value
    for source, target in [
        ("adj_open", "Open"), ("adj_high", "High"), ("adj_low", "Low"),
        ("adj_close", "Close"), ("volume", "Volume"),
        ("turnover", "Traded Value"), ("trades", "Trades"),
    ]:
        if source in frame:
            frame[target] = pd.to_numeric(frame[source], errors="coerce")
        else:
            frame[target] = float("nan")
    frame = frame.dropna(subset=["Date", "Close"])
    return (
        frame.loc[frame["Date"].between(start_ts, end_ts), output_columns]
        .sort_values("Date")
        .drop_duplicates("Date", keep="last")
        .reset_index(drop=True)
    )


def merge_price_panels(primary: pd.DataFrame | None, fallback: pd.DataFrame | None) -> pd.DataFrame:
    """Keep FYERS where it has candles; use NSE-adjusted history to fill gaps.

    For shared symbols, use a median overlap ratio to align NSE's adjusted
    series to the saved FYERS price level before filling missing dates.
    """
    if primary is None or primary.empty:
        return fallback.copy() if fallback is not None else pd.DataFrame()
    if fallback is None or fallback.empty:
        return primary.copy()
    p = primary.copy()
    f = fallback.copy()
    p.index = pd.to_datetime(p.index, errors="coerce", utc=True).tz_localize(None).normalize()
    f.index = pd.to_datetime(f.index, errors="coerce", utc=True).tz_localize(None).normalize()
    p = p.loc[~p.index.isna()].sort_index()
    f = f.loc[~f.index.isna()].sort_index()
    all_dates = p.index.union(f.index).sort_values()
    columns = list(dict.fromkeys(list(p.columns) + list(f.columns)))
    out = p.reindex(index=all_dates, columns=columns)
    f = f.reindex(index=all_dates)
    for column in f.columns:
        fallback_series = pd.to_numeric(f[column], errors="coerce")
        if column in p.columns:
            primary_series = pd.to_numeric(p[column].reindex(all_dates), errors="coerce")
            overlap = primary_series.notna() & fallback_series.notna() & fallback_series.ne(0)
            if overlap.any():
                ratio = (primary_series[overlap] / fallback_series[overlap]).dropna()
                ratio = ratio[(ratio > 0) & (ratio < float("inf"))]
                if not ratio.empty:
                    ratio = ratio.tail(120)
                    factor = float(ratio.median())
                    if pd.notna(factor) and factor > 0:
                        fallback_series = fallback_series * factor
            out[column] = primary_series.combine_first(fallback_series)
        else:
            out[column] = fallback_series
    return out.sort_index()
