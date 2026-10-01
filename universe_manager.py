from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List
import io

import pandas as pd
import requests

DATA_DIR = Path("data/reference")
MASTER_PATH = DATA_DIR / "fyers_symbol_master.csv"
CATALOG_PATH = DATA_DIR / "universe_catalog.csv"
MANUAL_GROUPS = Path("universe_groups.csv")

INTERNATIONAL_TERMS = re.compile(
    r"nasdaq|s\s*&\s*p|sp\s*500|dow\s*jones|nyse|hang\s*seng|china|japan|korea|taiwan|europe|germany|france|uk|u\.s\.?|usa|us\s|global|world|emerging|foreign|international|msci|ftse|nikkei|hangseng|russell|motilal.*nasdaq",
    re.I,
)
DEFENSIVE_TERMS = re.compile(r"gold|g-sec|gsec|government|liquid|overnight|money market|treasury", re.I)
ETF_TERMS = re.compile(r"etf|exchange traded", re.I)
INDEX_TERMS = re.compile(r"-INDEX\b|INDEX$|INDEX", re.I)


def _read_fyers_master(path: Path = MASTER_PATH) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="ignore")
    sample = "\n".join(text.splitlines()[:3])
    sep = "|" if sample.count("|") > sample.count(",") else ","
    try:
        df = pd.read_csv(path, sep=sep, low_memory=False)
    except Exception:
        df = pd.read_csv(io.StringIO(text), sep=sep, header=None, low_memory=False)  # type: ignore[name-defined]
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _pick_col(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return None


def build_catalog(master: pd.DataFrame) -> pd.DataFrame:
    if master.empty:
        return pd.DataFrame(columns=["symbol", "description", "asset_type", "groups", "tradable"])
    sym_col = _pick_col(master, ["symbol", "Symbol", "fySymbol", "fyers_symbol"])
    desc_col = _pick_col(master, ["description", "Description", "name", "display_name"])
    inst_col = _pick_col(master, ["exchange_instType", "instrument_type", "instType", "instrumentType"])
    if sym_col is None:
        # Some symbol masters are headerless. The usual FYERS schema places symbol in column 9-ish;
        # expose a best-effort first string column rather than failing the entire app.
        text_cols = [c for c in master.columns if master[c].dtype == "object"]
        sym_col = text_cols[0] if text_cols else master.columns[0]
    df = pd.DataFrame()
    df["symbol"] = master[sym_col].astype(str).str.strip()
    df["description"] = master[desc_col].astype(str).str.strip() if desc_col else ""
    df["instrument_type"] = master[inst_col].astype(str) if inst_col else ""
    df = df[df["symbol"].str.startswith("NSE:", na=False)].drop_duplicates("symbol")
    combined = (df["symbol"] + " " + df["description"]).fillna("")
    is_index = combined.str.contains(INDEX_TERMS, regex=True, na=False) | df["symbol"].str.endswith("-INDEX", na=False)
    is_etf = combined.str.contains(ETF_TERMS, regex=True, na=False)
    # Many FYERS masters don't say ETF explicitly in the symbol. Common ETF prefixes/names are caught as a fallback.
    is_etf = is_etf | df["symbol"].str.contains(r"ETF|BEES|GOLDBEES|LIQUIDBEES|JUNIORBEES|MID150BEES|SILVERBEES", case=False, regex=True)
    df["asset_type"] = "OTHER"
    df.loc[is_index, "asset_type"] = "INDEX"
    df.loc[is_etf & ~is_index, "asset_type"] = "ETF"
    intl = combined.str.contains(INTERNATIONAL_TERMS, regex=True, na=False)
    defensive = combined.str.contains(DEFENSIVE_TERMS, regex=True, na=False)
    groups: List[str] = []
    for typ, inter, defens in zip(df["asset_type"], intl, defensive):
        g = ["ALL"]
        if typ == "ETF":
            g.append("ALL_ETF")
            g.append("INTERNATIONAL_ETF" if inter else "DOMESTIC_ETF")
            if defens:
                g.append("DEFENSIVE")
        if typ == "INDEX":
            g.append("ALL_INDEX")
        groups.append("|".join(g))
    df["groups"] = groups
    df["tradable"] = df["asset_type"].isin(["ETF", "OTHER"])
    return df[["symbol", "description", "instrument_type", "asset_type", "groups", "tradable"]].sort_values("symbol")


def merge_manual_groups(catalog: pd.DataFrame, manual_path: Path = MANUAL_GROUPS) -> pd.DataFrame:
    if not manual_path.exists():
        return catalog
    try:
        manual = pd.read_csv(manual_path, comment="#")
    except Exception:
        return catalog
    if "symbol" not in manual.columns or "group" not in manual.columns:
        return catalog
    manual = manual.copy()
    manual["symbol"] = manual["symbol"].astype(str).str.strip()
    manual["group"] = manual["group"].astype(str).str.strip().str.upper()
    mapping = manual.groupby("symbol")["group"].apply(list).to_dict()
    extra = catalog["symbol"].map(lambda s: ",".join(mapping.get(s, [])))
    catalog = catalog.copy()
    catalog["groups"] = [
        "|".join(dict.fromkeys([p for p in str(a).split("|") if p] + ([b] if b else [])))
        for a, b in zip(catalog["groups"], extra.fillna(""))
    ]
    return catalog


def refresh_catalog() -> pd.DataFrame:
    master = _read_fyers_master()
    catalog = build_catalog(master)
    catalog = merge_manual_groups(catalog)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(CATALOG_PATH, index=False)
    return catalog


def load_catalog() -> pd.DataFrame:
    if CATALOG_PATH.exists():
        return pd.read_csv(CATALOG_PATH)
    return pd.DataFrame(columns=["symbol", "description", "instrument_type", "asset_type", "groups", "tradable"])


def available_groups(catalog: pd.DataFrame | None = None) -> List[str]:
    catalog = catalog if catalog is not None else load_catalog()
    groups = {"ALL", "ALL_ETF", "DOMESTIC_ETF", "INTERNATIONAL_ETF", "DEFENSIVE", "ALL_INDEX"}
    if not catalog.empty:
        for raw in catalog["groups"].fillna(""):
            groups.update([x for x in str(raw).split("|") if x])
    # Common indices are exposed even if symbol master hasn't been synced yet.
    groups.update({"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500", "BANKNIFTY"})
    return sorted(groups)


def _has_nifty_constituents(catalog: pd.DataFrame, group: str) -> bool:
    if catalog.empty or "groups" not in catalog.columns:
        return False
    mask = catalog["groups"].fillna("").astype(str).str.contains(
        rf"(?:^|\|){re.escape(group)}(?:\||$)", regex=True, na=False
    )
    # The index itself is not a constituent. Require at least a small set of EQ rows.
    return int((mask & catalog["symbol"].astype(str).str.endswith("-EQ")).sum()) >= 5


def ensure_nifty_constituents(group: str, catalog: pd.DataFrame | None = None) -> pd.DataFrame:
    group = str(group).upper()
    catalog = catalog if catalog is not None else load_catalog()
    if group in {"NIFTY50", "NIFTY100", "NIFTY200", "NIFTY500"} and not _has_nifty_constituents(catalog, group):
        refreshed = sync_nifty_constituents(catalog)
        if not refreshed.empty:
            catalog = refreshed
    return catalog


def symbols_for_group(group: str, catalog: pd.DataFrame | None = None) -> pd.DataFrame:
    group = group.upper()
    catalog = ensure_nifty_constituents(group, catalog)
    if catalog.empty:
        return catalog
    if group == "ALL":
        return catalog.copy()
    out = catalog[catalog["groups"].fillna("").str.contains(rf"(?:^|\|){re.escape(group)}(?:\||$)", regex=True, na=False)].copy()
    common_indices = {
        "NIFTY50": "NSE:NIFTY50-INDEX",
        "NIFTY100": "NSE:NIFTY100-INDEX",
        "NIFTY200": "NSE:NIFTY200-INDEX",
        "NIFTY500": "NSE:NIFTY500-INDEX",
        "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    }
    if group in common_indices and not (out["symbol"] == common_indices[group]).any():
        extra = pd.DataFrame([{
            "symbol": common_indices[group], "description": group, "instrument_type": "INDEX",
            "asset_type": "INDEX", "groups": f"ALL|ALL_INDEX|{group}", "tradable": False,
        }])
        out = pd.concat([out, extra], ignore_index=True)
    return out.drop_duplicates("symbol")


NIFTY_CONSTITUENT_URLS = {
    "NIFTY50": "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "NIFTY100": "https://niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "NIFTY200": "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "NIFTY500": "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}


def sync_nifty_constituents(catalog: pd.DataFrame | None = None) -> pd.DataFrame:
    catalog = catalog if catalog is not None else load_catalog()
    parts = [catalog] if not catalog.empty else []
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/csv,application/csv,text/plain,*/*",
        "Referer": "https://www.niftyindices.com/",
    }
    for group, url in NIFTY_CONSTITUENT_URLS.items():
        try:
            r = requests.get(url, headers=headers, timeout=30)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            cols = {str(c).strip().lower(): c for c in df.columns}
            sym_col = cols.get("symbol") or cols.get("ticker") or cols.get("tradingsymbol")
            if not sym_col:
                continue
            rows = []
            for raw in df[sym_col].dropna().astype(str):
                base = raw.strip().upper().replace("NSE:", "").replace(".NS", "")
                if not base:
                    continue
                rows.append({
                    "symbol": f"NSE:{base}-EQ",
                    "description": f"{base} | {group}",
                    "instrument_type": "EQ",
                    "asset_type": "OTHER",
                    "groups": f"ALL|{group}",
                    "tradable": True,
                })
            if rows:
                parts.append(pd.DataFrame(rows))
        except Exception:
            continue
    if not parts:
        return catalog
    out = pd.concat(parts, ignore_index=True).drop_duplicates(["symbol", "groups"], keep="last")
    # Merge multiple group memberships for the same symbol.
    grouped_rows=[]
    for sym, g in out.groupby("symbol", sort=True):
        first=g.iloc[0].to_dict()
        group_parts=[]
        for val in g["groups"].astype(str):
            group_parts.extend([x for x in val.split("|") if x])
        first["groups"]="|".join(dict.fromkeys(group_parts))
        grouped_rows.append(first)
    final=pd.DataFrame(grouped_rows)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    final.to_csv(CATALOG_PATH,index=False)
    return final
