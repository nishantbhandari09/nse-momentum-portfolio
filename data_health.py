"""Historical-data quality checks for saved FYERS daily stock/ETF candles."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pandas as pd

import fyers_history_data

ROOT = Path(__file__).resolve().parent


def audit_fyers_price_files(max_stale_days: int = 7) -> pd.DataFrame:
    """Audit raw FYERS equity/ETF CSVs without hiding duplicates via normalization."""
    price_dir = fyers_history_data.PRICE_DIR
    if not price_dir.exists():
        return pd.DataFrame(columns=[
            "Symbol", "Candles", "First Date", "Last Date", "Days Since Update",
            "Duplicate Dates", "Invalid OHLC Rows", "Gaps >7d", "Close Moves >25%",
            "Source Valid", "Health Status",
        ])

    today = pd.Timestamp.now().normalize()
    records = []
    for path in sorted(price_dir.glob("*.csv")):
        # Index series are stored separately under data/index_prices.
        raw_symbol = unquote(path.stem)
        if raw_symbol.startswith("INDEX_"):
            continue
        symbol = raw_symbol if raw_symbol.endswith(".NS") else f"{raw_symbol}.NS"
        try:
            frame = pd.read_csv(path)
        except (OSError, ValueError, pd.errors.ParserError) as exc:
            records.append({
                "Symbol": symbol, "Candles": 0, "First Date": None, "Last Date": None,
                "Days Since Update": None, "Duplicate Dates": None, "Invalid OHLC Rows": None,
                "Gaps >7d": None, "Close Moves >25%": None, "Source Valid": False,
                "Health Status": f"READ ERROR: {type(exc).__name__}",
            })
            continue

        source_ok = (
            "Source" in frame.columns
            and not frame.empty
            and frame["Source"].fillna("").astype(str).str.strip().str.upper().eq("FYERS API").all()
        )
        if not source_ok:
            records.append({
                "Symbol": symbol, "Candles": int(len(frame)), "First Date": None, "Last Date": None,
                "Days Since Update": None, "Duplicate Dates": None, "Invalid OHLC Rows": None,
                "Gaps >7d": None, "Close Moves >25%": None, "Source Valid": False,
                "Health Status": "INVALID / MISSING FYERS SOURCE MARKER",
            })
            continue

        if "Date" not in frame.columns:
            records.append({
                "Symbol": symbol, "Candles": int(len(frame)), "First Date": None, "Last Date": None,
                "Days Since Update": None, "Duplicate Dates": None, "Invalid OHLC Rows": None,
                "Gaps >7d": None, "Close Moves >25%": None, "Source Valid": True,
                "Health Status": "MISSING DATE COLUMN",
            })
            continue

        dates = pd.to_datetime(frame["Date"], errors="coerce").dt.tz_localize(None).dt.normalize()
        duplicate_dates = int(dates.duplicated(keep=False).sum())
        for col in ("Open", "High", "Low", "Close"):
            if col not in frame.columns:
                frame[col] = np.nan
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        good_dates = dates.notna()
        candles = frame.loc[good_dates].copy()
        candles["_Date"] = dates[good_dates]
        candles = candles.sort_values("_Date")
        ohlc = candles[["Open", "High", "Low", "Close"]]
        valid = (
            ohlc.notna().all(axis=1)
            & np.isfinite(ohlc).all(axis=1)
            & ohlc.gt(0).all(axis=1)
            & (ohlc["High"] >= ohlc[["Open", "Close"]].max(axis=1))
            & (ohlc["Low"] <= ohlc[["Open", "Close"]].min(axis=1))
            & (ohlc["High"] >= ohlc["Low"])
        )
        invalid_ohlc = int((~valid).sum())
        unique_dates = pd.DatetimeIndex(candles["_Date"].drop_duplicates().sort_values())
        gaps = int((unique_dates.to_series().diff().dropna() > pd.Timedelta(days=7)).sum())
        close = candles.groupby("_Date", sort=True)["Close"].last().dropna()
        close_moves = int((close.pct_change().abs() > 0.25).fillna(False).sum())
        first_date = unique_dates[0] if len(unique_dates) else pd.NaT
        last_date = unique_dates[-1] if len(unique_dates) else pd.NaT
        stale_days = int((today - last_date).days) if pd.notna(last_date) else None

        if candles.empty:
            status = "NO VALID DATE ROWS"
        elif invalid_ohlc:
            status = "CHECK — INVALID OHLC"
        elif duplicate_dates:
            status = "CHECK — DUPLICATE DATES"
        elif stale_days is None or stale_days > int(max_stale_days):
            status = "STALE — UPDATE REQUIRED"
        elif gaps:
            status = "REVIEW — LONG INTERNAL GAP"
        elif close_moves:
            status = "REVIEW — LARGE CLOSE MOVE"
        else:
            status = "OK — STRUCTURAL CHECKS PASSED"

        records.append({
            "Symbol": symbol,
            "Candles": int(len(unique_dates)),
            "First Date": first_date.date().isoformat() if pd.notna(first_date) else None,
            "Last Date": last_date.date().isoformat() if pd.notna(last_date) else None,
            "Days Since Update": stale_days,
            "Duplicate Dates": duplicate_dates,
            "Invalid OHLC Rows": invalid_ohlc,
            "Gaps >7d": gaps,
            "Close Moves >25%": close_moves,
            "Source Valid": True,
            "Health Status": status,
        })

    if not records:
        return pd.DataFrame(columns=[
            "Symbol", "Candles", "First Date", "Last Date", "Days Since Update",
            "Duplicate Dates", "Invalid OHLC Rows", "Gaps >7d", "Close Moves >25%",
            "Source Valid", "Health Status",
        ])
    return pd.DataFrame(records).sort_values(["Health Status", "Symbol"]).reset_index(drop=True)


def summarize_audit(frame: pd.DataFrame) -> dict:
    """Return compact counts for the app UI."""
    if frame is None or frame.empty:
        return {"symbols": 0, "healthy": 0, "issues": 0, "stale": 0, "invalid_ohlc": 0}
    status = frame["Health Status"].fillna("").astype(str)
    return {
        "symbols": int(len(frame)),
        "healthy": int(status.str.startswith("OK").sum()),
        "issues": int((~status.str.startswith("OK")).sum()),
        "stale": int(status.str.startswith("STALE").sum()),
        "invalid_ohlc": int(pd.to_numeric(frame["Invalid OHLC Rows"], errors="coerce").fillna(0).sum()),
    }
