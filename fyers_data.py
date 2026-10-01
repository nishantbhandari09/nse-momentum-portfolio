from __future__ import annotations

import hashlib
import io
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlencode

import pandas as pd
import requests

BASE_URL = "https://api-t1.fyers.in/api/v3"
DATA_URL = "https://api-t1.fyers.in/data"
SYMBOL_MASTER_URLS = {
    "NSE_CM": "https://public.fyers.in/sym_details/NSE_CM.csv",
    "NSE_FO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "NSE_COM": "https://public.fyers.in/sym_details/NSE_COM.csv",
    "MCX_COM": "https://public.fyers.in/sym_details/MCX_COM.csv",
}


def _secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st
        value = st.secrets.get(name, default)
        return str(value) if value is not None else default
    except Exception:
        return os.getenv(name, default)



def normalize_history_dates(df: pd.DataFrame) -> pd.DataFrame:
    """Return a history frame with a guaranteed pandas datetime64-normalized date column."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    out = df.copy()
    if "date" not in out.columns:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    parsed = pd.to_datetime(out["date"], errors="coerce", utc=True)
    out["date"] = parsed.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None).dt.normalize()
    out = out.dropna(subset=["date"]).drop_duplicates("date").sort_values("date")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    return out


def get_app_id() -> str:
    return _secret("FYERS_APP_ID")


def get_secret_id() -> str:
    return _secret("FYERS_SECRET_ID")


def get_redirect_uri() -> str:
    return _secret("FYERS_REDIRECT_URI")


def get_default_access_token() -> str:
    return _secret("FYERS_ACCESS_TOKEN")


def build_login_url(state: str = "momentum") -> str:
    params = {
        "client_id": get_app_id(),
        "redirect_uri": get_redirect_uri(),
        "response_type": "code",
        "state": state,
    }
    return f"{BASE_URL}/generate-authcode?{urlencode(params)}"


def exchange_auth_code(auth_code: str) -> Dict[str, Any]:
    app_id = get_app_id()
    secret = get_secret_id()
    if not app_id or not secret:
        raise ValueError("FYERS_APP_ID and FYERS_SECRET_ID are required in Streamlit Secrets.")
    app_hash = hashlib.sha256(f"{app_id}:{secret}".encode("utf-8")).hexdigest()
    payload = {
        "grant_type": "authorization_code",
        "appIdHash": app_hash,
        "code": auth_code,
    }
    r = requests.post(f"{BASE_URL}/validate-authcode", json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("s") != "ok" or not data.get("access_token"):
        raise RuntimeError(f"FYERS token exchange failed: {data}")
    return data


def parse_fyers_candles(response: Dict[str, Any]) -> pd.DataFrame:
    rows = response.get("candles") or []
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["timestamp"], unit="s", errors="coerce", utc=True)
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df = normalize_history_dates(df)
    return df.dropna(subset=["close"])


class FyersClient:
    def __init__(self, access_token: str, app_id: Optional[str] = None, timeout: int = 30):
        self.app_id = app_id or get_app_id()
        self.access_token = access_token
        self.timeout = timeout
        if not self.app_id or not self.access_token:
            raise ValueError("FYERS app ID/access token is missing.")

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"{self.app_id}:{self.access_token}",
            "Content-Type": "application/json",
            "version": "3",
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> Dict[str, Any]:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("headers", self.headers)
        r = requests.request(method, url, **kwargs)
        try:
            data = r.json()
        except Exception:
            data = {"s": "error", "code": r.status_code, "message": r.text[:500]}
        if r.status_code >= 400:
            raise RuntimeError(f"FYERS HTTP {r.status_code}: {data}")
        if isinstance(data, dict) and data.get("s") == "error":
            raise RuntimeError(f"FYERS API error: {data}")
        return data

    def profile(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/profile")

    def funds(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/funds")

    def holdings(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/holdings")

    def positions(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/positions")

    def orders(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/orders")

    def tradebook(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/tradebook")

    def market_status(self) -> Dict[str, Any]:
        return self._request("GET", f"{BASE_URL}/marketStatus")

    def quotes(self, symbols: Iterable[str]) -> Dict[str, Any]:
        syms = [s for s in symbols if s]
        out: Dict[str, Any] = {}
        for i in range(0, len(syms), 50):
            chunk = syms[i:i + 50]
            resp = self._request("GET", f"{DATA_URL}/quotes", params={"symbols": ",".join(chunk)})
            for item in resp.get("d", []):
                out[item.get("n") or item.get("symbol") or ""] = item
        return out

    def history(self, symbol: str, start: date, end: date, resolution: str = "1D") -> pd.DataFrame:
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": 1,
            "range_from": start.isoformat(),
            "range_to": end.isoformat(),
            "cont_flag": "1",
        }
        resp = self._request("GET", f"{DATA_URL}/history", params=payload)
        return parse_fyers_candles(resp)

    def history_chunked(self, symbol: str, start: date, end: date, resolution: str = "1D", pause: float = 0.10) -> pd.DataFrame:
        # Daily history is requested in conservative sub-ranges to stay below documented range limits.
        parts: List[pd.DataFrame] = []
        cur = start
        while cur <= end:
            chunk_end = min(cur + timedelta(days=359), end)
            try:
                part = self.history(symbol, cur, chunk_end, resolution=resolution)
                if not part.empty:
                    parts.append(part)
            finally:
                time.sleep(pause)
            cur = chunk_end + timedelta(days=1)
        if not parts:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        return pd.concat(parts, ignore_index=True).drop_duplicates("date").sort_values("date")

    def place_order(self, symbol: str, side: int, qty: int, product_type: str = "CNC", order_type: int = 2,
                    limit_price: float = 0, stop_price: float = 0, offline_order: bool = False,
                    order_tag: str = "MOMENTUM") -> Dict[str, Any]:
        payload = {
            "symbol": symbol,
            "qty": int(qty),
            "type": int(order_type),
            "side": int(side),
            "productType": product_type,
            "limitPrice": float(limit_price),
            "stopPrice": float(stop_price),
            "disclosedQty": 0,
            "validity": "DAY",
            "offlineOrder": bool(offline_order),
            "orderTag": order_tag[:20],
        }
        return self._request("POST", f"{BASE_URL}/orders/sync", json=payload)


def download_symbol_master(exchange_file: str = "NSE_CM", destination: str = "data/reference/fyers_symbol_master.csv") -> Path:
    url = SYMBOL_MASTER_URLS[exchange_file]
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return path


def _yahoo_history(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Fallback only when FYERS cannot return history for a symbol."""
    try:
        import yfinance as yf
        ticker = symbol.replace("NSE:", "").replace("-EQ", "") + ".NS"
        raw = yf.download(
            ticker,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.reset_index()
        rename = {"Date":"date","Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"}
        raw = raw.rename(columns=rename)
        needed = ["date","open","high","low","close","volume"]
        if not set(needed).issubset(raw.columns):
            return pd.DataFrame(columns=needed)
        raw = normalize_history_dates(raw[needed])
        return raw.dropna(subset=["close"])
    except Exception:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])


