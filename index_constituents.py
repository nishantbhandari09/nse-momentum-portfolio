"""Download official index constituent lists published by Nifty Indices / NSE."""

from io import StringIO

import pandas as pd
import requests
import streamlit as st


# Official downloadable constituent CSVs. Broad-market and sectoral groups are
# kept separate so the app's selector stays easy to navigate.
INDEX_CATEGORIES = {
    "Broad Market": {
        "Nifty 50": "ind_nifty50list.csv",
        "Nifty Next 50": "ind_niftynext50list.csv",
        "Nifty Next 100": "ind_niftynext100list.csv",
        "Nifty 100": "ind_nifty100list.csv",
        "Nifty 200": "ind_nifty200list.csv",
        "Nifty 500": "ind_nifty500list.csv",
        "Nifty Midcap 50": "ind_niftymidcap50list.csv",
        "Nifty Midcap 100": "ind_niftymidcap100list.csv",
        "Nifty Midcap 150": "ind_niftymidcap150list.csv",
        "Nifty Smallcap 50": "ind_niftysmallcap50list.csv",
        "Nifty Smallcap 100": "ind_niftysmallcap100list.csv",
        "Nifty Smallcap 250": "ind_niftysmallcap250list.csv",
        "Nifty Microcap 250": "ind_niftymicrocap250_list.csv",
        "Nifty MidSmallcap 400": "ind_niftymidsmallcap400list.csv",
    },
    "Sectoral": {
        "Nifty Auto": "ind_niftyautolist.csv",
        "Nifty Bank": "ind_niftybanklist.csv",
        "Nifty Financial Services": "ind_niftyfinancelist.csv",
        "Nifty FMCG": "ind_niftyfmcglist.csv",
        "Nifty IT": "ind_niftyitlist.csv",
        "Nifty Media": "ind_niftymedialist.csv",
        "Nifty Metal": "ind_niftymetallist.csv",
        "Nifty Pharma": "ind_niftypharmalist.csv",
        "Nifty PSU Bank": "ind_niftypsubanklist.csv",
        "Nifty Private Bank": "ind_nifty_privatebanklist.csv",
        "Nifty Healthcare": "ind_niftyhealthcarelist.csv",
        "Nifty Realty": "ind_niftyrealtylist.csv",
        "Nifty Energy": "ind_niftyenergylist.csv",
        "Nifty Infrastructure": "ind_niftyinfralist.csv",
        "Nifty Commodities": "ind_niftycommoditieslist.csv",
        "Nifty Consumption": "ind_niftyconsumptionlist.csv",
        "Nifty Oil and Gas": "ind_niftyoilgaslist.csv",
        "Nifty Consumer Durables": "ind_niftyconsumerdurableslist.csv",
        "Nifty PSE": "ind_niftypse_list.csv",
        "Nifty CPSE": "ind_niftycpse_list.csv",
        "Nifty MNC": "ind_niftymnc_list.csv",
        "Nifty Services Sector": "ind_niftyservsectorlist.csv",
    },
}

SOURCE_BASES = (
    "https://www.niftyindices.com/IndexConstituent/",
    "https://nsearchives.nseindia.com/content/indices/",
    "https://archives.nseindia.com/content/indices/",
    "https://www1.nseindia.com/content/indices/",
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,application/octet-stream,*/*",
    "Referer": "https://www.niftyindices.com/",
}


def _normalise_constituent_csv(csv_text: str) -> pd.DataFrame:
    """Read direct-header files and older NSE files that have a preamble."""
    expected = {
        "companyname": "Company Name",
        "industry": "Industry",
        "symbol": "Symbol",
        "series": "Series",
        "isincode": "ISIN Code",
    }

    parsed = None
    # Some archive files include a title and blank rows before their header.
    for skip_rows in range(0, 9):
        try:
            candidate = pd.read_csv(
                StringIO(csv_text),
                skiprows=skip_rows,
                dtype=str,
                on_bad_lines="skip",
            )
        except Exception:
            continue

        column_lookup = {
            "".join(ch.lower() for ch in str(col).strip().replace("\ufeff", "") if ch.isalnum()): col
            for col in candidate.columns
        }
        if "symbol" in column_lookup:
            parsed = (candidate, column_lookup)
            break

    if parsed is None:
        raise ValueError("The downloaded file did not contain a recognisable Symbol column.")

    candidate, column_lookup = parsed
    result = pd.DataFrame()
    for normalised, display_name in expected.items():
        source_col = column_lookup.get(normalised)
        result[display_name] = candidate[source_col].astype(str).str.strip() if source_col else ""

    result["Symbol"] = result["Symbol"].replace({"nan": "", "None": ""}).str.strip()
    result = result[result["Symbol"].ne("") & result["Symbol"].ne("-")]
    result = result.drop_duplicates(subset=["Symbol"]).reset_index(drop=True)

    if result.empty:
        raise ValueError("The downloaded file contained no constituent symbols.")

    result["Yahoo Ticker"] = result["Symbol"] + ".NS"
    return result[
        ["Company Name", "Industry", "Symbol", "Yahoo Ticker", "Series", "ISIN Code"]
    ]


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_index_constituents(category: str, index_name: str):
    """Return (constituents dataframe, successful official CSV URL)."""
    if category not in INDEX_CATEGORIES or index_name not in INDEX_CATEGORIES[category]:
        raise ValueError("Please select a supported index.")

    filename = INDEX_CATEGORIES[category][index_name]
    errors = []

    for base_url in SOURCE_BASES:
        url = base_url + filename
        try:
            response = requests.get(url, headers=REQUEST_HEADERS, timeout=20)
            response.raise_for_status()
            csv_text = response.content.decode("utf-8-sig", errors="replace")
            constituents = _normalise_constituent_csv(csv_text)
            return constituents, url
        except Exception as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            errors.append(f"{base_url} ({status or type(exc).__name__})")

    raise RuntimeError(
        f"Couldn't download the published constituent file for {index_name}. "
        "The source may be temporarily unavailable or the file name may have changed. "
        "Please try again later."
    )