def get_history_cached(client: FyersClient, symbol: str, start: date, end: date, cache_dir: str,
                       force: bool = False) -> pd.DataFrame:
    stem = f"{symbol.replace(':', '_').replace('/', '_').replace('?', '_')}"
    path = Path(cache_dir) / f"{stem}.parquet"
    csv_path = Path(cache_dir) / f"{stem}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)

    def read_cache() -> pd.DataFrame:
        try:
            if path.exists():
                return normalize_history_dates(pd.read_parquet(path))
            if csv_path.exists():
                return normalize_history_dates(pd.read_csv(csv_path))
        except Exception:
            return pd.DataFrame()
        return pd.DataFrame()

    def write_cache(frame: pd.DataFrame) -> None:
        try:
            frame.to_parquet(path, index=False)
        except Exception:
            frame.to_csv(csv_path, index=False)

    cached = read_cache() if not force else pd.DataFrame()
    if not cached.empty:
        cached = normalize_history_dates(cached)
        if cached["date"].min().date() <= start and cached["date"].max().date() >= end:
            return cached[(cached["date"].dt.date >= start) & (cached["date"].dt.date <= end)].copy()
        missing_parts: List[pd.DataFrame] = [cached]

        if start < cached["date"].min().date():
            missing_start = start
            missing_end = cached["date"].min().date() - timedelta(days=1)
            try:
                missing_parts.append(client.history_chunked(symbol, missing_start, missing_end))
            except Exception:
                if not symbol.upper().endswith("-INDEX"):
                    missing_parts.append(_yahoo_history(symbol, missing_start, missing_end))

        if end > cached["date"].max().date():
            missing_start = cached["date"].max().date() + timedelta(days=1)
            missing_end = end
            try:
                missing_parts.append(client.history_chunked(symbol, missing_start, missing_end))
            except Exception:
                if not symbol.upper().endswith("-INDEX"):
                    missing_parts.append(_yahoo_history(symbol, missing_start, missing_end))

        merged = pd.concat(missing_parts, ignore_index=True).drop_duplicates("date").sort_values("date")
        if len(merged) > len(cached):
            write_cache(merged)
        return merged[(merged["date"].dt.date >= start) & (merged["date"].dt.date <= end)].copy()

    try:
        df = client.history_chunked(symbol, start, end)
    except Exception:
        df = pd.DataFrame()

    # FYERS remains primary. Yahoo is used only when FYERS returns no usable history.
    if df.empty:
        df = _yahoo_history(symbol, start, end)

    if not cached.empty and not df.empty:
        df = pd.concat([cached, df], ignore_index=True).drop_duplicates("date").sort_values("date")
    elif not cached.empty:
        df = cached

    if not df.empty:
        write_cache(df)
    return df[(df["date"].dt.date >= start) & (df["date"].dt.date <= end)].copy()
