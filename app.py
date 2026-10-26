import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import json
import base64
from nacl.public import PublicKey, SealedBox
from io import StringIO
from datetime import datetime, timedelta

from fyers_auth import (
    get_login_url,
    exchange_code_for_tokens,
    get_authenticated_fyers,
    create_signed_state,
    validate_signed_state,
    FyersLoginError,
)
import momentum_data
import index_history_data
import fyers_history_data
import fyers_chart_data
import nse_history_data
import nse_archive_data
import data_health
import trading_costs
import eod_reporting
import tradingview_charts as tv_charts
from etf_index_universe import fetch_etf_and_index_universe
from index_constituents import (
    INDEX_CATEGORIES,
    ALL_ETF_CATEGORY,
    fetch_index_constituents,
    fetch_all_etfs_and_bees,
)
from etf_group_universe import (
    ETF_GROUP_NAMES,
    ETF_GROUP_SYMBOLS,
    ETF_GROUP_ALIASES,
    get_etf_group_symbols,
)

# ==========================================
# PAGE CONFIGURATION & STYLING
# ==========================================
st.set_page_config(
    page_title="Happy Trade | Momentum Investing",
    page_icon="📈",
    layout="wide",
)

st.markdown("""
    <style>
    :root {
        --ht-green: #00a85a;
        --ht-green-bright: #20d878;
        --ht-green-deep: #08763f;
        --ht-black: #050806;
        --ht-panel: #0b120e;
        --ht-panel-2: #101b14;
        --ht-border: #1c3c2a;
        --ht-text: #eff8f1;
        --ht-muted: #a0b2a6;
    }
    html, body, [data-testid="stAppViewContainer"], .stApp {
        background: var(--ht-black) !important;
        color: var(--ht-text) !important;
    }
    [data-testid="stHeader"] {
        background: rgba(5, 8, 6, 0.94) !important;
    }
    [data-testid="stMain"] .block-container {
        padding-top: 1.15rem;
        padding-bottom: 2.5rem;
        max-width: 1680px;
    }
    h1, h2, h3, h4, h5, h6, p, label, [data-testid="stMarkdownContainer"] {
        color: var(--ht-text);
    }
    h1, h2, h3 {
        letter-spacing: -0.025em;
    }
    [data-testid="stCaptionContainer"], .stCaption, small {
        color: var(--ht-muted) !important;
    }
    [data-testid="stMetric"] {
        background: linear-gradient(145deg, #101b14, #0a100c);
        border: 1px solid var(--ht-border);
        padding: 17px 18px;
        border-radius: 15px;
        box-shadow: 0 5px 18px rgba(0, 0, 0, 0.18);
    }
    [data-testid="stMetricLabel"] {
        color: #a8bdb0 !important;
        font-size: 0.88rem !important;
    }
    [data-testid="stMetricValue"] {
        color: #f1fff5 !important;
        font-weight: 700;
    }
    [data-testid="stMetricDelta"] {
        font-weight: 600;
    }
    .stButton > button, .stDownloadButton > button {
        border-radius: 10px;
        min-height: 2.55rem;
        font-weight: 650;
        border: 1px solid #28553a;
        transition: border-color 120ms ease, background 120ms ease, transform 120ms ease;
    }
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #07934d, #08763f);
        color: #ffffff;
        border: 1px solid #21c773;
        box-shadow: 0 3px 12px rgba(0, 168, 90, 0.16);
    }
    .stButton > button[kind="secondary"], .stDownloadButton > button {
        background: #101812;
        color: #eaf6ed;
    }
    .stButton > button:hover, .stDownloadButton > button:hover {
        border-color: var(--ht-green-bright);
        color: #ffffff;
        transform: translateY(-1px);
    }
    [data-baseweb="input"] > div, [data-baseweb="select"] > div,
    [data-testid="stDateInput"] input, [data-testid="stNumberInput"] input,
    [data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {
        background: #0b120e !important;
        color: #eff8f1 !important;
        border-color: #2a4735 !important;
        border-radius: 9px !important;
    }
    [data-baseweb="popover"], [data-baseweb="menu"], [role="listbox"] {
        background: #101812 !important;
    }
    [data-baseweb="tab-list"] {
        gap: 8px;
        border-bottom-color: #203b2b;
    }
    [data-baseweb="tab"] {
        color: #a8bdb0 !important;
        border-radius: 9px 9px 0 0;
        padding: 10px 15px;
    }
    [aria-selected="true"][data-baseweb="tab"] {
        color: #52e995 !important;
        border-bottom-color: var(--ht-green-bright) !important;
    }
    [data-testid="stDataFrame"], [data-testid="stTable"] {
        border: 1px solid #1c3c2a;
        border-radius: 12px;
        overflow: hidden;
    }
    [data-testid="stExpander"] {
        background: #0b120e;
        border: 1px solid #203b2b;
        border-radius: 12px;
    }
    [data-testid="stAlert"] {
        border-radius: 10px;
        border-width: 1px;
    }
    hr {
        border-color: #1c3325 !important;
    }
    .status-active {
        color: #5cf2a0;
        font-weight: 700;
        background-color: #10291b;
        border: 1px solid #1c6840;
        padding: 3px 9px;
        border-radius: 6px;
    }
    .status-paused {
        color: #ffb9a8;
        font-weight: 700;
        background-color: #2a1713;
        border: 1px solid #6b3023;
        padding: 3px 9px;
        border-radius: 6px;
    }
    .ht-brandbar {
        display: flex;
        align-items: center;
        gap: 18px;
        padding: 8px 0 17px 0;
        margin-bottom: 8px;
        border-bottom: 1px solid #1b3927;
    }
    .ht-brand-mark {
        width: 82px;
        height: 82px;
        flex: 0 0 82px;
        overflow: hidden;
        border-radius: 14px;
        border: 1px solid #1b5d38;
        box-shadow: 0 5px 18px rgba(0, 168, 90, 0.12);
    }
    .ht-brand-mark img {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
    }
    .ht-brand-title {
        color: #f2fff5;
        font-size: clamp(1.6rem, 2.4vw, 2.35rem);
        line-height: 1.08;
        letter-spacing: -0.04em;
        font-weight: 800;
    }
    .ht-brand-title span {
        color: #20d878;
    }
    .ht-brand-subtitle {
        color: #a0b2a6;
        font-size: 0.94rem;
        margin-top: 6px;
    }
    .ht-brand-eyebrow {
        color: #45dc89;
        font-size: 0.72rem;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        font-weight: 750;
        margin-bottom: 6px;
    }
    @media (max-width: 760px) {
        [data-testid="stMain"] .block-container { padding: 0.8rem 0.8rem 2rem; }
        .ht-brandbar { gap: 12px; padding-bottom: 12px; }
        .ht-brand-mark { width: 64px; height: 64px; flex-basis: 64px; }
        .ht-brand-subtitle { font-size: 0.8rem; }
    }
    </style>
""", unsafe_allow_html=True)

ASSET_GROUPS_MAPPING = {
    "Gold ETF": ["GOLDBEES.NS"],
    "LiquidBEES": ["LIQUIDBEES.NS"],
    "Gov Bond": ["SETFGSEC.NS"],
}

# Kept as a small overlay for portfolio metrics and regime checks. This is
# deliberately separate from the full user-selected ALL ETF universe.
ALWAYS_FETCH_OVERLAY_TICKERS = [
    "GOLDBEES.NS", "LIQUIDBEES.NS", "SETFGSEC.NS",
    "NIFTYBEES.NS", "JUNIORBEES.NS", "BANKBEES.NS",
]

NIFTY_REGIME_TICKER = "^CRSLDX"
GSEC_REGIME_TICKER = "SETFGSEC.NS"

# ==========================================
# DATA FETCHING ENGINE
# ==========================================
@st.cache_data(ttl=6 * 60 * 60, show_spinner=False)
def get_all_nse_stock_tickers():
    """Load the canonical all-NSE stock universe from the permanent archive."""
    return nse_archive_data.list_all_symbols()


@st.cache_data(ttl=86400)
def get_trusted_tickers_by_group(groups):
    tickers = []

    # Fetch each selected index's own official constituent list. Do not
    # approximate Nifty 200 by taking the first 200 rows of an alphabetically
    # sorted Nifty 500 file.
    equity_indices = [
        name for name in ("Nifty 500", "Nifty 200") if name in groups
    ]
    if "All NSE Stocks" in groups:
        try:
            tickers.extend(get_all_nse_stock_tickers())
        except Exception as exc:
            st.warning(
                "Could not load the full NSE stock universe from the permanent archive. "
                f"Check the NSE data archive workflow and retry. Detail: {exc}"
            )

    for index_name in equity_indices:
        try:
            constituents, _source_url = fetch_index_constituents("Broad Market", index_name)
            if "Yahoo Ticker" in constituents.columns:
                tickers.extend(
                    constituents["Yahoo Ticker"].dropna().astype(str).str.strip().tolist()
                )
            elif "Symbol" in constituents.columns:
                tickers.extend(
                    f"{str(symbol).strip()}.NS"
                    for symbol in constituents["Symbol"].dropna().unique()
                )
        except Exception:
            if index_name == "Nifty 500":
                # Retain the pre-existing mirror fallback for Nifty 500 if
                # official endpoints are temporarily unavailable.
                try:
                    mirror_url = "https://raw.githubusercontent.com/indian-stock-market/nifty-500-constituents/main/nifty500.csv"
                    mirror = pd.read_csv(mirror_url)
                    if "Symbol" in mirror.columns:
                        tickers.extend(
                            f"{str(symbol).strip()}.NS"
                            for symbol in mirror["Symbol"].dropna().unique()
                        )
                except Exception:
                    tickers.extend([
                        "RELIANCE.NS", "TCS.NS", "INFY.NS",
                        "HDFCBANK.NS", "ICICIBANK.NS",
                    ])
            # If Nifty 200's list is unavailable, fail closed for that group
            # instead of silently using an unrelated alphabetical subset.

    for grp in groups:
        if grp in ETF_GROUP_SYMBOLS or grp in ETF_GROUP_ALIASES:
            tickers.extend(get_etf_group_symbols(grp))
        elif grp in ASSET_GROUPS_MAPPING:
            tickers.extend(ASSET_GROUPS_MAPPING[grp])

    known_group_names = (
        set(ASSET_GROUPS_MAPPING)
        | set(ETF_GROUP_SYMBOLS)
        | set(ETF_GROUP_ALIASES)
        | {"Nifty 500", "Nifty 200", "All NSE Stocks"}
    )
    remaining_groups = [g for g in groups if g not in known_group_names]
    if remaining_groups:
        try:
            etf_universe = fetch_etf_and_index_universe()
            for grp in remaining_groups:
                tickers.extend(etf["symbol"] for etf in etf_universe["etfs"].get(grp, []))
        except Exception:
            pass

    if not tickers and "All NSE Stocks" not in groups:
        tickers = ["RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS"]

    return list(set(tickers))


def get_selected_etf_tickers(groups):
    """Resolve selected ETF groups to symbols, including older broker categories."""
    tickers = set()
    for group in groups:
        if group in ETF_GROUP_SYMBOLS or group in ETF_GROUP_ALIASES:
            tickers.update(get_etf_group_symbols(group))
        elif group in ASSET_GROUPS_MAPPING:
            tickers.update(ASSET_GROUPS_MAPPING[group])

    # Keep previously available broker-derived ETF categories working too.
    try:
        etf_universe = fetch_etf_and_index_universe()
        for group in groups:
            for item in etf_universe["etfs"].get(group, []):
                symbol = str(item.get("symbol", "")).strip()
                if symbol:
                    tickers.add(symbol if symbol.endswith(".NS") else f"{symbol}.NS")
    except Exception:
        pass
    return tickers


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_market_data(tickers, period="5y", full_history=False, start_date=None, end_date=None):
    """Load saved FYERS prices first and use the permanent NSE archive to fill gaps.

    The NSE archive is downloaded lazily by requested calendar year. In overlapping
    dates, FYERS values remain primary; split/bonus-adjusted NSE values fill older
    or missing rows. No future-price backfill is used.
    """
    ticker_values = list(tickers) if tickers is not None else []
    requested = list(dict.fromkeys(
        [str(t).strip() for t in ticker_values if str(t).strip()]
        + ALWAYS_FETCH_OVERLAY_TICKERS
        + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]
    ))
    broad_nse_universe = len(ticker_values) > 1000
    if broad_nse_universe:
        # For an all-NSE backtest, the compact Release archive is the efficient
        # stock-price source. Load only broker-only benchmarks/overlays from
        # individual FYERS CSV files; otherwise thousands of per-symbol reads
        # add latency without improving this archive-backed universe.
        broker_only_symbols = list(dict.fromkeys(
            ALWAYS_FETCH_OVERLAY_TICKERS + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER]
        ))
        prices = fyers_history_data.load_close_prices(broker_only_symbols)
    else:
        prices = fyers_history_data.load_close_prices(requested)

    # Always use the permanent NSE archive as a secondary source, not only for
    # explicit backtests. This lets scans, regime filters and portfolio valuation
    # recover older/missing equity and ETF candles too. Index tickers (^) are
    # intentionally excluded by nse_archive_data because they need their own source.
    archive_end = pd.Timestamp(end_date).normalize() if end_date is not None else pd.Timestamp.today().normalize()
    if start_date is not None:
        archive_start = pd.Timestamp(start_date).normalize()
    elif full_history:
        archive_start = (
            pd.Timestamp(prices.index.min()).normalize()
            if prices is not None and not prices.empty
            else pd.Timestamp("1994-11-03")
        )
    else:
        # Match the requested period for normal scans/portfolio views so we do
        # not download decades of stock history for a one-month holdings screen.
        requested_period = str(period or "5y").strip().lower()
        try:
            if requested_period == "max":
                archive_start = pd.Timestamp("1994-11-03")
            elif requested_period.endswith("mo"):
                archive_start = archive_end - pd.DateOffset(months=max(1, int(requested_period[:-2])))
            elif requested_period.endswith("y"):
                archive_start = archive_end - pd.DateOffset(years=max(1, int(requested_period[:-1])))
            elif requested_period.endswith("d"):
                archive_start = archive_end - pd.Timedelta(days=max(1, int(requested_period[:-1])))
            elif requested_period.endswith("m"):
                archive_start = archive_end - pd.DateOffset(months=max(1, int(requested_period[:-1])))
            else:
                archive_start = archive_end - pd.DateOffset(years=5)
        except (TypeError, ValueError):
            archive_start = archive_end - pd.DateOffset(years=5)
    try:
        archive_prices = nse_archive_data.load_close_prices(requested, archive_start, archive_end)
        if archive_prices is not None and not archive_prices.empty:
            # FYERS candles win on matching dates; NSE-adjusted prices fill dates
            # or whole symbols that FYERS did not return. The merged archive files
            # are cached on disk under nse_data/derived for subsequent app runs.
            prices = nse_archive_data.merge_price_panels(prices, archive_prices)
    except Exception as exc:
        # Keep the available source usable, but expose archive failures instead of
        # implying that all historical gaps have been repaired.
        if prices is None or prices.empty or full_history or start_date is not None:
            st.warning(f"NSE historical archive could not be loaded; some price gaps may remain: {exc}")

    if prices is None or prices.empty:
        return pd.DataFrame()
    prices = prices.loc[:, ~prices.isna().all(axis=0)].sort_index()
    # Carry a prior close for at most five union trading rows. Unlimited
    # forward-fill makes delisted/suspended stocks look flat forever and can
    # materially understate volatility and max drawdown.
    return prices.ffill(limit=5)

# ==========================================
# FYERS - LIVE PRICE OVERLAY
# ==========================================
def get_fyers_client():
    if "fyers_access_token" not in st.session_state:
        raise RuntimeError("not connected yet this session — use the Fyers Connection panel above")
    return get_authenticated_fyers(st.session_state["fyers_client_id"], st.session_state["fyers_access_token"])


def trigger_fyers_history_update_on_login(access_token: str, app_id: str):
    """Securely hand the fresh login token to GitHub Actions and start a history refresh.

    The token is encrypted with GitHub's repository public key before it is
    uploaded as an Actions secret. It is never placed in a workflow input,
    repository file, log message or client payload.
    """
    # This Streamlit app is publicly reachable, so only the intended FYERS
    # account may update the repository's credentials and historical database.
    allowed_user_id = str(st.secrets.get("FYERS_SYNC_ALLOWED_USER_ID") or "").strip().upper()
    try:
        profile_response = get_authenticated_fyers(app_id, access_token).get_profile()
        if isinstance(profile_response, dict):
            profile_data = profile_response.get("data") or profile_response
        else:
            profile_data = {}
        logged_in_user_id = str(
            profile_data.get("fy_id")
            or profile_data.get("fyId")
            or profile_data.get("user_id")
            or ""
        ).strip().upper()
    except Exception:
        logged_in_user_id = ""

    if not logged_in_user_id:
        return (
            False,
            "FYERS login succeeded, but the app could not verify the logged-in FYERS account ID. "
            "No historical update was started.",
        )
    if not allowed_user_id:
        return (
            False,
            f"Your FYERS account ID is {logged_in_user_id}. Add it as FYERS_SYNC_ALLOWED_USER_ID "
            "in Streamlit Secrets to authorize this account for automatic history updates, then log in again.",
        )
    if logged_in_user_id != allowed_user_id:
        return (
            False,
            "FYERS is connected, but this account is not the one authorized to update the saved history. "
            "No GitHub credentials were changed.",
        )

    github_token = str(
        st.secrets.get("GITHUB_ACTIONS_TOKEN")
        or st.secrets.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if not github_token:
        return (
            False,
            "Your FYERS account is verified, but automatic historical-data saving is not enabled yet. "
            "Add GITHUB_ACTIONS_TOKEN to Streamlit Secrets once; this setup does not require your FYERS PIN."
        )

    owner, repo = "nishantbhandari09", "nse-momentum-portfolio"
    api_root = f"https://api.github.com/repos/{owner}/{repo}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {github_token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        key_response = requests.get(
            f"{api_root}/actions/secrets/public-key",
            headers=headers,
            timeout=20,
        )
        key_response.raise_for_status()
        key_data = key_response.json()
        public_key = PublicKey(base64.b64decode(key_data["key"]))
        sealed_box = SealedBox(public_key)
        # Store the same App ID used by this completed login and its fresh
        # session token. GitHub receives encrypted values only.
        for secret_name, secret_value in (
            ("FYERS_APP_ID", app_id),
            ("FYERS_ACCESS_TOKEN", access_token),
        ):
            encrypted_value = base64.b64encode(
                sealed_box.encrypt(str(secret_value).encode("utf-8"))
            ).decode("ascii")
            secret_response = requests.put(
                f"{api_root}/actions/secrets/{secret_name}",
                headers=headers,
                json={
                    "encrypted_value": encrypted_value,
                    "key_id": key_data["key_id"],
                },
                timeout=20,
            )
            secret_response.raise_for_status()

        dispatch_response = requests.post(
            f"{api_root}/dispatches",
            headers=headers,
            json={
                "event_type": "fyers-login",
                "client_payload": {"source": "streamlit-login"},
            },
            timeout=20,
        )
        dispatch_response.raise_for_status()
        return (
            True,
            "Historical-data refresh has been triggered in GitHub Actions using this login. "
            "The workflow will save its progress back to the repository when finished.",
        )
    except Exception as exc:
        # Do not surface request bodies or any credential values in the app.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"GitHub returned HTTP {status}." if status else "The GitHub request could not be completed."
        return (
            False,
            "FYERS login succeeded, but automatic historical-data refresh could not be started. "
            f"{detail} Check that GITHUB_ACTIONS_TOKEN has repository Secrets read/write and Contents write permission.",
        )


def render_fyers_connection_panel():
    # The OAuth callback returns to this same Streamlit URL. Capture the code
    # directly from query parameters and exchange it automatically. The user
    # never needs to copy/paste an auth_code.
    app_id = str(st.secrets.get("FYERS_APP_ID") or "").strip()
    # Accept either common secret name to avoid breaking existing Streamlit setups.
    secret_key = str(st.secrets.get("FYERS_SECRET_ID") or st.secrets.get("FYERS_SECRET_KEY") or "").strip()
    redirect_uri = str(st.secrets.get("FYERS_REDIRECT_URI") or "").strip()

    callback_code = str(st.query_params.get("auth_code", "") or st.query_params.get("code", "")).strip()
    callback_state = str(st.query_params.get("state", "")).strip()
    callback_error = str(st.query_params.get("error_description", "") or st.query_params.get("error", "")).strip()

    if callback_error:
        st.error(f"FYERS login was cancelled or rejected: {callback_error}")
        st.query_params.clear()
    elif callback_code:
        try:
            if not (app_id and secret_key and redirect_uri):
                raise FyersLoginError(
                    "Add FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI in Streamlit Secrets first."
                )
            if not validate_signed_state(callback_state, secret_key):
                raise FyersLoginError(
                    "The FYERS login return could not be verified or has expired. "
                    "Click Connect FYERS and complete a fresh login."
                )
            with st.spinner("Finishing FYERS connection..."):
                token_bundle = exchange_code_for_tokens(app_id, secret_key, redirect_uri, callback_code)
            st.session_state["fyers_access_token"] = token_bundle["access_token"]
            if token_bundle.get("refresh_token"):
                st.session_state["fyers_refresh_token"] = token_bundle["refresh_token"]
            st.session_state["fyers_client_id"] = app_id
            st.session_state["fyers_connected_at"] = datetime.now().isoformat(timespec="seconds")
            update_ok, update_message = trigger_fyers_history_update_on_login(
                token_bundle["access_token"], app_id
            )
            st.session_state["_fyers_history_update_status"] = {
                "ok": update_ok,
                "message": update_message,
            }
            st.query_params.clear()
            st.rerun()
        except FyersLoginError as e:
            st.error(str(e))
            st.query_params.clear()
        except Exception as e:
            st.error(f"FYERS connection failed: {e}")
            st.query_params.clear()

    history_setup_pending = not st.session_state.get("_fyers_history_update_status", {}).get("ok", False)
    with st.expander(
        "🔌 FYERS Connection",
        expanded=("fyers_access_token" not in st.session_state) or history_setup_pending,
    ):
        if "fyers_access_token" in st.session_state:
            st.success("Connected to FYERS for this session. Live quotes are fetched from this session when you scan or value holdings.")
            update_status = st.session_state.get("_fyers_history_update_status")
            if update_status:
                if update_status.get("ok"):
                    st.success(update_status.get("message", "Historical-data refresh triggered."))
                else:
                    st.warning(update_status.get("message", "Automatic historical-data refresh is not configured."))
            if not (
                st.secrets.get("GITHUB_ACTIONS_TOKEN")
                or st.secrets.get("GITHUB_TOKEN")
            ):
                setup_message = (
                    "One-time setup for saved history: add GITHUB_ACTIONS_TOKEN in Streamlit Secrets "
                    "using a fine-grained token limited to this repository (Secrets read/write and Contents "
                    "read/write). After setup, each login from your authorized account triggers a history "
                    "refresh; your FYERS PIN is not needed."
                )
                if not st.secrets.get("FYERS_SYNC_ALLOWED_USER_ID"):
                    setup_message += (
                        " Also set FYERS_SYNC_ALLOWED_USER_ID to the verified FYERS account ID shown "
                        "after your first login."
                    )
                st.info(setup_message)
            connected_at = st.session_state.get("fyers_connected_at")
            if connected_at:
                st.caption(f"Connected at {connected_at}")
            st.caption(
                "A login from this app starts a history refresh, and a second refresh is scheduled "
                "for 4:45 pm IST on weekdays. FYERS access tokens expire at 6:30 am the following day, "
                "so you must log in from this app on each trading day for that day's EOD refresh to work. "
                "The app does not automate or bypass FYERS daily 2FA."
            )
            if st.button("Disconnect FYERS"):
                st.session_state.pop("fyers_access_token", None)
                st.session_state.pop("fyers_refresh_token", None)
                st.session_state.pop("fyers_client_id", None)
                st.session_state.pop("fyers_connected_at", None)
                st.rerun()
            return

        if not (app_id and secret_key and redirect_uri):
            st.warning(
                "Complete FYERS setup in Streamlit Secrets. Required keys: "
                "FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI. "
                "Use the App ID and Secret from the same activated app, and make the redirect URL "
                "exactly match the URL registered in the FYERS API dashboard."
            )
            return

        try:
            state = create_signed_state(secret_key)
            login_url = get_login_url(app_id, secret_key, redirect_uri, state=state).generate_authcode()
            st.link_button("🔐 Connect FYERS", login_url, type="primary", use_container_width=True)
            st.caption(
                "Sign in on FYERS. After approval, you will return here and the app will finish "
                "the connection automatically—no copying or pasting an auth code."
            )
        except FyersLoginError as e:
            st.error(str(e))
        except Exception as e:
            st.error(f"Couldn't set up FYERS login: {e}")


def get_live_prices(yf_tickers):
    yf_tickers = [t for t in dict.fromkeys(yf_tickers) if t and not t.startswith("^")]
    if not yf_tickers:
        return {}
    try:
        fyers = get_fyers_client()
    except Exception:
        st.session_state["_fyers_status"] = "not connected in this session"
        return {}

    fyers_symbols = {t: "NSE:" + t.replace(".NS", "") + "-EQ" for t in yf_tickers}
    by_symbol = {}
    failures = []
    # FYERS Standard allows up to 50 symbols per Quotes request. Batching also
    # prevents Nifty 500 scans from failing as one oversized all-symbol request.
    symbols = list(fyers_symbols.values())
    for offset in range(0, len(symbols), 50):
        batch = symbols[offset:offset + 50]
        try:
            response = fyers.quotes({"symbols": ",".join(batch)})
        except Exception as exc:
            failures.append(f"batch {offset // 50 + 1}: request failed ({exc})")
            continue
        if not isinstance(response, dict) or response.get("s") != "ok":
            failures.append(f"batch {offset // 50 + 1}: FYERS did not return a successful quote response")
            continue
        for item in response.get("d", []):
            symbol = item.get("n")
            value = item.get("v", {}).get("lp")
            if symbol and value is not None:
                try:
                    price = float(value)
                    if np.isfinite(price) and price > 0:
                        by_symbol[symbol] = price
                except (TypeError, ValueError):
                    continue

    if not by_symbol:
        st.session_state["_fyers_status"] = "; ".join(failures) or "no live quotes returned"
        return {}
    st.session_state["_fyers_status"] = (
        f"live ({len(by_symbol)}/{len(fyers_symbols)} symbols)"
        if not failures and len(by_symbol) == len(fyers_symbols)
        else f"partial live quotes ({len(by_symbol)}/{len(fyers_symbols)} symbols)"
    )
    return {
        ticker: by_symbol[sym]
        for ticker, sym in fyers_symbols.items()
        if sym in by_symbol
    }


def apply_live_price_overlay(prices_df, tickers):
    live_prices = get_live_prices(tickers)
    result = {}
    for t in tickers:
        if t in live_prices:
            result[t] = float(live_prices[t])
        elif not prices_df.empty and t in prices_df.columns:
            series = prices_df[t].dropna()
            if not series.empty:
                result[t] = float(series.iloc[-1])
    return result

# ==========================================
# STATE INITIALIZATION
# ==========================================
if "navigation_tab" not in st.session_state:
    st.session_state.navigation_tab = "DASHBOARD"

if "active_strategy_view" not in st.session_state:
    st.session_state.active_strategy_view = None

if "editing_strategy_name" not in st.session_state:
    st.session_state.editing_strategy_name = None

if "strategies" not in st.session_state:
    st.session_state.strategies = {
        "Alpha Momentum Multiplier": {
            "created_date": (datetime.now() - timedelta(days=43)).strftime("%Y-%m-%d"),
            "status": "Active",
            "type": "Real",
            "allocated": 100000.0,
            "realized_pnl": 0.0,
            "rebalance_freq": "Monthly",
            "rebalance_day": 1,
            "groups": ["Nifty 500", "Gold ETF"],
            "allocation_multiplier": 1.0,
            "entry_rank": 10,
            "exit_rank": 20,
            "period_days": [252, 120, 60],
            "period_weights": [0.5, 0.3, 0.2],
            "pct_from_high": 15.0,
            "pct_from_low": 0.0,
            "moving_average": "200 EMA",
            "use_rs": True,
            "rs_benchmark": "Nifty 500 / G-Sec",
            "min_price": 10.0,
            "skip_days": 21,
            "positions": [],
            "notification_email": "",
            "email_on_scan": True,
            "email_on_rebalance_date": True,
            "email_on_rebalance_execution": True
        }
    }

# ==========================================
# METRICS & CALCULATIONS ENGINE
# ==========================================
def calculate_days_to_rebalance(rebalance_day):
    today = datetime.now()
    target_date = datetime(today.year, today.month, min(rebalance_day, 28))
    if target_date < today:
        if today.month == 12:
            target_date = datetime(today.year + 1, 1, min(rebalance_day, 28))
        else:
            target_date = datetime(today.year, today.month + 1, min(rebalance_day, 28))
    return (target_date - today).days


def calculate_strategy_metrics(strat):
    positions = strat.get("positions", [])
    realized_pnl = strat.get("realized_pnl", 0.0)
    allocated = strat["allocated"]

    unrealized_pnl = 0.0
    current_holding_val = 0.0
    total_cost_basis = 0.0
    positions_data = []

    if positions and strat["status"] == "Active":
        tickers = [p["Symbol"] for p in positions]
        prices_df = fetch_market_data(tickers, period="1mo")
        live_or_close = apply_live_price_overlay(prices_df, tickers)

        for pos in positions:
            sym = pos["Symbol"]
            buy_qty = pos["Buy Qty"]
            buy_price = pos["Buy Price"]
            cmp_price = live_or_close.get(sym, buy_price)

            cost_val = buy_price * buy_qty
            curr_val = cmp_price * buy_qty
            pos_unrealized = curr_val - cost_val
            pos_pnl_pct = ((cmp_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0.0

            total_cost_basis += cost_val
            unrealized_pnl += pos_unrealized
            current_holding_val += curr_val

            positions_data.append({
                "Symbol": sym.replace(".NS", ""),
                "Buy Qty": buy_qty,
                "Buy Price": f"₹{buy_price:,.2f}",
                "Entry Date": pos["Entry Date"],
                "CMP": f"₹{cmp_price:,.2f}",
                "Current Value": f"₹{curr_val:,.2f}",
                "Current P&L": f"₹{pos_unrealized:,.2f}",
                "Current P&L %": f"{pos_pnl_pct:.2f}%"
            })

    cash_reserve = max(0.0, allocated - total_cost_basis) if total_cost_basis > 0 else allocated
    total_pnl = realized_pnl + unrealized_pnl
    returns_pct = (total_pnl / allocated * 100) if allocated > 0 else 0.0
    current_total_value = cash_reserve + current_holding_val + realized_pnl

    return {
        "allocated": allocated,
        "cash_reserve": cash_reserve,
        "current_holding_val": current_holding_val,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "total_pnl": total_pnl,
        "returns_pct": returns_pct,
        "current_total_value": current_total_value,
        "positions_data": positions_data
    }


MAJOR_MARKET_INDICES = {
    "Nifty 50": "^NSEI",
    "Nifty Bank": "^NSEBANK",
    "Nifty 500": "^CRSLDX",
    "Nifty 200": "^CNX200",
    "Nifty IT": "^CNXIT",
    "Nifty Pharma": "^CNXPHARMA",
    "Nifty Auto": "^CNXAUTO",
    "Nifty FMCG": "^CNXFMCG",
    "Nifty Metal": "^CNXMETAL",
}


def _market_trend_is_bullish(strat, as_of_date=None):
    """Return True when the selected index is above its configured trend indicator."""
    if not strat.get("use_market_trend_filter", False):
        return True
    index_name = strat.get("market_trend_index", "Nifty 50")
    ticker = MAJOR_MARKET_INDICES.get(index_name)
    if not ticker:
        return True
    try:
        history = fetch_market_data([ticker], period="5y")
        if history.empty or ticker not in history.columns:
            return False
        series = history[ticker].dropna()
        if as_of_date is not None:
            series = series.loc[:pd.Timestamp(as_of_date)]
        indicator = strat.get("market_trend_indicator", "Moving Average")
        if indicator == "Moving Average":
            period = int(strat.get("market_trend_ma_period", 200))
            ma_type = strat.get("market_trend_ma_type", "EMA")
            average = series.ewm(span=period, adjust=False).mean() if ma_type == "EMA" else series.rolling(period).mean()
            if len(series) < period or pd.isna(average.iloc[-1]):
                return False
            return bool(series.iloc[-1] > average.iloc[-1])
        # Volatility Stop (ATR-based trailing stop). Above the stop = bullish.
        atr_period = int(strat.get("market_trend_vstop_atr_period", 10))
        multiplier = float(strat.get("market_trend_vstop_multiplier", 2.0))
        # VStop uses only the persisted FYERS OHLC series. If the archive is
        # missing, fail closed instead of substituting another price provider.
        ohlc = index_history_data.load_index_ohlc(ticker)
        if ohlc.empty:
            return False
        ohlc = ohlc.set_index("Date")
        if isinstance(ohlc.columns, pd.MultiIndex):
            if "Close" in ohlc.columns.get_level_values(0):
                close = ohlc["Close"][ticker] if ticker in ohlc["Close"].columns else ohlc["Close"].iloc[:, 0]
            else:
                close = ohlc.xs("Close", axis=1, level=1).iloc[:, 0]
            high = ohlc.xs("High", axis=1, level=0).iloc[:, 0] if "High" in ohlc.columns.get_level_values(0) else close
            low = ohlc.xs("Low", axis=1, level=0).iloc[:, 0] if "Low" in ohlc.columns.get_level_values(0) else close
        else:
            close = ohlc["Close"]
            high = ohlc["High"] if "High" in ohlc.columns else close
            low = ohlc["Low"] if "Low" in ohlc.columns else close
        frame = pd.DataFrame({"close": close, "high": high, "low": low}).dropna()
        if as_of_date is not None:
            frame = frame.loc[:pd.Timestamp(as_of_date)]
        if len(frame) < atr_period + 2:
            return False
        previous_close = frame["close"].shift(1)
        true_range = pd.concat([
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ], axis=1).max(axis=1)
        atr = true_range.ewm(alpha=1 / atr_period, adjust=False, min_periods=atr_period).mean()
        long_stop = frame["close"].rolling(atr_period).max() - multiplier * atr
        # Use an ATR trailing stop approximation for the trend regime.
        stop = long_stop.copy()
        for i in range(1, len(stop)):
            if pd.isna(stop.iloc[i]) or pd.isna(stop.iloc[i - 1]):
                continue
            if frame["close"].iloc[i - 1] > stop.iloc[i - 1]:
                stop.iloc[i] = max(stop.iloc[i], stop.iloc[i - 1])
        return bool(frame["close"].iloc[-1] > stop.iloc[-1])
    except Exception:
        return False



def _ensure_scan_index_history(ticker, min_bars=220):
    """Refresh missing or stale supported index candles from the active FYERS session."""
    ticker = str(ticker or "").strip()
    if ticker not in fyers_chart_data.INDEX_FYERS_SYMBOLS:
        return None

    today = pd.Timestamp.now(tz="Asia/Kolkata").date()
    try:
        saved = index_history_data.load_index_ohlc(ticker)
    except Exception:
        saved = pd.DataFrame()
    latest = pd.to_datetime(
        saved.get("Date", pd.Series(dtype="datetime64[ns]")), errors="coerce"
    ).dropna()
    stale = latest.empty or (pd.Timestamp(today) - latest.max().normalize()).days > 10
    if not saved.empty and len(saved) >= int(min_bars) and not stale:
        return None
    if "fyers_access_token" not in st.session_state:
        return (
            f"Index history for {ticker} is missing or stale. Connect FYERS and rerun the scan "
            "so Happy Trade can fetch its required benchmark candles."
        )

    try:
        start_date = (pd.Timestamp(today) - pd.DateOffset(years=5)).date()
        index_name = index_history_data.TICKER_TO_INDEX_NAME.get(ticker)
        if index_name:
            spec_start = pd.Timestamp(index_history_data.INDEX_HISTORY_SPECS[index_name]["start_date"]).date()
            start_date = max(start_date, spec_start)
        fresh = fyers_chart_data.fetch_index_history(get_fyers_client(), ticker, start_date, today)
        if fresh.empty:
            return (
                f"FYERS returned no candles for {ticker}; check the FYERS historical-data entitlement "
                "and the mapped index symbol."
            )

        fields = ["Date", "Open", "High", "Low", "Close", "Volume"]
        old = saved.reindex(columns=fields) if not saved.empty else pd.DataFrame(columns=fields)
        fresh = fresh.reindex(columns=fields)
        merged = pd.concat([old, fresh], ignore_index=True)
        merged["Date"] = pd.to_datetime(merged["Date"], errors="coerce").dt.tz_localize(None).dt.normalize()
        merged = merged.dropna(subset=["Date", "Close"]).sort_values("Date").drop_duplicates("Date", keep="last")
        merged["Source"] = "FYERS API"
        spec = index_history_data.INDEX_HISTORY_SPECS[index_name]
        path = index_history_data.INDEX_PRICE_DIR / spec["filename"]
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.6f")
        fetch_market_data.clear()
        return (
            f"Refreshed {index_name} from FYERS: {len(merged):,} daily candles, "
            f"{merged['Date'].min().date()} to {merged['Date'].max().date()}."
        )
    except Exception as exc:
        return f"Could not refresh benchmark {ticker} from FYERS ({type(exc).__name__}): {exc}"


def run_strategy_stock_scanner(strat, price_subset=None, current_holdings=None, as_of_date=None, membership_calendar=None, diagnostics=None):
    selected_groups = strat.get("groups", ["Nifty 500"])
    scan_notes = []
    filter_counts = {}
    group_tickers = []
    prices_df = pd.DataFrame()
    filtered_df = pd.DataFrame()
    required_index_tickers = []

    def _scan_exit(result, stage, reason):
        if isinstance(diagnostics, dict):
            diagnostics.update({
                "stage": stage,
                "reason": reason,
                "groups": list(selected_groups),
                "universe_tickers": int(len(group_tickers)),
                "universe_symbols_with_prices": int(sum(1 for t in group_tickers if t in prices_df.columns)),
                "price_rows": int(len(prices_df)),
                "price_columns": int(len(prices_df.columns)),
                "first_price_date": str(prices_df.index.min().date()) if not prices_df.empty else None,
                "last_price_date": str(prices_df.index.max().date()) if not prices_df.empty else None,
                "missing_benchmarks": [
                    t for t in required_index_tickers
                    if t not in prices_df.columns or prices_df[t].dropna().empty
                ],
                "filter_counts": dict(filter_counts),
                "notes": list(scan_notes),
            })
        return result

    use_relative_momentum = bool(strat.get("use_relative_momentum_filter", False))
    relative_index_name = strat.get("relative_momentum_index", "Nifty 50")
    relative_index_ticker = MAJOR_MARKET_INDICES.get(relative_index_name, "^NSEI")

    if price_subset is None:
        group_tickers = get_trusted_tickers_by_group(selected_groups)
        fetch_tickers = list(group_tickers)
        if strat.get("use_rs", True):
            rs_benchmark_name = str(strat.get("rs_benchmark", "Nifty 500 / G-Sec"))
            required_index_tickers.append("^NSEI" if rs_benchmark_name == "Nifty 50 / Liquid ETF" else NIFTY_REGIME_TICKER)
        if use_relative_momentum:
            required_index_tickers.append(relative_index_ticker)
        if strat.get("use_market_trend_filter", False):
            required_index_tickers.append(
                MAJOR_MARKET_INDICES.get(strat.get("market_trend_index", "Nifty 50"), "^NSEI")
            )
        required_index_tickers = list(dict.fromkeys(required_index_tickers))
        for index_ticker in required_index_tickers:
            note = _ensure_scan_index_history(index_ticker)
            if note:
                scan_notes.append(note)
        if use_relative_momentum and relative_index_ticker not in fetch_tickers:
            fetch_tickers.append(relative_index_ticker)
        prices_df = fetch_market_data(fetch_tickers, period="2y")
        candidate_tickers = set(group_tickers)
    else:
        prices_df = price_subset.copy()
        candidate_tickers = None

    if prices_df is None or prices_df.empty:
        return _scan_exit(
            [], "price loading",
            "No historical price rows loaded for this strategy universe. Check the NSE archive and FYERS history.",
        )

    prices_df = prices_df.sort_index()
    latest_prices = prices_df.iloc[-1]
    filtered_df = prices_df.copy()
    if isinstance(diagnostics, dict):
        diagnostics.update({
            "groups": list(selected_groups),
            "universe_tickers": int(len(group_tickers)) if candidate_tickers is not None else int(len(prices_df.columns)),
            "universe_symbols_with_prices": int(sum(1 for t in group_tickers if t in prices_df.columns)) if candidate_tickers is not None else int(len(prices_df.columns)),
            "price_rows": int(len(prices_df)),
            "price_columns": int(len(prices_df.columns)),
            "first_price_date": str(prices_df.index.min().date()),
            "last_price_date": str(prices_df.index.max().date()),
            "missing_benchmarks": [
                t for t in required_index_tickers
                if t not in prices_df.columns or prices_df[t].dropna().empty
            ],
            "notes": list(scan_notes),
        })

    # Backtest calls pass a historical price matrix that also contains benchmark
    # indices and always-fetched overlays. Keep ETF-only backtests inside the
    # exact selected ETF groups; equity universes are filtered by point-in-time
    # membership below. Neither benchmarks nor unselected ETFs are candidates.
    has_equity_group = any(g in selected_groups for g in ("Nifty 500", "Nifty 200"))
    if candidate_tickers is not None:
        filtered_df = filtered_df.loc[:, [c for c in filtered_df.columns if c in candidate_tickers]]
    elif has_equity_group and (membership_calendar is None or membership_calendar.empty):
        # Without a usable PIT ledger, the documented fallback is today's chosen
        # index universe applied historically—not every cached ticker/benchmark.
        selected_assets = set(get_trusted_tickers_by_group(selected_groups))
        selected_assets.update(get_selected_etf_tickers(selected_groups))
        filtered_df = filtered_df.loc[:, [
            c for c in filtered_df.columns if c in selected_assets
        ]]
    elif not has_equity_group:
        selected_assets = get_selected_etf_tickers(selected_groups)
        filtered_df = filtered_df.loc[:, [
            c for c in filtered_df.columns if c in selected_assets
        ]]

    filter_counts["selected universe with prices"] = int(len(filtered_df.columns))
    if filtered_df.empty:
        return _scan_exit([], "universe selection", "No selected-universe symbols matched the downloaded price panel. The constituent list or ticker aliases may be the issue.")

    trend_bullish = _market_trend_is_bullish(strat, as_of_date=as_of_date)
    if strat.get("use_market_trend_filter", False) and not trend_bullish:
        # Bearish regime: do not open new positions. Existing holdings may be
        # liquidated at the backtest rebalance if the exit rule is enabled.
        if strat.get("market_trend_exit_on_bearish", True):
            current_holdings = []
        return _scan_exit([], "market trend filter", "The market-trend filter is bearish or its selected index does not have enough valid history. If you expect bullish signals, inspect the benchmark history or temporarily disable this optional filter.")

    if use_relative_momentum:
        if relative_index_ticker not in prices_df.columns:
            # Fail closed: don't silently admit stocks if benchmark history is missing.
            return _scan_exit([], "relative momentum benchmark", f"Relative momentum needs {relative_index_ticker}, but its price history is missing.")
        benchmark = prices_df[relative_index_ticker].dropna()
        if as_of_date is not None:
            benchmark = benchmark.loc[:pd.Timestamp(as_of_date)]
        if benchmark.empty:
            return _scan_exit([], "relative momentum benchmark", f"No candles are available for benchmark {relative_index_ticker}.")

        # Restrict the numerator to the configured main group. In point-in-time
        # stock backtests, retain the historical archive names and let the
        # membership calendar below decide which ones were eligible on each date.
        if candidate_tickers is None:
            selected_etf_tickers = get_selected_etf_tickers(selected_groups)
            has_equity_group = any(g in selected_groups for g in ("Nifty 500", "Nifty 200"))
            if has_equity_group and membership_calendar is not None:
                candidate_cols = [
                    c for c in filtered_df.columns
                    if not c.startswith("^")
                    and (
                        c not in ALWAYS_FETCH_OVERLAY_TICKERS
                        and c != GSEC_REGIME_TICKER
                        or c in selected_etf_tickers
                    )
                ]
            else:
                main_group_tickers = set(get_trusted_tickers_by_group(selected_groups))
                candidate_cols = [c for c in filtered_df.columns if c in main_group_tickers]
            filtered_df = filtered_df.loc[:, candidate_cols]
        else:
            candidate_cols = [c for c in filtered_df.columns if c in candidate_tickers]

        # RS is the stock's price divided by the selected index level. Scaling
        # the ratio by 100 makes it easier to read but doesn't change the MA test.
        candidate_prices = prices_df.reindex(columns=candidate_cols)
        aligned_benchmark = benchmark.reindex(candidate_prices.index).ffill()
        rs_ratio = candidate_prices.div(aligned_benchmark.replace(0, np.nan), axis=0) * 100.0
        rs_type = strat.get("relative_momentum_ma_type", "SMA")
        rs_period = max(2, int(strat.get("relative_momentum_ma_period", 200)))
        if rs_type == "EMA":
            rs_average = rs_ratio.ewm(span=rs_period, adjust=False, min_periods=rs_period).mean()
        else:
            rs_average = rs_ratio.rolling(window=rs_period, min_periods=rs_period).mean()

        current_rs = rs_ratio.iloc[-1]
        current_rs_average = rs_average.iloc[-1]
        passes_rs = (current_rs > current_rs_average) & current_rs.notna() & current_rs_average.notna()
        filtered_df = filtered_df.loc[:, [c for c in filtered_df.columns if c in passes_rs.index and bool(passes_rs.get(c, False))]]
        filter_counts["relative momentum"] = int(len(filtered_df.columns))
        if filtered_df.empty:
            return _scan_exit([], "relative momentum filter", "No stocks passed the relative-momentum moving-average filter.")

    ma_config = strat.get("moving_average", "200 EMA")
    if ma_config != "None":
        period = 200 if "200" in ma_config else (100 if "100" in ma_config else 50)
        is_ema = "EMA" in ma_config
        if is_ema:
            ma_vals = prices_df.ewm(span=period, adjust=False).mean().iloc[-1]
        else:
            ma_vals = prices_df.rolling(window=period).mean().iloc[-1]
        filtered_df = filtered_df.loc[:, latest_prices > ma_vals]
    filter_counts["moving average"] = int(len(filtered_df.columns))

    pct_high = strat.get("pct_from_high", 15.0)
    if pct_high > 0 and len(filtered_df) >= 252:
        period_high = filtered_df.iloc[-252:].max()
        pct_diff = ((period_high - latest_prices) / period_high) * 100
        filtered_df = filtered_df.loc[:, pct_diff <= pct_high]
    filter_counts["distance from 52-week high"] = int(len(filtered_df.columns))

    min_price = strat.get("min_price", 0.0)
    if min_price > 0:
        filtered_df = filtered_df.loc[:, latest_prices >= min_price]
    filter_counts["minimum price"] = int(len(filtered_df.columns))

    if as_of_date is not None and membership_calendar is not None and not membership_calendar.empty:
        selected_etf_tickers = get_selected_etf_tickers(selected_groups)
        # A historical price matrix includes indices and every cached ETF. Only
        # instruments explicitly selected by the strategy, or symbols represented
        # in this index's membership ledger and active on the signal date, may rank.
        membership_symbols = {
            str(symbol).strip().upper()
            for symbol in membership_calendar["symbol"].dropna().tolist()
        }
        eligible_cols = [
            c for c in filtered_df.columns
            if not c.startswith("^")
            and (
                c in selected_etf_tickers
                or (
                    c.upper() in membership_symbols
                    and momentum_data.was_member(c, as_of_date, membership_calendar)
                )
            )
        ]
        filtered_df = filtered_df[eligible_cols]
    filter_counts["index membership / eligibility"] = int(len(filtered_df.columns))

    if strat.get("use_rs", True):
        rs_benchmark = str(strat.get("rs_benchmark", "Nifty 500 / G-Sec"))
        if rs_benchmark == "Nifty 50 / Liquid ETF":
            rs_index_ticker = "^NSEI"
            rs_defensive_ticker = "LIQUIDBEES.NS"
        else:
            rs_index_ticker = NIFTY_REGIME_TICKER
            rs_defensive_ticker = GSEC_REGIME_TICKER

        # A missing/stale benchmark must not silently disable the regime rule.
        if rs_index_ticker not in prices_df.columns or rs_defensive_ticker not in prices_df.columns:
            missing = [t for t in (rs_index_ticker, rs_defensive_ticker) if t not in prices_df.columns or prices_df[t].dropna().empty]
            return _scan_exit([], "relative strength benchmark", "Required RS benchmark candles are missing: " + ", ".join(missing) + ". Reconnect FYERS and rerun the scan.")
        rs_index = prices_df[rs_index_ticker].dropna()
        rs_defensive = prices_df[rs_defensive_ticker].dropna()
        if as_of_date is not None:
            cutoff = pd.Timestamp(as_of_date)
            rs_index = rs_index.loc[:cutoff]
            rs_defensive = rs_defensive.loc[:cutoff]
        rs_ratio = rs_index.div(rs_defensive.replace(0, np.nan)).dropna()
        rs_sma = rs_ratio.rolling(window=50, min_periods=50).mean()
        if rs_ratio.empty or pd.isna(rs_sma.iloc[-1]):
            return _scan_exit([], "relative strength history", f"The {rs_index_ticker} / {rs_defensive_ticker} ratio has fewer than 50 overlapping valid sessions.")
        if rs_ratio.iloc[-1] < rs_sma.iloc[-1]:
            scan_notes.append(f"Relative-strength regime is below its 50-session average; {rs_defensive_ticker} is the configured defensive fallback.")
            return _scan_exit([rs_defensive_ticker], "defensive regime", f"The strategy selected {rs_defensive_ticker} because the market-relative-strength ratio is below its 50-session average.")

    periods = [int(p) for p in strat.get("period_days", [252, 120, 60]) if int(p) > 0]
    raw_weights = strat.get("period_weights", [0.5, 0.3, 0.2])
    weights = np.asarray(raw_weights, dtype=float).flatten()
    if len(weights) != len(periods) or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        weights = np.ones(len(periods), dtype=float)
    weights = weights / weights.sum()
    skip_days = max(0, int(strat.get("skip_days", 0)))

    end_idx = -1 - skip_days
    rank_components = []
    component_weights = []
    for period, weight in zip(periods, weights):
        start_idx = end_idx - period
        if len(filtered_df) <= abs(start_idx):
            continue
        start_prices = filtered_df.iloc[start_idx]
        end_prices = filtered_df.iloc[end_idx]
        valid = (
            start_prices.notna()
            & end_prices.notna()
            & np.isfinite(start_prices)
            & np.isfinite(end_prices)
            & start_prices.gt(0)
        )
        returns = (end_prices[valid] / start_prices[valid]) - 1.0
        returns = returns.replace([np.inf, -np.inf], np.nan).dropna()
        if returns.empty:
            continue
        rank_components.append(returns.rank(ascending=False, method="min"))
        component_weights.append(float(weight))

    entry_n = int(strat.get("entry_rank", 10))

    # Never fall back to column order when there is insufficient history.
    # A symbol must have a valid return for every configured period that is
    # included in the composite score; missing periods cannot give it an
    # artificially low (better) weighted rank.
    if not rank_components:
        return _scan_exit([], "momentum ranking", "Not enough usable price history for any configured momentum lookback. Check the stock-history date range or use shorter lookbacks.")

    rank_frame = pd.concat(rank_components, axis=1)
    complete_rank_frame = rank_frame.dropna(axis=0, how="any")
    if complete_rank_frame.empty:
        return _scan_exit([], "momentum ranking", "No stocks have valid returns across all configured momentum lookbacks. Some symbols have missing or short histories.")
    active_weights = np.asarray(component_weights, dtype=float)
    active_weights = active_weights / active_weights.sum()
    composite_rank = (
        complete_rank_frame.mul(active_weights, axis=1)
        .sum(axis=1)
        .sort_values()
    )

    ordinal_rank = pd.Series(range(1, len(composite_rank) + 1), index=composite_rank.index)
    exit_n = max(strat.get("exit_rank", entry_n), entry_n)
    held = set(current_holdings or [])

    retained = [s for s in ordinal_rank.index if s in held and ordinal_rank[s] <= exit_n]
    slots_needed = max(entry_n - len(retained), 0)
    new_entries = [s for s in ordinal_rank.index if s not in retained][:slots_needed]

    result = retained + new_entries
    if isinstance(diagnostics, dict):
        diagnostics.update({
            "stage": "complete",
            "reason": f"Ranked {len(composite_rank):,} eligible symbols and selected {len(result):,} target(s).",
            "rankable_symbols": int(len(composite_rank)),
            "selected_targets": int(len(result)),
            "filter_counts": dict(filter_counts),
            "notes": list(scan_notes),
        })
    return result


def run_backtest_simulation(strat_config, initial_capital, start_date, end_date, use_point_in_time=True, trading_cost_config=None):
    cost_config = trading_costs.normalize_cost_config(trading_cost_config)
    selected_groups = strat_config.get("groups", ["Nifty 500"])
    apply_point_in_time = use_point_in_time and any(
        group in selected_groups for group in ("Nifty 500", "Nifty 200")
    )

    if apply_point_in_time:
        # Keep the historical stock archive for point-in-time membership and
        # add the selected ETF groups so they can be ranked in the same strategy.
        tickers = list(dict.fromkeys(
            fyers_history_data.load_all_symbols()
            + get_trusted_tickers_by_group(selected_groups)
        ))
        # Prefer the committed reconstructed Nifty 500 membership intervals. The
        # public interval source does not provide an exact Nifty 200 series, so
        # Nifty 200 backtests currently use the broader Nifty 500 calendar as a
        # proxy. The app keeps this limitation explicit rather than inventing
        # an exact historical Nifty 200 membership list.
        membership_target = "Nifty 500" if "Nifty 500" in selected_groups else "Nifty 200"
        membership_calendar = index_history_data.get_membership_calendar(membership_target)
        nifty200_membership_is_approximate = (
            membership_target == "Nifty 200" and membership_calendar.empty
        )

        # The validated interval source begins in 2014 for Nifty 500. Preserve the
        # older calendar before that cutoff instead of leaving early backtest dates
        # with no eligible stocks. After the cutoff, prefer the checked-in interval
        # ledger, which has a better constituent count than the older legacy calendar.
        if nifty200_membership_is_approximate:
            membership_calendar = index_history_data.get_membership_calendar("Nifty 500")

        try:
            legacy_membership = momentum_data.load_membership_calendar()
        except Exception:
            legacy_membership = pd.DataFrame(columns=["symbol", "start", "end"])

        if not membership_calendar.empty and legacy_membership is not None and not legacy_membership.empty:
            cutoff = pd.to_datetime(membership_calendar["start"], errors="coerce").min()
            if pd.notna(cutoff):
                legacy_prefix = legacy_membership[["symbol", "start", "end"]].copy()
                legacy_prefix["start"] = pd.to_datetime(legacy_prefix["start"], errors="coerce")
                legacy_prefix["end"] = pd.to_datetime(legacy_prefix["end"], errors="coerce")
                cutoff_previous_day = cutoff - pd.Timedelta(days=1)
                legacy_prefix = legacy_prefix[
                    legacy_prefix["start"].notna() & (legacy_prefix["start"] < cutoff)
                ].copy()
                legacy_prefix["end"] = legacy_prefix["end"].fillna(cutoff_previous_day)
                legacy_prefix.loc[
                    legacy_prefix["end"] > cutoff_previous_day, "end"
                ] = cutoff_previous_day
                legacy_prefix = legacy_prefix[
                    legacy_prefix["end"].notna()
                    & (legacy_prefix["end"] >= legacy_prefix["start"])
                ]
                membership_calendar = pd.concat(
                    [legacy_prefix, membership_calendar], ignore_index=True
                ).drop_duplicates()
        elif membership_calendar.empty and legacy_membership is not None and not legacy_membership.empty:
            membership_calendar = legacy_membership[["symbol", "start", "end"]].copy()

        if membership_calendar.empty:
            st.error(
                "Historical constituent membership could not be loaded. "
                "The point-in-time backtest has been stopped rather than silently "
                "using today's index constituents for past dates."
            )
            return None, None, None

        if nifty200_membership_is_approximate:
            st.warning(
                "Nifty 200 point-in-time membership is not available in the stored source. "
                "This run uses historical Nifty 500 membership as a broader proxy, so "
                "results are approximate and may differ from a true Nifty 200 backtest."
            )

        # Request price history for all historical members as well as the
        # 970-symbol archive and current index constituents. This enables
        # members that later exited the index to be considered on earlier dates.
        if membership_calendar is not None and not membership_calendar.empty:
            historic_members = (
                membership_calendar["symbol"].dropna().astype(str).str.upper().tolist()
            )
            historic_members = [
                symbol if symbol.endswith(".NS") else f"{symbol}.NS"
                for symbol in historic_members
            ]
            tickers = list(dict.fromkeys(tickers + historic_members))
    else:
        # ETF-only strategies don't need index membership restrictions.
        tickers = get_trusted_tickers_by_group(selected_groups)
        membership_calendar = None

    if strat_config.get("use_relative_momentum_filter", False):
        relative_index_ticker = MAJOR_MARKET_INDICES.get(
            strat_config.get("relative_momentum_index", "Nifty 50"), "^NSEI"
        )
        if relative_index_ticker not in tickers:
            tickers.append(relative_index_ticker)

    # Explicitly request the series needed by the configured risk/relative-strength
    # rules. A missing benchmark must never silently disable a strategy filter.
    required_benchmarks = []
    if strat_config.get("use_rs", True):
        if str(strat_config.get("rs_benchmark", "Nifty 500 / G-Sec")) == "Nifty 50 / Liquid ETF":
            rs_index_ticker, rs_defensive_ticker = "^NSEI", "LIQUIDBEES.NS"
        else:
            rs_index_ticker, rs_defensive_ticker = NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER
        required_benchmarks.extend([rs_index_ticker, rs_defensive_ticker])
    if strat_config.get("use_relative_momentum_filter", False):
        required_benchmarks.append(relative_index_ticker)
    if strat_config.get("use_market_trend_filter", False):
        trend_ticker = MAJOR_MARKET_INDICES.get(
            strat_config.get("market_trend_index", "Nifty 50")
        )
        if trend_ticker:
            required_benchmarks.append(trend_ticker)
    tickers = list(dict.fromkeys(tickers + required_benchmarks))

    # At the start of each backtest, check inception coverage for the actual
    # index universe/benchmarks used by this strategy, not just the requested
    # date range. Full inception coverage and exact price-value validation are
    # distinct from having enough candles for a shorter recent backtest.
    relevant_index_names = {
        group for group in selected_groups
        if group in index_history_data.INDEX_HISTORY_SPECS
    }
    relevant_index_names.update(
        index_history_data.TICKER_TO_INDEX_NAME[ticker]
        for ticker in required_benchmarks
        if ticker in index_history_data.TICKER_TO_INDEX_NAME
    )
    if relevant_index_names:
        inception_audit = index_history_data.get_index_history_coverage()
        if not inception_audit.empty:
            incomplete_inception = []
            for index_name in sorted(relevant_index_names):
                row_match = inception_audit.loc[inception_audit["Index"] == index_name]
                if row_match.empty:
                    incomplete_inception.append(f"{index_name}: no coverage audit row")
                    continue
                audit_row = row_match.iloc[0]
                if not bool(audit_row.get("Start Covered", False)):
                    incomplete_inception.append(
                        f"{index_name}: first saved candle {audit_row.get('First Stored Date') or 'missing'}; "
                        f"target start {audit_row.get('Target Start')}"
                    )
                else:
                    launch_text = str(audit_row.get("Launch Date", "") or "")
                    if launch_text and launch_text != "Not published" and not bool(
                        audit_row.get("Launch Covered", False)
                    ):
                        incomplete_inception.append(
                            f"{index_name}: stored candles do not reach verified launch date {launch_text}"
                        )
                    gap_count = pd.to_numeric(
                        audit_row.get("Internal Gaps (>7d)", 0), errors="coerce"
                    )
                    missing_sessions = pd.to_numeric(
                        audit_row.get("Missing Sessions vs Nifty 50", 0), errors="coerce"
                    )
                    invalid_ohlc_count = pd.to_numeric(
                        audit_row.get("Invalid OHLC Rows", 0), errors="coerce"
                    )
                    if pd.notna(gap_count) and int(gap_count) > 0:
                        incomplete_inception.append(
                            f"{index_name}: has calendar gaps longer than 7 days"
                        )
                    if pd.notna(missing_sessions) and int(missing_sessions) > 0:
                        incomplete_inception.append(
                            f"{index_name}: misses {int(missing_sessions)} trading session(s) versus Nifty 50"
                        )
                    if pd.notna(invalid_ohlc_count) and int(invalid_ohlc_count) > 0:
                        incomplete_inception.append(
                            f"{index_name}: has invalid OHLC rows"
                        )
            if incomplete_inception:
                st.warning(
                    "Index inception/data-quality check: the stored history for this strategy is not "
                    "confirmed complete from the configured earliest target. "
                    + "; ".join(incomplete_inception)
                    + ". A shorter backtest can only be trusted if its date-range and warm-up checks pass; "
                    "full historical coverage and value-by-value exchange validation are not yet established."
                )

    start_ts = pd.Timestamp(start_date).normalize()
    end_ts = pd.Timestamp(end_date).normalize()
    today = pd.Timestamp.now().normalize()
    if end_ts < start_ts:
        st.error("Backtest stopped: the end date is earlier than the start date.")
        return None, None, None
    if end_ts > today:
        st.error("Backtest stopped: the end date is in the future. Select a date up to today.")
        return None, None, None
    if float(initial_capital) <= 0:
        st.error("Backtest stopped: initial capital must be greater than zero.")
        return None, None, None

    # Audit the raw stored candles before forward-filling for portfolio valuation.
    # Using ffilled data here would hide stale/missing source dates.
    audit_tickers = list(dict.fromkeys(
        tickers + ALWAYS_FETCH_OVERLAY_TICKERS + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER, "^NSEI"]
    ))
    if len(tickers) > 1000:
        raw_prices = fyers_history_data.load_close_prices(list(dict.fromkeys(
            ALWAYS_FETCH_OVERLAY_TICKERS + [NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER, "^NSEI"]
        )))
    else:
        raw_prices = fyers_history_data.load_close_prices(audit_tickers)
    if raw_prices is None:
        raw_prices = pd.DataFrame()

    # Include enough pre-start history for the longest momentum/MA warm-up.
    raw_lookbacks = strat_config.get("lookbacks", [252, 120, 90, 60])
    try:
        if isinstance(raw_lookbacks, dict):
            max_lookback = max(int(k) for k in raw_lookbacks)
        else:
            max_lookback = max(int(value) for value in raw_lookbacks)
    except (TypeError, ValueError):
        max_lookback = 252
    ma_warmup = int(strat_config.get("ma_period", strat_config.get("ema_period", 200)) or 200)
    archive_start_ts = start_ts - pd.Timedelta(
        days=max(900, max_lookback * 2 + ma_warmup + 60)
    )
    # This is a supplemental source: FYERS remains primary, while the permanent
    # NSE Release fills older/missing equity and ETF history for the requested period.
    archive_error = None
    try:
        nse_prices = nse_archive_data.load_close_prices(
            audit_tickers, archive_start_ts, end_ts
        )
        if nse_prices is not None and not nse_prices.empty:
            raw_prices = nse_archive_data.merge_price_panels(raw_prices, nse_prices)
    except Exception as exc:
        archive_error = str(exc)

    if raw_prices is None or raw_prices.empty:
        message = (
            "Backtest stopped: no saved FYERS data or downloadable NSE historical archive data is available. "
            "Run the 'Update all-stock NSE historical archive' workflow and retry."
        )
        if archive_error:
            message += f" Archive detail: {archive_error}"
        st.error(message)
        return None, None, None
    raw_prices.index = pd.to_datetime(raw_prices.index, errors="coerce").tz_localize(None).normalize()
    raw_prices = raw_prices.loc[~raw_prices.index.isna()].sort_index()
    raw_prices = raw_prices.loc[:, ~raw_prices.isna().all(axis=0)]
    in_range = raw_prices.loc[(raw_prices.index >= start_ts) & (raw_prices.index <= end_ts)]
    in_range_dates = in_range.index[in_range.notna().any(axis=1)] if not in_range.empty else pd.DatetimeIndex([])
    if in_range_dates.empty:
        st.error(
            f"Backtest stopped: no saved FYERS candles exist between {start_ts.date()} and {end_ts.date()}. "
            "The app will not shift your requested backtest dates silently."
        )
        return None, None, None
    if (in_range_dates[0] - start_ts).days > 10:
        st.error(
            f"Backtest stopped: you requested {start_ts.date()}, but the first saved candle in that period is "
            f"{in_range_dates[0].date()}. Historical data before that date is missing."
        )
        return None, None, None
    if (end_ts - in_range_dates[-1]).days > 10:
        st.error(
            f"Backtest stopped: you requested data through {end_ts.date()}, but the latest stored candle in that "
            f"period is {in_range_dates[-1].date()}. Update the FYERS history before trusting the results."
        )
        return None, None, None

    coverage_errors = []
    for ticker in dict.fromkeys(required_benchmarks):
        if ticker not in raw_prices.columns:
            coverage_errors.append(f"{ticker}: no saved history file")
            continue
        series = raw_prices[ticker].dropna()
        if series.empty:
            coverage_errors.append(f"{ticker}: no valid candles")
            continue
        if series.index.min() > start_ts + pd.Timedelta(days=7):
            coverage_errors.append(
                f"{ticker}: saved history starts {series.index.min().date()}, after requested start {start_ts.date()}"
            )
        to_end = series.loc[:end_ts]
        if to_end.empty or (end_ts - to_end.index.max()).days > 10:
            coverage_errors.append(
                f"{ticker}: no recent candle within 10 days of requested end {end_ts.date()}"
            )
        # Index benchmark candles should be continuous across normal market weeks.
        selected_period = series.loc[(series.index >= start_ts) & (series.index <= end_ts)]
        if len(selected_period) > 1:
            long_gaps = selected_period.index.to_series().diff().dropna()
            if (long_gaps > pd.Timedelta(days=7)).any():
                coverage_errors.append(f"{ticker}: one or more gaps longer than 7 calendar days in the requested range")
        # NSE indices share the same trading calendar where their history
        # overlaps. If a required index series misses dates present in Nifty 50,
        # do not forward-fill through those missing benchmark sessions.
        if ticker.startswith("^") and ticker != "^NSEI" and "^NSEI" in raw_prices.columns:
            reference = raw_prices["^NSEI"].dropna()
            if not reference.empty and not series.empty:
                overlap_start = max(start_ts, reference.index.min(), series.index.min())
                overlap_end = min(end_ts, reference.index.max(), series.index.max())
                if overlap_start <= overlap_end:
                    ref_dates = set(reference.loc[
                        (reference.index >= overlap_start) & (reference.index <= overlap_end)
                    ].index)
                    ticker_dates = set(series.loc[
                        (series.index >= overlap_start) & (series.index <= overlap_end)
                    ].index)
                    missing_sessions = len(ref_dates - ticker_dates)
                    if missing_sessions:
                        coverage_errors.append(
                            f"{ticker}: missing {missing_sessions} sessions compared with Nifty 50 in the selected range"
                        )
    if coverage_errors:
        st.error(
            "Backtest stopped because a required benchmark failed the history-coverage check:\n\n"
            + "\n".join(f"• {error}" for error in coverage_errors)
        )
        return None, None, None

    # This call reads the same stored FYERS candles but forward-fills after each
    # symbol's first real candle so daily holdings can be marked between sessions.
    prices_df = fetch_market_data(
        tickers,
        period="5y",
        full_history=True,
        start_date=archive_start_ts,
        end_date=end_ts,
    )
    if prices_df is None or prices_df.empty:
        st.error("Backtest stopped: no usable FYERS/NSE historical prices were loaded for the selected universe. Check the NSE archive workflow and try again.")
        return None, None, None
    prices_df.index = pd.to_datetime(prices_df.index, errors="coerce").tz_localize(None).normalize()
    prices_df = prices_df.loc[~prices_df.index.isna()].sort_index()
    prices_df = prices_df.loc[:end_ts]
    simulation_dates = prices_df.index[(prices_df.index >= start_ts) & (prices_df.index <= end_ts)]
    if simulation_dates.empty:
        st.error("Backtest stopped: no trading sessions exist in the selected date range.")
        return None, None, None

    # Large one-session close changes can be genuine crashes/rallies, but in an
    # unadjusted series they can also signal a split, bonus or other corporate
    # action. Flag them because an unadjusted price drop can corrupt portfolio
    # returns and drawdown even if the CSV has a complete date range.
    potential_corporate_actions = []
    quality_start = start_ts - pd.Timedelta(days=400)
    for ticker in tickers:
        if ticker not in raw_prices.columns or ticker.startswith("^"):
            continue
        series = raw_prices[ticker].dropna()
        series = series.loc[(series.index >= quality_start) & (series.index <= end_ts)]
        if len(series) < 2:
            continue
        jumps = series.pct_change()
        jumps = jumps[jumps.abs() >= 0.25]
        for event_date, move in jumps.items():
            potential_corporate_actions.append(
                (ticker, event_date.date().isoformat(), float(move) * 100.0)
            )
    if potential_corporate_actions:
        examples = [
            f"{symbol} on {event_date}: {change:+.1f}%"
            for symbol, event_date, change in potential_corporate_actions[:12]
        ]
        st.warning(
            f"Data-quality warning: found {len(potential_corporate_actions)} single-session stock price "
            "moves of 25% or more in the backtest window/warm-up. Some may be real, but others may be "
            "unadjusted splits/bonuses/corporate actions. Check these dates before trusting total return "
            "or maximum drawdown. Examples: " + "; ".join(examples)
        )

    # Require enough pre-start rows to calculate momentum, proximity, MA and RS
    # signals without borrowing future values or starting with a warmed-up curve.
    lookback_periods = [int(p) for p in strat_config.get("period_days", [252, 120, 60]) if int(p) > 0]
    required_lookback = max(lookback_periods or [252])
    ma_config = strat_config.get("moving_average", "200 EMA")
    if ma_config != "None":
        required_lookback = max(
            required_lookback,
            200 if "200" in ma_config else (100 if "100" in ma_config else 50),
        )
    if strat_config.get("pct_from_high", 15.0) > 0:
        required_lookback = max(required_lookback, 252)
    if strat_config.get("use_relative_momentum_filter", False):
        required_lookback = max(
            required_lookback,
            int(strat_config.get("relative_momentum_ma_period", 200)),
        )
    required_lookback += int(strat_config.get("skip_days", 0)) + 2
    previous_dates = prices_df.index[prices_df.index < simulation_dates[0]]
    if len(previous_dates) < required_lookback:
        st.error(
            f"Backtest stopped: only {len(previous_dates)} warm-up sessions are available before "
            f"{simulation_dates[0].date()}, but this strategy needs {required_lookback}. "
            "Choose a later start date or complete the missing historical downloads."
        )
        return None, None, None
    for ticker in dict.fromkeys(required_benchmarks):
        benchmark_history = raw_prices[ticker].dropna()
        bars_before_start = benchmark_history.loc[benchmark_history.index < simulation_dates[0]]
        if len(bars_before_start) < required_lookback:
            st.error(
                f"Backtest stopped: {ticker} has only {len(bars_before_start)} valid stored candles "
                f"before the start date; {required_lookback} are required for this strategy's lookbacks."
            )
            return None, None, None

    # Mark the portfolio to market every trading day. Momentum signals are
    # computed using the previous session's close and changes take effect at
    # the next session's close. This avoids trading on a close that was needed
    # to calculate that same day's signal and lets MDD see intra-month declines.
    frequency = str(strat_config.get("rebalance_freq", "Monthly")).strip().lower()
    rebalance_day = max(1, min(28, int(strat_config.get("rebalance_day", 1))))
    scheduled_dates = {simulation_dates[0]}

    if frequency == "weekly":
        period_keys = simulation_dates.to_period("W-FRI")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            if len(period_dates):
                scheduled_dates.add(period_dates[0])
    elif frequency == "quarterly":
        period_keys = simulation_dates.to_period("Q")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            anchor_month = key.start_time.month
            candidates = period_dates[
                (period_dates.month == anchor_month) & (period_dates.day >= rebalance_day)
            ]
            if len(candidates):
                scheduled_dates.add(candidates[0])
    else:
        period_keys = simulation_dates.to_period("M")
        for key in period_keys.unique():
            period_dates = simulation_dates[period_keys == key]
            candidates = period_dates[period_dates.day >= rebalance_day]
            if len(candidates):
                scheduled_dates.add(candidates[0])

    portfolio_history = []
    holdings_history = []
    current_cash = float(initial_capital)
    current_holdings = {}
    multiplier = float(strat_config.get("allocation_multiplier", 1.0))
    all_dates = prices_df.index
    cumulative_trading_costs = 0.0
    daily_transaction_cost = 0.0
    daily_buy_turnover = 0.0
    daily_sell_turnover = 0.0
    daily_trade_count = 0

    def execute_target_holdings(target_holdings):
        nonlocal current_cash, current_holdings, cumulative_trading_costs
        nonlocal daily_transaction_cost, daily_buy_turnover, daily_sell_turnover, daily_trade_count
        current_holdings, current_cash, trade_stats = trading_costs.rebalance_holdings(
            current_holdings=current_holdings,
            target_holdings=target_holdings,
            close_row=close_row,
            current_cash=current_cash,
            config=cost_config,
            allow_leverage=multiplier > 1.0,
        )
        daily_transaction_cost += trade_stats["Trading Costs"]
        daily_buy_turnover += trade_stats["Buy Turnover"]
        daily_sell_turnover += trade_stats["Sell Turnover"]
        daily_trade_count += trade_stats["Trade Count"]
        cumulative_trading_costs += trade_stats["Trading Costs"]

    for dt in simulation_dates:
        close_row = prices_df.loc[dt]
        daily_transaction_cost = 0.0
        daily_buy_turnover = 0.0
        daily_sell_turnover = 0.0
        daily_trade_count = 0

        # Never value an open holding indefinitely at its last known close.
        # If more than five rows have passed without another candle, there is
        # no reliable portfolio liquidation / corporate-action price. Stop
        # instead of emitting misleading final return and max-drawdown metrics.
        unpriceable_holdings = []
        for sym in current_holdings:
            price_now = close_row.get(sym, np.nan)
            if pd.isna(price_now) or not np.isfinite(float(price_now)) or float(price_now) <= 0:
                last_real_date = None
                if sym in raw_prices.columns:
                    observed = raw_prices[sym].dropna()
                    observed = observed.loc[observed.index <= dt]
                    if not observed.empty:
                        last_real_date = observed.index[-1].date().isoformat()
                unpriceable_holdings.append(
                    f"{sym} (last real close: {last_real_date or 'none'})"
                )
        if unpriceable_holdings:
            st.error(
                "Backtest stopped rather than calculate unreliable returns/drawdown. "
                "An open holding has no recent FYERS candle: "
                + ", ".join(unpriceable_holdings[:10])
                + ". This may indicate delisting, suspension, a missing download, or a corporate action. "
                "Check the history and delisting/corporate-action outcome before trusting the metrics."
            )
            return None, None, None

        # Daily valuation before trades: prior positions earn the close-to-close
        # move up to this date. The same close is then used to execute today's
        # target, which was generated from the prior session's data only.
        daily_value = float(current_cash)
        for sym, qty in current_holdings.items():
            price = close_row.get(sym, np.nan)
            if pd.notna(price) and float(price) > 0:
                daily_value += int(qty) * float(price)

        if dt in scheduled_dates:
            prior = all_dates[all_dates < dt]
            if len(prior):
                signal_dt = prior[-1]
                historical_sub_df = prices_df.loc[:signal_dt]
                selected_stocks = run_strategy_stock_scanner(
                    strat_config,
                    price_subset=historical_sub_df,
                    current_holdings=list(current_holdings.keys()),
                    as_of_date=signal_dt,
                    membership_calendar=membership_calendar,
                )
                holdings_history.append((dt, list(selected_stocks)))

                if selected_stocks:
                    # Keep only candidates with a real saved execution close.
                    tradable = [
                        sym for sym in selected_stocks
                        if sym in raw_prices.columns
                        and pd.notna(raw_prices.at[dt, sym])
                        and np.isfinite(float(raw_prices.at[dt, sym]))
                        and float(raw_prices.at[dt, sym]) > 0
                    ]
                    if tradable:
                        # Reserve an estimated cost buffer for unleveraged portfolios.
                        effective_pool = max(float(daily_value), 0.0) * multiplier
                        if multiplier <= 1.0:
                            effective_pool /= (
                                1.0
                                + trading_costs.effective_cost_rate("buy", cost_config)
                                + trading_costs.effective_cost_rate("sell", cost_config)
                            )
                        allocation_per_stock = effective_pool / len(tradable)
                        target_holdings = {}
                        for sym in tradable:
                            price = float(close_row[sym])
                            qty = int(allocation_per_stock // price) if price > 0 else 0
                            if qty > 0:
                                target_holdings[sym] = qty
                        execute_target_holdings(target_holdings)
                elif (
                    strat_config.get("use_market_trend_filter", False)
                    and not _market_trend_is_bullish(strat_config, as_of_date=signal_dt)
                ):
                    # Honour the user setting: hold during bearish periods when
                    # exit-on-bearish is disabled; otherwise sell at the next rebalance.
                    if strat_config.get("market_trend_exit_on_bearish", True):
                        execute_target_holdings({})
                else:
                    # An empty target during a non-bearish rebalance exits holdings
                    # which no longer qualify; the sell-side costs are deducted.
                    execute_target_holdings({})

        # Re-value at today's close after any rebalance. Record every trading day,
        # not only rebalance dates, so maximum drawdown and daily risk metrics
        # are calculated from a daily equity curve.
        marked_value = float(current_cash)
        for sym, qty in current_holdings.items():
            price = close_row.get(sym, np.nan)
            if pd.notna(price) and float(price) > 0:
                marked_value += int(qty) * float(price)
        portfolio_history.append({
            "Date": dt,
            "Portfolio Value": marked_value,
            "Trading Costs": float(daily_transaction_cost),
            "Cumulative Trading Costs": float(cumulative_trading_costs),
            "Buy Turnover": float(daily_buy_turnover),
            "Sell Turnover": float(daily_sell_turnover),
            "Trade Count": int(daily_trade_count),
        })

    if not portfolio_history:
        return None, None, None

    df_res = pd.DataFrame(portfolio_history).set_index("Date")
    df_res["Daily Return"] = df_res["Portfolio Value"].pct_change().fillna(0.0)
    df_res["Peak Value"] = df_res["Portfolio Value"].cummax()
    df_res["Drawdown"] = df_res["Portfolio Value"] / df_res["Peak Value"] - 1.0
    return df_res, holdings_history, prices_df

# ==========================================
# STRATEGY EMAIL NOTIFICATIONS
# ==========================================
def _email_secret(name, default=""):
    try:
        return str(st.secrets.get(name, default) or default).strip()
    except Exception:
        return str(default or "").strip()


def _smtp_configuration():
    username = _email_secret("SMTP_USERNAME")
    try:
        smtp_port = int(_email_secret("SMTP_PORT", "587") or "587")
    except (TypeError, ValueError):
        smtp_port = 587
    return {
        "smtp_host": _email_secret("SMTP_HOST"),
        "smtp_port": smtp_port,
        "username": username,
        "password": _email_secret("SMTP_PASSWORD"),
        "sender": _email_secret("SMTP_FROM", username),
    }


def _latest_saved_price_map(symbols):
    symbols = list(dict.fromkeys(str(symbol).strip() for symbol in (symbols or []) if str(symbol).strip()))
    if not symbols:
        return {}, None
    frame = fetch_market_data(symbols, period="5d")
    if frame is None or frame.empty:
        return {}, None
    price_map = {}
    for symbol in symbols:
        if symbol not in frame.columns:
            continue
        series = pd.to_numeric(frame[symbol], errors="coerce").dropna()
        if not series.empty and float(series.iloc[-1]) > 0:
            price_map[symbol] = float(series.iloc[-1])
    reference = frame["^NSEI"].dropna() if "^NSEI" in frame.columns else pd.Series(dtype=float)
    if not reference.empty:
        signal_date = reference.index[-1].date().isoformat()
    else:
        any_dates = frame.index[frame.notna().any(axis=1)]
        signal_date = any_dates[-1].date().isoformat() if len(any_dates) else None
    return price_map, signal_date


def _send_strategy_update_email(
    strategy_name,
    strategy,
    event_type,
    target_symbols=None,
    current_positions=None,
    before_positions=None,
    entered_symbols=None,
    exited_symbols=None,
    retained_symbols=None,
    price_by_symbol=None,
    signal_date=None,
    note=None,
):
    """Send one configured strategy update by SMTP; never submits broker orders."""
    toggle_by_event = {
        "SCAN": "email_on_scan",
        "REBALANCE_DATE": "email_on_rebalance_date",
        "REBALANCE_EXECUTED": "email_on_rebalance_execution",
    }
    toggle_name = toggle_by_event.get(event_type)
    if toggle_name and not bool(strategy.get(toggle_name, True)):
        return {"status": "skipped", "message": f"Email notification is disabled for {event_type.lower().replace('_', ' ')}."}

    recipient = str(strategy.get("notification_email", "") or "").strip()
    if not recipient:
        return {"status": "skipped", "message": "Add a notification email address in this strategy's settings to receive updates."}
    if not eod_reporting.is_valid_email_address(recipient):
        return {"status": "error", "message": "The strategy notification email address is not valid. Check the address in strategy settings."}

    smtp = _smtp_configuration()
    if not (smtp["smtp_host"] and smtp["sender"]):
        return {
            "status": "error",
            "message": "Recipient saved, but email delivery is not configured. Add SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and SMTP_FROM in Streamlit Secrets.",
        }

    current_positions = list(current_positions or [])
    before_positions = list(before_positions if before_positions is not None else current_positions)
    symbols = list(target_symbols or [])
    symbols.extend(
        str(position.get("Symbol", "")).strip()
        for position in current_positions + before_positions
        if isinstance(position, dict)
    )
    symbols.extend(str(symbol).strip() for symbol in (exited_symbols or []))
    symbols = list(dict.fromkeys(symbol for symbol in symbols if symbol))

    if price_by_symbol is None:
        price_by_symbol, detected_date = _latest_saved_price_map(symbols)
        if signal_date is None:
            signal_date = detected_date
    else:
        price_by_symbol = dict(price_by_symbol)
    body = eod_reporting.build_strategy_update_report(
        strategy_name=strategy_name,
        strategy_config=strategy,
        event_type=event_type,
        target_symbols=target_symbols,
        current_positions=current_positions,
        before_positions=before_positions,
        price_by_symbol=price_by_symbol,
        entered_symbols=entered_symbols,
        exited_symbols=exited_symbols,
        retained_symbols=retained_symbols,
        signal_date=signal_date,
        note=note,
    )
    try:
        smtp_port = int(smtp["smtp_port"] or 587)
    except (TypeError, ValueError):
        smtp_port = 587
    ok, message = eod_reporting.send_email_alert(
        subject=f"NSE Momentum — {strategy_name} — {event_type.replace('_', ' ').title()}",
        body=body,
        smtp_host=smtp["smtp_host"],
        smtp_port=smtp_port,
        username=smtp["username"],
        password=smtp["password"],
        sender=smtp["sender"],
        recipient=recipient,
        use_tls=True,
    )
    return {"status": "sent" if ok else "error", "message": message}


def _maybe_send_rebalance_date_emails():
    """Send one report per due strategy/cadence while the Streamlit app is active."""
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    # Daily reports should use after-close data. This is an in-app trigger, not a
    # server-side scheduler; the app must be visited after 4 p.m. IST on a weekday.
    if now.weekday() >= 5 or now.hour < 16:
        return
    for strategy_name, strategy in st.session_state.get("strategies", {}).items():
        if strategy.get("status", "Active") != "Active":
            continue
        if not strategy.get("notification_email") or not strategy.get("email_on_rebalance_date", True):
            continue
        cycle_key = eod_reporting.rebalance_notification_cycle(
            strategy.get("rebalance_freq", "Monthly"),
            strategy.get("rebalance_day", 1),
            now=now.to_pydatetime(),
        )
        if not cycle_key or strategy.get("last_rebalance_email_cycle") == cycle_key:
            continue
        smtp = _smtp_configuration()
        if not (smtp["smtp_host"] and smtp["sender"]):
            continue
        attempt_key = f"{strategy_name}|{cycle_key}"
        attempted = st.session_state.get("_rebalance_email_attempts", set())
        if attempt_key in attempted:
            continue
        attempted = set(attempted)
        attempted.add(attempt_key)
        st.session_state["_rebalance_email_attempts"] = attempted

        try:
            current_symbols = [
                position.get("Symbol")
                for position in strategy.get("positions", [])
                if isinstance(position, dict) and position.get("Symbol")
            ]
            targets = run_strategy_stock_scanner(
                strategy,
                current_holdings=current_symbols,
                as_of_date=now.tz_localize(None),
            )
            outcome = _send_strategy_update_email(
                strategy_name=strategy_name,
                strategy=strategy,
                event_type="REBALANCE_DATE",
                target_symbols=targets,
                current_positions=strategy.get("positions", []),
                before_positions=strategy.get("positions", []),
                note=(
                    f"Scheduled rebalance-cycle email ({cycle_key}). This is a review/reminder only; "
                    "it does not execute the strategy rebalance or place orders."
                ),
            )
            if outcome["status"] == "sent":
                strategy["last_rebalance_email_cycle"] = cycle_key
            st.session_state["_scheduled_email_notice"] = {
                "strategy": strategy_name,
                **outcome,
            }
        except Exception as exc:
            st.session_state["_scheduled_email_notice"] = {
                "strategy": strategy_name,
                "status": "error",
                "message": f"Could not generate scheduled email: {type(exc).__name__}.",
            }


# Attempt after-close scheduled strategy emails once per due cadence while the app
# session is active; no live orders are submitted by this process.
_maybe_send_rebalance_date_emails()

# ==========================================
# NAVIGATION HEADER
# ==========================================
try:
    with open("assets/happy_trade_logo.png", "rb") as _logo_file:
        _happy_trade_logo_png = base64.b64encode(_logo_file.read()).decode("ascii")
    st.markdown(
        f"""
        <div class="ht-brandbar">
            <div class="ht-brand-mark"><img src="data:image/png;base64,{_happy_trade_logo_png}" alt="The Happy Trade logo" /></div>
            <div>
                <div class="ht-brand-eyebrow">Your trading friend</div>
                <div class="ht-brand-title">Happy <span>Trade</span></div>
                <div class="ht-brand-subtitle">Research smarter. Trade with a plan. Invest with discipline.</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
except (OSError, UnicodeDecodeError):
    st.markdown(
        '<div class="ht-brandbar"><div><div class="ht-brand-title">Happy <span>Trade</span></div>'
        '<div class="ht-brand-subtitle">Research smarter. Trade with a plan. Invest with discipline.</div></div></div>',
        unsafe_allow_html=True,
    )

nav_col1, nav_col2, nav_col3, nav_col4, nav_col5, nav_col6 = st.columns([1.0, 1.45, 1.15, 1.25, 1.1, 1.25])
with nav_col1:
    if st.button("💻 DASHBOARD", use_container_width=True, type="primary" if st.session_state.navigation_tab == "DASHBOARD" else "secondary"):
        st.session_state.navigation_tab = "DASHBOARD"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col2:
    if st.button("➕ INVESTING STRATEGY", use_container_width=True, type="primary" if st.session_state.navigation_tab == "STRATEGY_BUILDER" else "secondary"):
        st.session_state.navigation_tab = "STRATEGY_BUILDER"
        st.session_state.editing_strategy_name = None
        st.rerun()

with nav_col3:
    if st.button("📈 BACKTEST ENGINE", use_container_width=True, type="primary" if st.session_state.navigation_tab == "BACKTEST" else "secondary"):
        st.session_state.navigation_tab = "BACKTEST"
        st.rerun()

with nav_col4:
    if st.button("🧾 INDEX CONSTITUENTS", use_container_width=True, type="primary" if st.session_state.navigation_tab == "INDEX_CONSTITUENTS" else "secondary"):
        st.session_state.navigation_tab = "INDEX_CONSTITUENTS"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col5:
    if st.button("📊 PRICE CHARTS", use_container_width=True, type="primary" if st.session_state.navigation_tab == "PRICE_CHARTS" else "secondary"):
        st.session_state.navigation_tab = "PRICE_CHARTS"
        st.session_state.active_strategy_view = None
        st.rerun()

with nav_col6:
    if st.button("🛡️ DATA & EOD", use_container_width=True, type="primary" if st.session_state.navigation_tab == "DATA_REPORTS" else "secondary"):
        st.session_state.navigation_tab = "DATA_REPORTS"
        st.session_state.active_strategy_view = None
        st.rerun()

st.markdown("---")

scheduled_email_notice = st.session_state.get("_scheduled_email_notice")
if scheduled_email_notice:
    scheduled_email_status = scheduled_email_notice.get("status")
    scheduled_email_text = (
        f"Scheduled strategy email for '{scheduled_email_notice.get('strategy', 'strategy')}': "
        f"{scheduled_email_notice.get('message', '')}"
    )
    if scheduled_email_status == "sent":
        st.success(scheduled_email_text)
    elif scheduled_email_status == "error":
        st.warning(scheduled_email_text)

render_fyers_connection_panel()

with st.expander("📚 FYERS Historical Data Status", expanded=False):
    history_summary = fyers_history_data.get_data_summary()
    if history_summary["symbols"]:
        hist_cols = st.columns(4)
        hist_cols[0].metric("Symbols with History", f'{history_summary["symbols"]:,}')
        hist_cols[1].metric("Daily Candles Stored", f'{history_summary["rows"]:,}')
        hist_cols[2].metric("Oldest Stored Date", history_summary["oldest_date"] or "—")
        hist_cols[3].metric("Backfills Pending", f'{history_summary["backfill_pending"]:,}')
        st.caption(
            f'FYERS source coverage through {history_summary["latest_date"] or "—"}. '
            "Backtests now read this stored dataset only; the updater resumes old-history downloads "
            "in later runs until the oldest available FYERS data is reached."
        )
    else:
        st.warning(
            "No FYERS history files are available in this deployment yet. Connect to FYERS from this app "
            "and configure GITHUB_ACTIONS_TOKEN in Streamlit Secrets once so the login-triggered workflow "
            "can download and commit the historical files."
        )
    st.markdown("#### Index History Coverage Audit")
    index_coverage = index_history_data.get_index_history_coverage()
    st.dataframe(
        index_coverage[
            ["Index", "Launch Date", "Target Start", "First Stored Date", "Last Stored Date",
             "Candles", "Internal Gaps (>7d)", "Missing Sessions vs Nifty 50",
             "Invalid OHLC Rows", "Close Moves >15%", "Launch Covered", "Coverage Status"]
        ],
        use_container_width=True,
        hide_index=True,
    )
    if index_coverage.empty or (index_coverage["Coverage Status"] == "MISSING").any():
        st.warning(
            "One or more required index history files are missing. The app cannot claim that index data "
            "covers the full target history until those files are downloaded."
        )
    elif (~index_coverage["Start Covered"]).any():
        st.warning(
            "One or more index histories start after their configured earliest target. FYERS Standard API "
            "history is limited to the last 5 years; full inception backfills need older history access, "
            "and API availability must be verified per index."
        )
    st.caption(
        "Launch dates are shown separately from base/history target dates. Some index series have a calculated "
        "base-date history before formal launch; current official factsheets do not publish a launch date for "
        "every series. The audit checks date coverage, long gaps, OHLC validity and unusually large close changes, "
        "but does not reconcile every value against the official exchange record. Index level history also differs "
        "from total-return history."
    )
    st.link_button(
        "Open FYERS Historical Data Workflow",
        "https://github.com/nishantbhandari09/nse-momentum-portfolio/actions/workflows/update-index-history.yml",
        use_container_width=True,
    )

with st.expander("💾 Backups & Downloads"):
    bkp_col1, bkp_col2 = st.columns(2)
    with bkp_col1:
        export_json = json.dumps(st.session_state.strategies, indent=2, default=str)
        st.download_button("📥 Download strategies backup (JSON)", export_json,
                            "strategies_backup.json", "application/json")
        uploaded = st.file_uploader("📤 Restore strategies from a backup file", type="json")
        if uploaded:
            try:
                restored = json.load(uploaded)
                st.session_state.strategies.update(restored)
                st.success(f"Restored {len(restored)} strategy(ies). Switch pages to see them.")
            except Exception as e:
                st.error(f"Couldn't read that file: {e}")
    with bkp_col2:
        history_summary = fyers_history_data.get_data_summary()
        if history_summary["symbols"]:
            st.markdown("**FYERS historical price database**")
            st.caption(
                f'{history_summary["symbols"]:,} symbols with data; '
                f'{history_summary["rows"]:,} daily candles; '
                f'coverage {history_summary["oldest_date"] or "—"} to '
                f'{history_summary["latest_date"] or "—"}.'
            )
            if fyers_history_data.MANIFEST_PATH.exists():
                st.download_button(
                    "📥 Download FYERS history coverage manifest",
                    fyers_history_data.MANIFEST_PATH.read_bytes(),
                    "fyers_history_manifest.csv",
                    "text/csv",
                )
        else:
            st.warning(
                "The FYERS historical OHLCV archive has not been downloaded to this deployment yet. "
                "The scanner and backtester will not substitute Yahoo Finance prices. Configure the "
                "connect to FYERS and configure GITHUB_ACTIONS_TOKEN in Streamlit Secrets once."
            )

with st.expander("📦 NSE All-Stock Historical Archive Status", expanded=False):
    st.markdown("**Published historical archive used by backtests**")
    nse_release_status = nse_archive_data.get_archive_status()
    if nse_release_status.get("available"):
        release_cols = st.columns(4)
        release_cols[0].metric("Years Published", f'{nse_release_status["years_published"]:,}')
        release_cols[1].metric("Years Cached Here", f'{nse_release_status["years_downloaded_locally"]:,}')
        release_cols[2].metric("Symbols", f'{nse_release_status["total_symbols"] or 0:,}')
        release_cols[3].metric("Historical Rows", f'{nse_release_status["total_rows"] or 0:,}')
        st.success(
            f'Permanent GitHub Release "{nse_release_status["release_tag"]}" is available: '
            f'{nse_release_status["first_date"] or "unknown"} to {nse_release_status["latest_date"] or "unknown"}.'
        )
        st.caption(
            f'Last archive build: {nse_release_status["generated_at"] or "unknown"}. '
            "The app downloads only the calendar years needed by a backtest and reuses verified local files. "
            "FYERS prices take precedence wherever both sources have data."
        )
    else:
        st.warning(
            "The permanent NSE data Release is not available yet, so the app cannot download its historical "
            "archive. Run the all-stock historical workflow and wait for a successful green check."
        )
        if nse_release_status.get("error"):
            st.caption(f'Data status detail: {nse_release_status["error"]}')
    status_col1, status_col2 = st.columns(2)
    if status_col1.button("🔄 Refresh archive status", key="refresh_nse_release_status", use_container_width=True):
        nse_release_status = nse_archive_data.get_archive_status(refresh=True)
        st.rerun()
    status_col2.link_button(
        "Open history workflow",
        "https://github.com/nishantbhandari09/nse-momentum-portfolio/actions/workflows/update-nse-all-stock-history.yml",
        use_container_width=True,
    )

    st.divider()
    st.markdown("**Local full-OHLCV archive for volume/traded-value charts**")
    if nse_history_data.available():
        nse_symbols_count = len(nse_history_data.list_symbols())
        nse_partitions = len(nse_history_data._partition_paths(False))
        st.success(
            f"Local NSE raw archive is available: {nse_symbols_count:,} symbols, "
            f"{nse_partitions} yearly partition(s)."
        )
    else:
        st.info(
            "The full-OHLCV yearly partitions are separate from the compact backtest archive. "
            "They may not be mounted into Streamlit Cloud; the backtest uses the permanent Release assets "
            "above, while volume/traded-value charts need the raw partitions locally."
        )

# ==========================================
# PAGE 1: PORTFOLIO DASHBOARD & DRILL-DOWN
# ==========================================
if st.session_state.navigation_tab == "DASHBOARD":

    if st.session_state.active_strategy_view is not None:
        strat_name = st.session_state.active_strategy_view
        strat = st.session_state.strategies.get(strat_name)

        if not strat:
            st.session_state.active_strategy_view = None
            st.rerun()

        metrics = calculate_strategy_metrics(strat)
        days_left = calculate_days_to_rebalance(strat.get("rebalance_day", 1))

        top_col1, top_col2, top_col3 = st.columns([2, 5, 3])
        with top_col1:
            if st.button("❮ Back to Dashboard"):
                st.session_state.active_strategy_view = None
                st.rerun()
        with top_col3:
            current_status = strat["status"]
            btn_label = "⏸️ Pause Strategy" if current_status == "Active" else "▶️ Start Strategy"
            if st.button(btn_label):
                strat["status"] = "Paused" if current_status == "Active" else "Active"
                st.rerun()

        st.subheader(f"Strategy: {strat_name}")
        status_class = "status-active" if strat["status"] == "Active" else "status-paused"
        st.markdown(f"Status: <span class='{status_class}'>{strat['status']}</span> | **Groups:** {', '.join(strat.get('groups', ['Nifty 500']))} | **Multiplier:** {strat.get('allocation_multiplier', 1.0)}x", unsafe_allow_html=True)

        fyers_status = st.session_state.get("_fyers_status")
        if fyers_status == "live":
            st.caption("🟢 Prices below are live from Fyers where available.")
        elif fyers_status:
            st.caption(f"🟡 Fyers live prices {fyers_status} — showing cached closes instead.")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Allocated Capital", f"₹{metrics['allocated']:,.2f}")
        m2.metric("Uninvested Cash", f"₹{metrics['cash_reserve']:,.2f}")
        m3.metric("Unrealized P&L", f"₹{metrics['unrealized_pnl']:,.2f}")
        m4.metric("Total P&L & Returns", f"₹{metrics['total_pnl']:,.2f}", delta=f"{metrics['returns_pct']:.2f}%")

        st.markdown("---")
        st.subheader("🔍 Live Stock Scanner & Rebalance Engine")

        scan_col1, scan_col2 = st.columns([2, 3])
        with scan_col1:
            run_scan = st.button("🚀 Run Live Stock Scan for this Strategy", type="primary", use_container_width=True)

        if run_scan:
            scan_diagnostics = {}
            with st.spinner("Executing strategy parameters against selected asset groups..."):
                current_symbols = [p["Symbol"] for p in strat.get("positions", [])]
                try:
                    scanned_tickers = run_strategy_stock_scanner(
                        strat,
                        current_holdings=current_symbols,
                        as_of_date=pd.Timestamp.today(),
                        diagnostics=scan_diagnostics,
                    )
                except Exception as exc:
                    scanned_tickers = []
                    scan_diagnostics.update({
                        "stage": "scan error",
                        "reason": f"{type(exc).__name__}: {exc}",
                        "groups": list(strat.get("groups", ["Nifty 500"])),
                    })
                st.session_state[f"scanned_results_{strat_name}"] = scanned_tickers
                st.session_state[f"scan_diagnostics_{strat_name}"] = scan_diagnostics
                st.session_state[f"strategy_email_notice_{strat_name}"] = _send_strategy_update_email(
                    strategy_name=strat_name,
                    strategy=strat,
                    event_type="SCAN",
                    target_symbols=scanned_tickers,
                    current_positions=strat.get("positions", []),
                    before_positions=strat.get("positions", []),
                    note="Triggered by manually running the live stock scanner. Signals do not place orders.",
                )

        email_notice = st.session_state.get(f"strategy_email_notice_{strat_name}")
        if email_notice:
            if email_notice.get("status") == "sent":
                st.success(f"Email update: {email_notice.get('message', 'Sent')}")
            elif email_notice.get("status") == "error":
                st.warning(f"Email update: {email_notice.get('message', 'Delivery failed')}")
            elif email_notice.get("status") == "skipped" and st.session_state.get(f"scanned_results_{strat_name}") is not None:
                st.info(f"Email update not sent: {email_notice.get('message', 'Not configured')}")

        scanned_results = st.session_state.get(f"scanned_results_{strat_name}", None)

        if scanned_results is not None:
            st.success(f"Scan Complete! Found **{len(scanned_results)}** target portfolio stocks across selected groups based on strategy rules.")
            if not scanned_results:
                scan_diagnostics = st.session_state.get(f"scan_diagnostics_{strat_name}", {})
                with st.expander("Why did the scan return no stocks?", expanded=True):
                    st.warning(scan_diagnostics.get(
                        "reason",
                        "No symbols matched the strategy. Check the diagnostics and selected filters below.",
                    ))
                    diag_cols = st.columns(4)
                    diag_cols[0].metric("Universe symbols", f'{scan_diagnostics.get("universe_tickers", 0):,}')
                    diag_cols[1].metric("Symbols with prices", f'{scan_diagnostics.get("universe_symbols_with_prices", 0):,}')
                    diag_cols[2].metric("Price rows", f'{scan_diagnostics.get("price_rows", 0):,}')
                    diag_cols[3].metric("Price columns", f'{scan_diagnostics.get("price_columns", 0):,}')
                    st.caption(
                        f"Stopped at: {scan_diagnostics.get('stage', 'unknown')} | "
                        f"Price dates: {scan_diagnostics.get('first_price_date') or '—'} to "
                        f"{scan_diagnostics.get('last_price_date') or '—'}"
                    )
                    counts = scan_diagnostics.get("filter_counts", {})
                    if counts:
                        st.dataframe(
                            pd.DataFrame([{"Filter stage": name, "Symbols remaining": count} for name, count in counts.items()]),
                            use_container_width=True,
                            hide_index=True,
                        )
                    for note in scan_diagnostics.get("notes", []):
                        st.info(note)
                    missing = scan_diagnostics.get("missing_benchmarks", [])
                    if missing:
                        st.error("Missing benchmark history: " + ", ".join(missing))

            sc_col1, sc_col2 = st.columns(2)
            with sc_col1:
                st.markdown("#### 📌 Current Holding Stocks")
                if metrics["positions_data"]:
                    st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
                else:
                    st.info("No active holdings currently allocated.")

            with sc_col2:
                st.markdown("#### 🔄 Updated / Scanned Portfolio Stocks")
                prices_df = fetch_market_data(scanned_results, period="5d")
                live_or_close = apply_live_price_overlay(prices_df, scanned_results)
                held_symbols = {p["Symbol"] for p in strat.get("positions", [])}
                scanned_rows = []
                for ticker in scanned_results:
                    cmp = live_or_close.get(ticker, 0.0)
                    scanned_rows.append({
                        "Symbol": ticker.replace(".NS", ""),
                        "CMP": f"₹{cmp:,.2f}",
                        "Status": "Held (in buffer)" if ticker in held_symbols else "Entry Target"
                    })
                st.dataframe(pd.DataFrame(scanned_rows), use_container_width=True)

            if st.button("⚡ Execute Rebalance & Reinvest Capital", type="primary", use_container_width=True):
                existing_positions = {p["Symbol"]: p for p in strat.get("positions", [])}
                retained_symbols = [s for s in scanned_results if s in existing_positions]
                new_symbols = [s for s in scanned_results if s not in existing_positions]
                exited_symbols = [s for s in existing_positions if s not in scanned_results]

                price_lookup_df = fetch_market_data(scanned_results + exited_symbols, period="5d")
                live_or_close = apply_live_price_overlay(price_lookup_df, scanned_results + exited_symbols)

                freed_cash = 0.0
                newly_realized = 0.0
                for sym in exited_symbols:
                    pos = existing_positions[sym]
                    exit_price = live_or_close.get(sym, pos["Buy Price"])
                    proceeds = exit_price * pos["Buy Qty"]
                    freed_cash += proceeds
                    newly_realized += proceeds - (pos["Buy Price"] * pos["Buy Qty"])

                new_positions = [existing_positions[s] for s in retained_symbols]

                investable_for_new = (metrics["cash_reserve"] + freed_cash) * strat.get("allocation_multiplier", 1.0)
                per_new_alloc = investable_for_new / len(new_symbols) if new_symbols else 0.0

                for ticker in new_symbols:
                    cmp = live_or_close.get(ticker)
                    if not cmp or cmp <= 0:
                        continue
                    qty = int(per_new_alloc // cmp)
                    if qty > 0:
                        new_positions.append({
                            "Symbol": ticker,
                            "Buy Qty": qty,
                            "Buy Price": cmp,
                            "Entry Date": datetime.now().strftime("%Y-%m-%d")
                        })

                strat["positions"] = new_positions
                strat["realized_pnl"] = strat.get("realized_pnl", 0.0) + newly_realized
                st.session_state[f"strategy_email_notice_{strat_name}"] = _send_strategy_update_email(
                    strategy_name=strat_name,
                    strategy=strat,
                    event_type="REBALANCE_EXECUTED",
                    target_symbols=scanned_results,
                    current_positions=new_positions,
                    before_positions=list(existing_positions.values()),
                    entered_symbols=[
                        position["Symbol"] for position in new_positions
                        if position.get("Symbol") not in existing_positions
                    ],
                    exited_symbols=exited_symbols,
                    retained_symbols=retained_symbols,
                    price_by_symbol=live_or_close,
                    signal_date=datetime.now().strftime("%Y-%m-%d"),
                    note=(
                        f"Portfolio rebalance was executed in the app. Realized P&L booked for exits during this "
                        f"rebalance: ₹{newly_realized:,.2f}. This app update does not submit broker orders."
                    ),
                )
                st.session_state[f"scanned_results_{strat_name}"] = None
                st.success(f"Rebalanced '{strat_name}': kept {len(retained_symbols)} in the buffer, exited {len(exited_symbols)}, entered {len(new_symbols)} new.")
                st.rerun()

        else:
            st.markdown("#### 📋 Current Portfolio Holdings")
            if metrics["positions_data"]:
                st.dataframe(pd.DataFrame(metrics["positions_data"]), use_container_width=True)
            else:
                st.info("No active scanned holdings for this strategy. Click 'Run Live Stock Scan' above.")

    else:
        st.title("MY PORTFOLIO")
        save_notice = st.session_state.pop("strategy_save_notice", None)
        if save_notice:
            st.success(save_notice)

        tot_allocated = 0.0
        tot_holding_val = 0.0
        tot_realized_pnl = 0.0
        tot_unrealized_pnl = 0.0

        for name, strat in st.session_state.strategies.items():
            m = calculate_strategy_metrics(strat)
            tot_allocated += m["allocated"]
            tot_holding_val += m["current_holding_val"]
            tot_realized_pnl += m["realized_pnl"]
            tot_unrealized_pnl += m["unrealized_pnl"]

        tot_pnl = tot_realized_pnl + tot_unrealized_pnl
        tot_returns_pct = (tot_pnl / tot_allocated * 100) if tot_allocated > 0 else 0.0
        tot_portfolio_val = tot_allocated + tot_pnl

        kpi1, kpi2, kpi3 = st.columns(3)
        kpi1.metric("Total Allocated Capital", f"₹{tot_allocated:,.2f}")
        kpi2.metric("Total Realized P&L", f"₹{tot_realized_pnl:,.2f}")
        kpi3.metric("Total Portfolio P&L", f"₹{tot_pnl:,.2f}", delta=f"{tot_returns_pct:.2f}%")

        kpi4, kpi5, kpi6 = st.columns(3)
        kpi4.metric("Current Holdings Value", f"₹{tot_holding_val:,.2f}")
        kpi5.metric("Total Unrealized P&L", f"₹{tot_unrealized_pnl:,.2f}")
        kpi6.metric("Total Portfolio Balance", f"₹{tot_portfolio_val:,.2f}")

        st.markdown("---")
        real_names = [
            name for name, strat in st.session_state.strategies.items()
            if str(strat.get("type", "Virtual")).strip().lower() == "real"
        ]
        virtual_names = [
            name for name, strat in st.session_state.strategies.items()
            if str(strat.get("type", "Virtual")).strip().lower() != "real"
        ]

        def render_strategy_cards(keys, empty_message):
            if not keys:
                st.info(empty_message)
                return

            strat_cols = st.columns(3)
            for i, name in enumerate(keys):
                strat = st.session_state.strategies[name]
                sm = calculate_strategy_metrics(strat)
                days_left = calculate_days_to_rebalance(strat.get("rebalance_day", 1))
                col = strat_cols[i % 3]

                with col:
                    with st.container(border=True):
                        c_head1, c_head2 = st.columns([3, 2])
                        with c_head1:
                            st.markdown(f"### **{name}**")
                            status_class = "status-active" if strat["status"] == "Active" else "status-paused"
                            st.markdown(f"<span class='{status_class}'>{strat['status']}</span>", unsafe_allow_html=True)

                        with c_head2:
                            act_col1, act_col2, act_col3 = st.columns(3)
                            is_active = strat["status"] == "Active"

                            if act_col1.button("⏸️" if is_active else "▶️", key=f"toggle_{name}", help="Start/Stop Strategy"):
                                strat["status"] = "Paused" if is_active else "Active"
                                st.rerun()

                            if act_col2.button("✏️", key=f"edit_{name}", help="Edit Strategy Config"):
                                st.session_state.editing_strategy_name = name
                                st.session_state.navigation_tab = "STRATEGY_BUILDER"
                                st.rerun()

                            if act_col3.button("🗑️", key=f"del_{name}", help="Delete Strategy"):
                                del st.session_state.strategies[name]
                                st.rerun()

                        st.caption(f"Groups: {', '.join(strat.get('groups', ['Nifty 500']))} | Multiplier: {strat.get('allocation_multiplier', 1.0)}x")
                        st.caption(f"Rebalance Date: Day {strat.get('rebalance_day', 1)} | {days_left} days left")
                        st.markdown(f"**Allocated:** ₹{sm['allocated']:,.2f}")
                        st.markdown(f"**Realized P&L:** ₹{sm['realized_pnl']:,.2f}")
                        st.markdown(f"**Unrealized P&L:** ₹{sm['unrealized_pnl']:,.2f}")

                        pnl_color = "green" if sm['total_pnl'] >= 0 else "red"
                        st.markdown(f"**P&L / Returns:** <span style='color:{pnl_color}; font-weight:bold;'>₹{sm['total_pnl']:,.2f} ({sm['returns_pct']:.2f}%)</span>", unsafe_allow_html=True)

                        if st.button("🔍 Open Scanner & Details", key=f"view_detail_{name}", use_container_width=True):
                            st.session_state.active_strategy_view = name
                            st.rerun()

        real_tab, virtual_tab = st.tabs(["💹 Real Strategies", "🧪 Virtual Strategies"])
        with real_tab:
            st.caption("Strategies categorized as Real. This section is separated from virtual/paper strategies.")
            render_strategy_cards(real_names, "No real strategies yet. Create a strategy and set its type to Real.")

        with virtual_tab:
            st.caption("Virtual strategies are for simulated portfolio tracking and do not place broker orders.")
            render_strategy_cards(virtual_names, "No virtual strategies yet. Create a strategy and set its type to Virtual.")

# ==========================================
# PAGE 2: STRATEGY BUILDER & CONFIG
# ==========================================
elif st.session_state.navigation_tab == "STRATEGY_BUILDER":
    edit_mode = st.session_state.editing_strategy_name is not None
    edit_strat = st.session_state.strategies.get(st.session_state.editing_strategy_name, {}) if edit_mode else {}

    st.subheader("✏️ EDIT STRATEGY ENGINE" if edit_mode else "➕ CREATE STRATEGY ENGINE")

    st.markdown("#### Investment & Capital Details")

    inv_r1, inv_r2, inv_r3 = st.columns(3)
    with inv_r1:
        st_type = st.radio("Strategy Type :", ["Real", "Virtual"], index=0 if edit_strat.get("type") == "Real" else 1, horizontal=True)
        default_name = st.session_state.editing_strategy_name if edit_mode else "New Momentum Strategy"
        strat_name_input = st.text_input("Strategy Name :", value=default_name, disabled=edit_mode)
    with inv_r2:
        tot_alloc = st.number_input("Total Allocation Capital (₹) :", value=float(edit_strat.get("allocated", 100000.0)), step=10000.0)
        rebal_freq = st.selectbox("Rebalance Frequency :", ["Monthly", "Weekly", "Quarterly"])
    with inv_r3:
        rebal_day = st.number_input("Rebalance Day of Month (1-28) :", value=int(edit_strat.get("rebalance_day", 1)), min_value=1, max_value=28)
        alloc_multiplier = st.number_input("Allocation Multiplier (X times) :", value=float(edit_strat.get("allocation_multiplier", 1.0)), min_value=0.1, max_value=10.0, step=0.1)

    st.markdown("---")
    st.markdown("#### Asset Universe Selection (Groups Checkboxes)")

    existing_groups = edit_strat.get("groups", ["Nifty 500"])
    grp_c1, grp_c2, grp_c3 = st.columns(3)
    with grp_c1:
        grp_nifty500 = st.checkbox("Nifty 500 Universe", value=("Nifty 500" in existing_groups))
        grp_nifty200 = st.checkbox("Nifty 200 Universe", value=("Nifty 200" in existing_groups))
        grp_all_nse_stocks = st.checkbox(
            "All NSE Stocks (current + historical)",
            value=("All NSE Stocks" in existing_groups),
            help=(
                "Uses the permanent NSE archive's full security universe, including securities that have since "
                "been delisted. This broad universe is useful for research and reduces reliance on today's "
                "Nifty 500 constituents. Some securities may have limited trading history."
            ),
        )
    with grp_c2:
        grp_all_etfs = st.checkbox("All ETFs Universe", value=("All ETFs" in existing_groups))
        grp_gold_etf = st.checkbox("Gold ETF Only", value=("Gold ETF" in existing_groups))
    with grp_c3:
        grp_liquidbees = st.checkbox("LiquidBEES Only", value=("LiquidBEES" in existing_groups))
        grp_gov_bond = st.checkbox("Gov Bond Only", value=("Gov Bond" in existing_groups))

    etf_universe = {"etfs": {}}
    try:
        etf_universe = fetch_etf_and_index_universe()
    except Exception:
        pass
    etf_categories = sorted(etf_universe["etfs"].keys())
    selected_etf_categories = []
    if etf_categories:
        total_etfs = sum(len(v) for v in etf_universe["etfs"].values())
        st.caption(f"Full ETF universe ({total_etfs} found via your broker) — add any categories:")
        selected_etf_categories = st.multiselect(
            "Additional ETF categories", etf_categories,
            default=[c for c in etf_categories if c in existing_groups],
        )
    else:
        st.caption("Full ETF universe not available yet (needs a working broker connection) — the checkboxes above still work.")

    st.markdown("#### ETF Groups from Your Uploaded Lists")
    st.caption(
        "These same curated groups are available in Index Constituents and can be used "
        "for strategy scanning, portfolio execution and backtesting."
    )
    selected_uploaded_etf_groups = st.multiselect(
        "Uploaded ETF groups",
        options=ETF_GROUP_NAMES,
        default=[g for g in ETF_GROUP_NAMES if g in existing_groups],
        key=f"strategy_uploaded_etf_groups_{st.session_state.editing_strategy_name or 'new'}",
    )

    st.markdown("---")
    st.markdown("#### Ranking, Entry & Exit Parameters")

    rank_c1, rank_c2 = st.columns(2)
    with rank_c1:
        entry_rank = st.number_input("Entry Rank Stocks (Top N Target) :", value=int(edit_strat.get("entry_rank", 10)), min_value=1, max_value=50)
    with rank_c2:
        exit_rank = st.number_input("Exit Rank Threshold :", value=int(edit_strat.get("exit_rank", 20)), min_value=1, max_value=100, help="A held stock is only sold once it ranks worse than this.")

    st.markdown("---")
    st.markdown("#### Technical Features & Indicator Rules")

    sd_r1, sd_r2, sd_r3 = st.columns(3)
    with sd_r1:
        st.caption("Lookback Periods (Days) :")
        existing_periods = edit_strat.get("period_days", [252, 120, 60])
        p252 = st.checkbox("252 Days (1 Year)", value=(252 in existing_periods))
        p120 = st.checkbox("120 Days (6 Months)", value=(120 in existing_periods))
        p90 = st.checkbox("90 Days (3 Months)", value=(90 in existing_periods))
        p60 = st.checkbox("60 Days (2 Months)", value=(60 in existing_periods))

    with sd_r2:
        st.caption("Proximity Filters (% from Period High/Low) :")
        pct_high = st.number_input("% Within Period High :", value=float(edit_strat.get("pct_from_high", 15.0)))
        pct_low = st.number_input("% Above Period Low :", value=float(edit_strat.get("pct_from_low", 0.0)))
        min_price = st.number_input("Minimum Price (₹) :", value=float(edit_strat.get("min_price", 10.0)), min_value=0.0)

    with sd_r3:
        ma_options = ["None", "200 EMA", "100 EMA", "200 SMA", "50 SMA"]
        ma_val = edit_strat.get("moving_average", "200 EMA")
        ma_idx = ma_options.index(ma_val) if ma_val in ma_options else 1
        selected_ma = st.selectbox("Moving Average Filter :", ma_options, index=ma_idx)

        use_rs = st.checkbox("Enable Relative Strength Ratio Filter", value=edit_strat.get("use_rs", True))
        rs_benchmark = st.selectbox("RS Benchmark Ratio :", ["Nifty 500 / G-Sec", "Nifty 50 / Liquid ETF"])

    st.markdown("#### Optional Relative Momentum Filter")
    use_relative_momentum = st.checkbox(
        "Enable Relative Momentum Filter",
        value=bool(edit_strat.get("use_relative_momentum_filter", False)),
        help="Compare each selected stock's price/index ratio against its own moving average. Only stocks with RS above the selected average remain eligible."
    )
    relative_index_options = list(MAJOR_MARKET_INDICES.keys())
    relative_index_saved = edit_strat.get("relative_momentum_index", "Nifty 50")
    relative_momentum_index = st.selectbox(
        "Relative Momentum Benchmark Index (Denominator)",
        relative_index_options,
        index=relative_index_options.index(relative_index_saved) if relative_index_saved in relative_index_options else 0,
        disabled=not use_relative_momentum,
        key="relative_momentum_index_select",
    )
    relative_ma_options = ["SMA", "EMA"]
    relative_ma_saved = edit_strat.get("relative_momentum_ma_type", "SMA")
    relative_momentum_ma_type = st.selectbox(
        "Relative Strength Indicator",
        relative_ma_options,
        index=relative_ma_options.index(relative_ma_saved) if relative_ma_saved in relative_ma_options else 0,
        disabled=not use_relative_momentum,
        key="relative_momentum_ma_type_select",
    )
    relative_momentum_ma_period = st.number_input(
        "Relative Strength MA Period (Days)",
        min_value=2,
        max_value=500,
        value=int(edit_strat.get("relative_momentum_ma_period", 200)),
        step=1,
        disabled=not use_relative_momentum,
        key="relative_momentum_ma_period_input",
    )

    st.markdown("#### Optional Market Trend Filter")
    use_market_trend = st.checkbox(
        "Enable Market Trend Filter",
        value=bool(edit_strat.get("use_market_trend_filter", False)),
        help="Use a major index regime to control whether the strategy opens positions."
    )
    trend_index_options = list(MAJOR_MARKET_INDICES.keys())
    trend_index_saved = edit_strat.get("market_trend_index", "Nifty 50")
    trend_index = st.selectbox(
        "Reference Index",
        trend_index_options,
        index=trend_index_options.index(trend_index_saved) if trend_index_saved in trend_index_options else 0,
        disabled=not use_market_trend,
    )
    trend_indicator_options = ["Moving Average", "VStop"]
    trend_indicator_saved = edit_strat.get("market_trend_indicator", "Moving Average")
    trend_indicator = st.selectbox(
        "Trend Indicator",
        trend_indicator_options,
        index=trend_indicator_options.index(trend_indicator_saved) if trend_indicator_saved in trend_indicator_options else 0,
        disabled=not use_market_trend,
    )
    trend_ma_type = "EMA"
    trend_ma_period = 200
    trend_vstop_atr = 10
    trend_vstop_multiplier = 2.0
    if use_market_trend and trend_indicator == "Moving Average":
        trend_ma_c1, trend_ma_c2 = st.columns(2)
        with trend_ma_c1:
            trend_ma_type = st.selectbox(
                "Moving Average Type", ["EMA", "SMA"],
                index=["EMA", "SMA"].index(edit_strat.get("market_trend_ma_type", "EMA"))
                if edit_strat.get("market_trend_ma_type", "EMA") in ["EMA", "SMA"] else 0,
            )
        with trend_ma_c2:
            trend_ma_period = st.number_input(
                "Moving Average Period", min_value=2, max_value=500,
                value=int(edit_strat.get("market_trend_ma_period", 200)), step=1,
            )
    elif use_market_trend and trend_indicator == "VStop":
        trend_vs_c1, trend_vs_c2 = st.columns(2)
        with trend_vs_c1:
            trend_vstop_atr = st.number_input(
                "VStop ATR Period", min_value=2, max_value=100,
                value=int(edit_strat.get("market_trend_vstop_atr_period", 10)), step=1,
            )
        with trend_vs_c2:
            trend_vstop_multiplier = st.number_input(
                "VStop ATR Multiplier", min_value=0.5, max_value=10.0,
                value=float(edit_strat.get("market_trend_vstop_multiplier", 2.0)), step=0.5,
            )
    market_trend_exit_on_bearish = st.checkbox(
        "Exit existing positions when index trend turns bearish",
        value=bool(edit_strat.get("market_trend_exit_on_bearish", True)),
        disabled=not use_market_trend,
        help="When enabled, a bearish index trend closes held positions at the next strategy rebalance.",
    )

    st.markdown("---")
    st.markdown("#### Email Updates for This Strategy")
    st.caption(
        "Enter the inbox that should receive this strategy's scan targets, entry/exit candidates and portfolio details. "
        "SMTP sender settings must also be configured in Streamlit Secrets. Rebalance-date emails are checked when "
        "the app runs after 4 p.m. IST on a weekday; this session-based trigger is not a server-side scheduler."
    )
    email_col1, email_col2 = st.columns([2, 1])
    strategy_notification_email = email_col1.text_input(
        "Email address for strategy updates",
        value=str(edit_strat.get("notification_email", "") or ""),
        placeholder="you@example.com",
        key=f"strategy_notification_email_{st.session_state.editing_strategy_name or 'new'}",
    )
    with email_col2:
        st.write("Send emails when")
        email_on_scan = st.checkbox(
            "I run a scan",
            value=bool(edit_strat.get("email_on_scan", True)),
            key=f"email_on_scan_{st.session_state.editing_strategy_name or 'new'}",
        )
        email_on_rebalance_date = st.checkbox(
            "Rebalance date is due",
            value=bool(edit_strat.get("email_on_rebalance_date", True)),
            key=f"email_on_rebalance_date_{st.session_state.editing_strategy_name or 'new'}",
        )
        email_on_rebalance_execution = st.checkbox(
            "I execute a rebalance",
            value=bool(edit_strat.get("email_on_rebalance_execution", True)),
            key=f"email_on_rebalance_execution_{st.session_state.editing_strategy_name or 'new'}",
        )

    st.markdown("---")
    save_label = "💾 Update Strategy Config" if edit_mode else "💾 Deploy New Strategy Profile"
    if st.button(save_label, type="primary", use_container_width=True):
        # Validate/normalise inputs first, then update session state atomically.
        # A failed save stays on this page and reports the precise exception.
        target_name = (
            str(st.session_state.editing_strategy_name or "").strip()
            if edit_mode else str(strat_name_input or "").strip()
        )

        try:
            if not target_name:
                raise ValueError("Please enter a name for this strategy.")
            if not edit_mode and target_name in st.session_state.strategies:
                raise ValueError(
                    f"A strategy named '{target_name}' already exists. Choose a different name."
                )
            if float(tot_alloc) <= 0:
                raise ValueError("Allocated capital must be greater than ₹0.")
            if str(strategy_notification_email or "").strip() and not eod_reporting.is_valid_email_address(strategy_notification_email):
                raise ValueError("Please enter a valid notification email address, or leave it blank.")

            selected_groups = []
            if grp_nifty500: selected_groups.append("Nifty 500")
            if grp_nifty200: selected_groups.append("Nifty 200")
            if grp_all_nse_stocks: selected_groups.append("All NSE Stocks")
            if grp_all_etfs: selected_groups.append("All ETFs")
            if grp_gold_etf: selected_groups.append("Gold ETF")
            if grp_liquidbees: selected_groups.append("LiquidBEES")
            if grp_gov_bond: selected_groups.append("Gov Bond")
            selected_groups.extend(selected_etf_categories)
            selected_groups.extend(selected_uploaded_etf_groups)
            selected_groups = list(dict.fromkeys(selected_groups))
            if not selected_groups:
                selected_groups = ["Nifty 500"]

            selected_periods = []
            weights = []
            if p252:
                selected_periods.append(252)
                weights.append(0.4)
            if p120:
                selected_periods.append(120)
                weights.append(0.3)
            if p90:
                selected_periods.append(90)
                weights.append(0.2)
            if p60:
                selected_periods.append(60)
                weights.append(0.1)
            if not selected_periods:
                selected_periods, weights = [252], [1.0]
            else:
                total_weight = sum(weights)
                weights = [weight / total_weight for weight in weights]

            # Build the complete record before assigning it to the strategies map.
            saved_config = {
                "created_date": edit_strat.get("created_date", datetime.now().strftime("%Y-%m-%d")),
                "status": edit_strat.get("status", "Active"),
                "type": st_type,
                "allocated": float(tot_alloc),
                "realized_pnl": float(edit_strat.get("realized_pnl", 0.0)),
                "rebalance_freq": rebal_freq,
                "rebalance_day": int(rebal_day),
                "groups": selected_groups,
                "allocation_multiplier": float(alloc_multiplier),
                "entry_rank": int(entry_rank),
                "exit_rank": int(exit_rank),
                "period_days": selected_periods,
                "period_weights": weights,
                "pct_from_high": float(pct_high),
                "pct_from_low": float(pct_low),
                "moving_average": selected_ma,
                "use_rs": bool(use_rs),
                "rs_benchmark": rs_benchmark,
                "min_price": float(min_price),
                "use_relative_momentum_filter": bool(use_relative_momentum),
                "relative_momentum_index": relative_momentum_index,
                "relative_momentum_ma_type": relative_momentum_ma_type,
                "relative_momentum_ma_period": int(relative_momentum_ma_period),
                "use_market_trend_filter": bool(use_market_trend),
                "market_trend_index": trend_index,
                "market_trend_indicator": trend_indicator,
                "market_trend_ma_type": trend_ma_type,
                "market_trend_ma_period": int(trend_ma_period),
                "market_trend_vstop_atr_period": int(trend_vstop_atr),
                "market_trend_vstop_multiplier": float(trend_vstop_multiplier),
                "market_trend_exit_on_bearish": bool(market_trend_exit_on_bearish),
                "notification_email": str(strategy_notification_email or "").strip(),
                "email_on_scan": bool(email_on_scan),
                "email_on_rebalance_date": bool(email_on_rebalance_date),
                "email_on_rebalance_execution": bool(email_on_rebalance_execution),
                "last_rebalance_email_cycle": edit_strat.get("last_rebalance_email_cycle"),
                "positions": edit_strat.get("positions", []),
            }
            st.session_state.strategies[target_name] = saved_config
            st.session_state.strategy_save_notice = f"Strategy '{target_name}' saved successfully."
        except Exception as exc:
            st.error(
                f"Strategy was not saved ({type(exc).__name__}): {exc}. "
                "Your existing strategies have not been intentionally overwritten."
            )
        else:
            st.session_state.editing_strategy_name = None
            st.session_state.navigation_tab = "DASHBOARD"
            st.rerun()

# ==========================================
# PAGE 3: BACKTEST ENGINE SECTION
# ==========================================
elif st.session_state.navigation_tab == "BACKTEST":
    st.title("📈 BACKTEST STUDIO")
    st.caption("Simulate historical momentum performance with periodic rebalancing, entry/exit rankings, moving average trend rules, asset groups, multiplier scaling, and RS regime filters.")

    strategy_names = list(st.session_state.strategies.keys())
    selected_backtest_strat = st.selectbox("Select Strategy to Backtest :", strategy_names)

    bt_strat_config = st.session_state.strategies[selected_backtest_strat]

    bt_c1, bt_c2, bt_c3 = st.columns(3)
    with bt_c1:
        initial_cap = st.number_input("Initial Backtest Capital (₹) :", value=100000.0, step=10000.0)
    with bt_c2:
        start_d = st.date_input("Start Date :", value=datetime.now() - timedelta(days=365*3))
    with bt_c3:
        end_d = st.date_input("End Date :", value=datetime.now())

    with st.expander("💸 Trading-cost assumptions (deducted from backtest cash)", expanded=True):
        cost_col1, cost_col2, cost_col3 = st.columns(3)
        with cost_col1:
            buy_fee_bps = st.number_input(
                "Estimated buy-side fees (bps)", min_value=0.0, max_value=1000.0,
                value=15.0, step=1.0,
                help="Brokerage and applicable exchange/statutory costs, excluding slippage. 15 bps = 0.15% of buy turnover.",
            )
        with cost_col2:
            sell_fee_bps = st.number_input(
                "Estimated sell-side fees (bps)", min_value=0.0, max_value=1000.0,
                value=25.0, step=1.0,
                help="Estimated sell-side fees and taxes, excluding slippage. Check your broker's current contract note and adjust.",
            )
        with cost_col3:
            slippage_bps = st.number_input(
                "Slippage per side (bps)", min_value=0.0, max_value=1000.0,
                value=5.0, step=1.0,
                help="Estimated execution shortfall on each buy or sell, applied to the traded notional.",
            )
        trading_cost_config = {
            "buy_fee_bps": float(buy_fee_bps),
            "sell_fee_bps": float(sell_fee_bps),
            "slippage_bps": float(slippage_bps),
        }
        st.caption(
            "These are editable estimates, not live FYERS fee quotes. The model deducts fees plus slippage "
            "on actual share turnover, sells first, and reduces unleveraged buys when cash is insufficient."
        )

    use_pit = st.checkbox(
        "Use point-in-time index membership for backtesting",
        value=True,
        help="Historical eligibility is reconstructed from published index changes. This reduces survivorship bias but does not eliminate every data gap."
    )
    if "All NSE Stocks" in bt_strat_config.get("groups", []):
        st.caption(
            "All NSE universe note: the data archive includes historical and currently traded NSE securities; "
            "ticker/ISIN aliases are linked where possible. It is not a certified point-in-time NSE listing ledger, "
            "and the split/bonus-adjusted prices do not include cash dividends."
        )
    if use_pit and any(group in bt_strat_config.get("groups", []) for group in ("Nifty 500", "Nifty 200")):
        st.caption(
            "Data quality note: daily OHLC history for major Nifty indices is stored in this repository. "
            "Historical constituent membership is reconstructed, not a certified daily official ledger; "
            "coverage/cardinality gaps are more likely before 2018. Exact historical Nifty 200 membership "
            "is not available in the stored source, so a Nifty 200 backtest uses a broader Nifty 500 "
            "membership proxy and displays a warning."
        )

    run_bt_btn = st.button("📊 Run Strategy Backtest Simulation", type="primary", use_container_width=True)

    if run_bt_btn:
        # The archive can be committed by GitHub Actions between Streamlit runs.
        # Clear the one-hour price cache before each backtest so it reads the
        # latest saved history rather than a stale in-memory matrix.
        fetch_market_data.clear()
        with st.spinner("Running historical rebalance simulation against market data..."):
            bt_results, holdings_history, bt_prices_df = run_backtest_simulation(
                bt_strat_config, initial_cap, start_d.strftime("%Y-%m-%d"), end_d.strftime("%Y-%m-%d"),
                use_point_in_time=use_pit, trading_cost_config=trading_cost_config,
            )

            if bt_results is not None and not bt_results.empty:
                st.session_state["_last_backtest"] = (bt_results, holdings_history, bt_prices_df)
                st.success("Backtest Completed Successfully!")

                final_val = float(bt_results["Portfolio Value"].iloc[-1])
                total_return_pct = ((final_val - initial_cap) / initial_cap) * 100
                daily_returns = bt_results["Portfolio Value"].pct_change().dropna()
                peak = bt_results["Portfolio Value"].cummax()
                drawdown = bt_results["Portfolio Value"] / peak - 1.0
                max_drawdown_pct = float(drawdown.min() * 100)
                elapsed_days = max(
                    1,
                    (pd.Timestamp(bt_results.index[-1]) - pd.Timestamp(bt_results.index[0])).days,
                )
                elapsed_years = elapsed_days / 365.25
                cagr_pct = (
                    ((final_val / float(initial_cap)) ** (1.0 / elapsed_years) - 1.0) * 100
                    if initial_cap > 0 and final_val > 0 else float("nan")
                )
                daily_vol = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
                annual_vol_pct = daily_vol * np.sqrt(252) * 100
                sharpe_daily = (
                    float(daily_returns.mean() / daily_vol * np.sqrt(252))
                    if daily_vol > 0 else 0.0
                )

                res_m1, res_m2, res_m3 = st.columns(3)
                res_m1.metric("Final Portfolio Value", f"₹{final_val:,.2f}")
                res_m2.metric("Total Backtest Return", f"{total_return_pct:.2f}%")
                res_m3.metric("CAGR", f"{cagr_pct:.2f}%")

                res_m4, res_m5, res_m6 = st.columns(3)
                res_m4.metric("Maximum Drawdown (Daily)", f"{max_drawdown_pct:.2f}%")
                res_m5.metric("Annualized Volatility", f"{annual_vol_pct:.2f}%")
                res_m6.metric("Daily Sharpe Ratio (RF = 0)", f"{sharpe_daily:.2f}")

                cost_total = float(bt_results.get("Trading Costs", pd.Series(dtype=float)).sum())
                buy_turnover_total = float(bt_results.get("Buy Turnover", pd.Series(dtype=float)).sum())
                sell_turnover_total = float(bt_results.get("Sell Turnover", pd.Series(dtype=float)).sum())
                trade_count_total = int(bt_results.get("Trade Count", pd.Series(dtype=int)).sum())
                cost_m1, cost_m2, cost_m3, cost_m4 = st.columns(4)
                cost_m1.metric("Estimated Trading Costs Paid", f"₹{cost_total:,.2f}")
                cost_m2.metric("Buy Turnover", f"₹{buy_turnover_total:,.2f}")
                cost_m3.metric("Sell Turnover", f"₹{sell_turnover_total:,.2f}")
                cost_m4.metric("Executed Orders", f"{trade_count_total:,}")
                st.caption(
                    "The equity curve is net of the configured estimated fees and slippage. Fees are charged "
                    "on actual buy/sell turnover; unleveraged purchases are cash-limited. Dividend cash flows "
                    "and corporate-action entitlements remain separate data limitations. Sharpe assumes a zero "
                    "risk-free rate. Review these assumptions against your broker contract notes."
                )
                st.markdown("#### Daily Portfolio Value Equity Curve")
                st.line_chart(bt_results["Portfolio Value"])

                st.markdown("#### Daily Backtest Data")
                st.dataframe(bt_results, use_container_width=True)
            else:
                st.error("Insufficient market data for the selected timeframe. Try selecting a broader timeframe.")


# ==========================================
# PAGE 4: INDEX CONSTITUENTS
# ==========================================
elif st.session_state.navigation_tab == "INDEX_CONSTITUENTS":
    st.title("🧾 INDEX CONSTITUENTS")
    st.caption(
        "Choose an index to see its published constituent companies. "
        "This section is separate from the strategy scanner and backtesting engine."
    )

    selector_col1, selector_col2, action_col = st.columns([1, 1.5, 1])
    with selector_col1:
        index_category = st.selectbox(
            "Index Category",
            options=list(INDEX_CATEGORIES.keys()),
            key="constituent_index_category",
        )
    with selector_col2:
        selected_index = st.selectbox(
            "Select Index",
            options=list(INDEX_CATEGORIES[index_category].keys()),
            key=f"constituent_index_selector_{index_category}",
        )
    with action_col:
        st.write("")
        st.write("")
        if st.button("🔄 Refresh Constituents", use_container_width=True):
            fetch_index_constituents.clear()
            if index_category == ALL_ETF_CATEGORY:
                fetch_all_etfs_and_bees.clear()
            st.rerun()

    st.caption(
        "Constituents are read from published Nifty Indices/NSE CSV files and cached "
        "for up to 6 hours. You do not need to connect FYERS to view this list."
    )

    try:
        with st.spinner(f"Loading {selected_index} constituents..."):
            all_constituents, source_url = fetch_index_constituents(
                index_category, selected_index
            )
    except Exception as exc:
        st.error(str(exc))
    else:
        industry_count = all_constituents["Industry"].replace("", pd.NA).nunique()
        search_query = st.text_input(
            "Search constituents",
            placeholder="Type a company name, symbol, industry or ISIN...",
            key="constituent_search_query",
        )

        visible_constituents = all_constituents.copy()
        if search_query.strip():
            search_mask = (
                visible_constituents.fillna("")
                .astype(str)
                .apply(
                    lambda column: column.str.contains(
                        search_query.strip(), case=False, regex=False
                    )
                )
                .any(axis=1)
            )
            visible_constituents = visible_constituents.loc[search_mask]

        metric1, metric2, metric3 = st.columns(3)
        metric1.metric(
            "Total ETFs & BEES" if index_category == ALL_ETF_CATEGORY else "Total Constituents",
            f"{len(all_constituents):,}",
        )
        metric2.metric("Matching Search", f"{len(visible_constituents):,}")
        if index_category == ALL_ETF_CATEGORY:
            metric3.metric("Universe", "All listed ETFs & BEES")
        else:
            metric3.metric("Industries Represented", f"{industry_count:,}")

        st.markdown(f"#### {selected_index} — Constituent List")
        st.dataframe(
            visible_constituents,
            use_container_width=True,
            hide_index=True,
            height=600,
        )

        safe_index_name = (
            selected_index.lower()
            .replace("&", "and")
            .replace(" ", "_")
            .replace("/", "_")
        )
        st.download_button(
            "📥 Download visible constituents (CSV)",
            data=visible_constituents.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"{safe_index_name}_constituents.csv",
            mime="text/csv",
            use_container_width=True,
        )
        st.markdown(f"Source: [Official constituent CSV]({source_url})")


# ==========================================
# PAGE 5: HISTORICAL PRICE CHARTS & MEMBERSHIP EVENTS
# ==========================================
elif st.session_state.navigation_tab == "PRICE_CHARTS":
    st.title("📊 HISTORICAL PRICE CHARTS")
    st.caption(
        "View saved daily prices for individual stocks and indices. FYERS history provides OHLCV; "
        "the supplemental NSE archive adds traded value and trade counts when its parquet files are available."
    )

    fyers_symbols = fyers_history_data.load_all_symbols()
    nse_symbols = nse_history_data.list_symbols()
    try:
        archive_stock_symbols = get_all_nse_stock_tickers()
    except Exception as exc:
        archive_stock_symbols = []
        st.warning(f"Full NSE stock chart list is temporarily unavailable: {exc}")
    index_symbols = list(index_history_data.TICKER_TO_INDEX_NAME.keys())
    tradingview_only_symbols = list(tv_charts.TRADINGVIEW_ONLY_INDEXES.keys())
    all_chart_symbols = list(dict.fromkeys(
        index_symbols + tradingview_only_symbols + fyers_symbols
        + [f"{s}.NS" for s in nse_symbols] + archive_stock_symbols
    ))
    if not all_chart_symbols:
        st.warning("No stored price history is available yet. Connect FYERS and confirm the history workflow completed.")
    else:
        # Open on the widely used Nifty 50 symbol; Nifty 500's TradingView alias
        # previously pointed to a non-existent ticker, making the default chart look blank.
        default_symbol = "^NSEI" if "^NSEI" in all_chart_symbols else all_chart_symbols[0]

        def _format_price_chart_symbol(symbol):
            if symbol in index_history_data.TICKER_TO_INDEX_NAME:
                return f"{index_history_data.TICKER_TO_INDEX_NAME[symbol]} ({tv_charts.tv_symbol(symbol)})"
            if symbol in tv_charts.TRADINGVIEW_ONLY_INDEXES:
                info = tv_charts.TRADINGVIEW_ONLY_INDEXES[symbol]
                return f"{info['label']} ({info['symbol']})"
            return symbol.replace(".NS", "")

        selected_symbol = st.selectbox(
            "Select a stock or index",
            options=all_chart_symbols,
            index=all_chart_symbols.index(default_symbol),
            format_func=_format_price_chart_symbol,
            key="historical_chart_symbol",
        )

        chart_interval_labels = {
            "15 minutes": "15",
            "1 hour": "60",
            "Daily": "D",
            "Weekly": "W",
            "Monthly": "M",
        }
        selected_is_fyers_index = selected_symbol.startswith("^")
        if selected_is_fyers_index:
            selected_interval = "Daily"
        else:
            selected_interval = st.selectbox(
                "TradingView interval",
                options=list(chart_interval_labels),
                index=2,
                key="tradingview_chart_interval",
            )

        st.markdown("#### Price chart")
        if selected_is_fyers_index:
            st.info(
                "Index charts are plotted inside Happy Trade from FYERS OHLC candles. "
                "This avoids TradingView's restriction on embedding some index symbols."
            )
        else:
            components.html(
                tv_charts.advanced_chart_html(
                    selected_symbol,
                    interval=chart_interval_labels[selected_interval],
                    height=560,
                    theme="dark",
                ),
                height=590,
                scrolling=False,
            )
        st.caption(
            "TradingView is used for stock and additional index embeds where allowed. Core Nifty indices use FYERS candles "
            "and a native in-app chart. Scanner and backtest calculations continue to use the app's verified stored data."
        )

        is_tradingview_only_index = selected_symbol in tv_charts.TRADINGVIEW_ONLY_INDEXES
        is_index_symbol = selected_symbol.startswith("^") or is_tradingview_only_index
        stock_base = selected_symbol[:-3] if selected_symbol.endswith(".NS") else selected_symbol
        nse_ohlcv = (
            nse_history_data.load_stock_ohlcv(stock_base, adjusted=True)
            if not is_index_symbol else pd.DataFrame()
        )
        archive_ohlcv = pd.DataFrame()
        if not is_index_symbol and nse_ohlcv.empty and selected_symbol.endswith(".NS"):
            try:
                archive_ohlcv = nse_archive_data.load_ohlcv(
                    selected_symbol,
                    start=(pd.Timestamp.now().normalize() - pd.DateOffset(years=5)).date(),
                    end=pd.Timestamp.now().normalize().date(),
                )
            except Exception as exc:
                st.info(f"Full NSE archive candles for this stock could not be loaded: {exc}")
        fyers_ohlcv = (
            fyers_history_data.load_ohlcv(selected_symbol)
            if not is_tradingview_only_index else pd.DataFrame()
        )
        native_index_fetch_used = False

        if selected_is_fyers_index:
            # Streamlit's current FYERS OAuth session can fetch daily index candles
            # directly. The chart-only cache remains in this session and does not
            # modify backtest data files or mark the data as persistently audited.
            native_slug = selected_symbol.lstrip("^").lower()
            today_ist = pd.Timestamp.now(tz="Asia/Kolkata").date()
            default_fetch_start = max(
                pd.Timestamp("2021-01-01").date(),
                today_ist - timedelta(days=365 * 5),
            )
            with st.expander(
                "Load / refresh candles from your connected FYERS account",
                expanded=fyers_ohlcv.empty,
            ):
                fetch_date_cols = st.columns(2)
                with fetch_date_cols[0]:
                    fyers_fetch_start = st.date_input(
                        "FYERS history start date",
                        value=default_fetch_start,
                        key=f"fyers_chart_start_{native_slug}",
                    )
                with fetch_date_cols[1]:
                    fyers_fetch_end = st.date_input(
                        "FYERS history end date",
                        value=today_ist,
                        key=f"fyers_chart_end_{native_slug}",
                    )
                cache_slot = f"_native_fyers_chart_{native_slug}"
                cached_chart = st.session_state.get(cache_slot)
                if (
                    isinstance(cached_chart, dict)
                    and cached_chart.get("start") == fyers_fetch_start.isoformat()
                    and cached_chart.get("end") == fyers_fetch_end.isoformat()
                    and isinstance(cached_chart.get("frame"), pd.DataFrame)
                ):
                    fyers_ohlcv = cached_chart["frame"].copy()
                    native_index_fetch_used = True
                    st.caption(
                        f"Using candles fetched from FYERS for {cached_chart['start']} to {cached_chart['end']} "
                        "in this app session."
                    )
                if "fyers_access_token" not in st.session_state:
                    st.warning("Connect FYERS in the connection panel above to fetch index candles.")
                if st.button(
                    "Fetch / refresh index candles from FYERS",
                    type="primary",
                    use_container_width=True,
                    disabled="fyers_access_token" not in st.session_state,
                    key=f"fetch_fyers_chart_{native_slug}",
                ):
                    if fyers_fetch_start > fyers_fetch_end:
                        st.error("Start date must be on or before end date.")
                    else:
                        try:
                            with st.spinner("Requesting daily candles from FYERS..."):
                                fetched_history = fyers_chart_data.fetch_index_history(
                                    get_fyers_client(),
                                    selected_symbol,
                                    fyers_fetch_start,
                                    fyers_fetch_end,
                                )
                            if fetched_history.empty:
                                st.warning(
                                    "FYERS returned no candles for this symbol and date range. "
                                    "Check your FYERS history entitlement and index symbol availability."
                                )
                            else:
                                fyers_ohlcv = fetched_history
                                native_index_fetch_used = True
                                st.session_state[cache_slot] = {
                                    "start": fyers_fetch_start.isoformat(),
                                    "end": fyers_fetch_end.isoformat(),
                                    "frame": fetched_history,
                                }
                                st.success(
                                    f"Loaded {len(fetched_history):,} daily candles from FYERS "
                                    f"({fetched_history['Date'].min().date()} to {fetched_history['Date'].max().date()})."
                                )
                        except Exception as exc:
                            st.error(
                                f"Could not fetch this index from FYERS ({type(exc).__name__}): {exc}. "
                                "Confirm FYERS is connected and your API app has historical-data permission."
                            )

        if not nse_ohlcv.empty:
            chart_frame = nse_ohlcv.copy()
            chart_source = "NSE bhavcopy archive (split/bonus-adjusted proxy; no dividend adjustment)"
            if "Adjusted Close" in chart_frame.columns and chart_frame["Adjusted Close"].notna().any():
                chart_frame["Chart Close"] = chart_frame["Adjusted Close"]
            else:
                chart_frame["Chart Close"] = chart_frame["Close"]
        elif not archive_ohlcv.empty:
            chart_frame = archive_ohlcv.copy()
            chart_frame["Chart Close"] = chart_frame["Close"]
            chart_source = "Permanent all-NSE archive (split/bonus-adjusted OHLC; cash dividends not included)"
        elif not fyers_ohlcv.empty:
            chart_frame = fyers_ohlcv.copy()
            chart_frame["Chart Close"] = chart_frame["Close"]
            chart_source = (
                "FYERS API history fetched in this session"
                if native_index_fetch_used else "FYERS API saved daily OHLCV"
            )
        else:
            chart_frame = pd.DataFrame()
            chart_source = "No stored candles for this symbol"

        if chart_frame.empty:
            st.warning(
                (
                    f"No verified local FYERS/NSE candles were found for {selected_symbol}. "
                    "For Nifty index symbols, connect FYERS and use the fetch panel above to draw the chart here. "
                    "Legacy index CSVs without a FYERS source marker are not treated as trusted data."
                )
            )
        else:
            chart_frame["Date"] = pd.to_datetime(chart_frame["Date"], errors="coerce")
            chart_frame = chart_frame.dropna(subset=["Date", "Chart Close"]).sort_values("Date")
            min_date = chart_frame["Date"].min().date()
            max_date = chart_frame["Date"].max().date()
            range_cols = st.columns(2)
            with range_cols[0]:
                chart_start = st.date_input(
                    "Stored-data start date",
                    value=min_date if selected_is_fyers_index else max(min_date, max_date - timedelta(days=365)),
                    min_value=min_date, max_value=max_date, key=f"price_chart_start_{selected_symbol.replace('^', '')}"
                )
            with range_cols[1]:
                chart_end = st.date_input(
                    "Stored-data end date", value=max_date,
                    min_value=min_date, max_value=max_date, key=f"price_chart_end_{selected_symbol.replace('^', '')}"
                )
            view = chart_frame.loc[
                (chart_frame["Date"].dt.date >= chart_start)
                & (chart_frame["Date"].dt.date <= chart_end)
            ].copy()
            if chart_end < chart_start:
                st.error("Chart end date must be on or after the start date.")
            elif view.empty:
                st.warning("No candles exist inside this date range.")
            else:
                if selected_is_fyers_index:
                    candles = view.dropna(subset=["Open", "High", "Low", "Close"]).copy()
                    if not candles.empty:
                        native_fig = go.Figure(data=[go.Candlestick(
                            x=candles["Date"],
                            open=pd.to_numeric(candles["Open"], errors="coerce"),
                            high=pd.to_numeric(candles["High"], errors="coerce"),
                            low=pd.to_numeric(candles["Low"], errors="coerce"),
                            close=pd.to_numeric(candles["Close"], errors="coerce"),
                            increasing_line_color="#20d878",
                            decreasing_line_color="#ff667a",
                            name=index_history_data.TICKER_TO_INDEX_NAME.get(selected_symbol, selected_symbol),
                        )])
                        native_fig.update_layout(
                            template="plotly_dark",
                            paper_bgcolor="#050806",
                            plot_bgcolor="#050806",
                            font={"color": "#eaf6ed"},
                            height=560,
                            margin={"l": 12, "r": 12, "t": 34, "b": 12},
                            xaxis_title="Date",
                            yaxis_title="Index level",
                            hovermode="x unified",
                            xaxis={"rangeslider": {"visible": True}, "gridcolor": "#1c3325"},
                            yaxis={"gridcolor": "#1c3325", "fixedrange": False},
                        )
                        st.plotly_chart(
                            native_fig,
                            use_container_width=True,
                            config={"scrollZoom": True, "displaylogo": False},
                        )
                    else:
                        st.warning("No valid OHLC candles are available to draw the index chart.")
                close = pd.to_numeric(view["Chart Close"], errors="coerce").dropna()
                first_close = float(close.iloc[0]) if not close.empty else float("nan")
                last_close = float(close.iloc[-1]) if not close.empty else float("nan")
                total_return = (last_close / first_close - 1.0) * 100 if first_close > 0 else float("nan")
                running_peak = close.cummax()
                drawdown = close.div(running_peak).sub(1.0)
                max_dd = float(drawdown.min() * 100) if not drawdown.empty else float("nan")
                metrics = st.columns(4)
                metrics[0].metric("Candles", f"{len(view):,}")
                metrics[1].metric("First close", f"{first_close:,.2f}" if np.isfinite(first_close) else "—")
                metrics[2].metric("Last close", f"{last_close:,.2f}" if np.isfinite(last_close) else "—")
                metrics[3].metric("Price return", f"{total_return:.2f}%" if np.isfinite(total_return) else "—")
                st.caption(
                    f"Metrics source: {chart_source}. Stored date range: {min_date} to {max_date}. "
                    f"Max drawdown in selected range: {max_dd:.2f}%. "
                    "These metrics and exported rows are calculated from stored data, not from the TradingView widget."
                )
                if "Volume" in view.columns and pd.to_numeric(view["Volume"], errors="coerce").notna().any():
                    st.markdown("#### Stored historical volume (FYERS/NSE archive)")
                    st.bar_chart(view.set_index("Date")["Volume"])
                if "Trades" in view.columns and pd.to_numeric(view["Trades"], errors="coerce").notna().any():
                    st.markdown("#### Daily number of trades (NSE archive)")
                    st.bar_chart(view.set_index("Date")["Trades"])
                if "Traded Value" in view.columns and pd.to_numeric(view["Traded Value"], errors="coerce").notna().any():
                    st.markdown("#### Daily traded value (NSE archive)")
                    st.line_chart(view.set_index("Date")["Traded Value"])
                st.download_button(
                    "📥 Download displayed OHLCV/trading data (CSV)",
                    data=view.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"{stock_base.replace('^', '')}_history_{chart_start}_{chart_end}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    st.markdown("---")
    st.markdown("#### Index inclusion / exclusion history")
    st.caption(
        "Upload the IndexInclExcl workbook to inspect its dated membership events. "
        "This workbook contains company names rather than a stable ticker/ISIN for every historical company, "
        "so it must be mapped and validated before it can safely replace the backtest's point-in-time membership ledger."
    )
    membership_upload = st.file_uploader(
        "Upload IndexInclExcl.xls or .xlsx",
        type=["xls", "xlsx"],
        key="price_chart_membership_upload",
    )
    if membership_upload is not None:
        try:
            sheets = pd.read_excel(membership_upload, sheet_name=None)
            event_frames = []
            for sheet_name, frame in sheets.items():
                if frame is None or frame.empty:
                    continue
                lookup = {str(col).strip().lower(): col for col in frame.columns}
                index_col = next((lookup[k] for k in lookup if k in {"index name", "index"}), None)
                date_col = next((lookup[k] for k in lookup if k in {"event date", "date", "effective date"}), None)
                company_col = next((lookup[k] for k in lookup if k in {"scrip name", "company name", "security name"}), None)
                description_col = next((lookup[k] for k in lookup if k in {"description", "action"}), None)
                if not all([date_col, company_col, description_col]):
                    continue
                part = pd.DataFrame({
                    "Index": frame[index_col].fillna(sheet_name).astype(str) if index_col else sheet_name,
                    "Event Date": pd.to_datetime(frame[date_col], errors="coerce", dayfirst=True),
                    "Company": frame[company_col].astype(str).str.strip(),
                    "Action": frame[description_col].astype(str).str.strip(),
                })
                part = part.dropna(subset=["Event Date"])
                part["Action"] = part["Action"].str.extract(r"(Inclusion|Exclusion)", expand=False).str.upper()
                part = part.dropna(subset=["Action"])
                event_frames.append(part)
            if event_frames:
                event_data = pd.concat(event_frames, ignore_index=True).sort_values(["Index", "Event Date", "Company"])
                index_options = sorted(event_data["Index"].dropna().astype(str).unique())
                chosen_event_index = st.selectbox("Filter membership events by index", index_options, key="uploaded_event_index")
                visible_events = event_data[event_data["Index"].astype(str).eq(chosen_event_index)].copy()
                ev1, ev2, ev3 = st.columns(3)
                ev1.metric("Events in workbook", f"{len(event_data):,}")
                ev2.metric("Events for selected index", f"{len(visible_events):,}")
                ev3.metric("Event date range", f"{visible_events['Event Date'].min().date()} to {visible_events['Event Date'].max().date()}" if not visible_events.empty else "—")
                st.dataframe(visible_events, use_container_width=True, hide_index=True, height=450)
                st.download_button(
                    "📥 Download normalized inclusion/exclusion events",
                    data=event_data.assign(**{"Event Date": event_data["Event Date"].dt.strftime("%Y-%m-%d")}).to_csv(index=False).encode("utf-8-sig"),
                    file_name="index_inclusion_exclusion_events.csv",
                    mime="text/csv",
                )
                st.warning(
                    "The event file currently ends in 2020 and has company names, not guaranteed stable ticker IDs. "
                    "The backtest will not treat this as a complete membership history until symbol/ISIN mapping and event-date coverage are verified."
                )
            else:
                st.error("No recognized Index Name / Event Date / Scrip Name / Description columns were found in this workbook.")
        except Exception as exc:
            st.error(f"Could not read the membership workbook: {type(exc).__name__}: {exc}")



# ==========================================
# PAGE 6: DATA HEALTH, END-OF-DAY REPORT & ALERTS
# ==========================================
elif st.session_state.navigation_tab == "DATA_REPORTS":
    st.title("🛡️ DATA HEALTH & END-OF-DAY REPORT")
    st.caption(
        "Validate the stored historical dataset, generate a rules-based after-close report for a chosen strategy, "
        "and optionally deliver it through Telegram or email. This page never places orders."
    )
    health_tab, eod_tab = st.tabs(["Historical Data Health", "End-of-Day Report & Alerts"])

    with health_tab:
        history_summary = fyers_history_data.get_data_summary()
        summary_cols = st.columns(4)
        summary_cols[0].metric("Symbols with FYERS History", f'{history_summary.get("symbols", 0):,}')
        summary_cols[1].metric("Stored Daily Candles", f'{history_summary.get("rows", 0):,}')
        summary_cols[2].metric("Oldest Stored Date", history_summary.get("oldest_date") or "—")
        summary_cols[3].metric("Latest FYERS Date", history_summary.get("latest_date") or "—")

        st.markdown("#### Stock / ETF OHLC Audit")
        st.caption(
            "Checks the raw saved FYERS CSVs for source markers, duplicate dates, invalid OHLC values, "
            "stale files, long date gaps and unusually large close moves. Large moves and gaps are review flags, "
            "not automatic proof of bad data."
        )
        if st.button("🔍 Run full stock / ETF data audit", type="primary", key="run_stock_data_audit"):
            with st.spinner("Auditing saved FYERS stock and ETF history..."):
                audited = data_health.audit_fyers_price_files(max_stale_days=7)
                st.session_state["_stock_data_audit"] = audited
                st.session_state["_stock_data_audit_at"] = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M IST")

        audit_frame = st.session_state.get("_stock_data_audit")
        if isinstance(audit_frame, pd.DataFrame):
            audit_summary = data_health.summarize_audit(audit_frame)
            audit_cols = st.columns(4)
            audit_cols[0].metric("Symbols Audited", f'{audit_summary["symbols"]:,}')
            audit_cols[1].metric("Passed Structural Checks", f'{audit_summary["healthy"]:,}')
            audit_cols[2].metric("Symbols Needing Review", f'{audit_summary["issues"]:,}')
            audit_cols[3].metric("Stale Files", f'{audit_summary["stale"]:,}')
            st.caption(
                f'Last full audit: {st.session_state.get("_stock_data_audit_at", "—")}. '
                "A structurally clean file has not necessarily been validated value-by-value against an official exchange source."
            )
            status_filter = st.multiselect(
                "Show health statuses",
                options=sorted(audit_frame["Health Status"].dropna().astype(str).unique()),
                default=sorted(audit_frame["Health Status"].dropna().astype(str).unique()),
                key="stock_audit_status_filter",
            )
            visible_audit = audit_frame[audit_frame["Health Status"].astype(str).isin(status_filter)]
            st.dataframe(visible_audit, use_container_width=True, hide_index=True, height=450)
            st.download_button(
                "📥 Download stock / ETF audit (CSV)",
                data=audit_frame.to_csv(index=False).encode("utf-8-sig"),
                file_name="fyers_stock_etf_data_health.csv",
                mime="text/csv",
                key="download_stock_audit",
            )
        else:
            st.info("Run the audit to inspect individual symbols. It reads stored files only and does not download new history.")

        st.markdown("#### Index Inception / Coverage Audit")
        try:
            index_coverage = index_history_data.get_index_history_coverage()
            if index_coverage is not None and not index_coverage.empty:
                st.dataframe(index_coverage, use_container_width=True, hide_index=True, height=330)
                st.caption(
                    "Coverage status checks date ranges and structural gaps. It does not certify that every saved "
                    "index close matches the exchange's official historical value."
                )
                st.download_button(
                    "📥 Download index coverage audit (CSV)",
                    data=index_coverage.to_csv(index=False).encode("utf-8-sig"),
                    file_name="index_history_coverage_audit.csv",
                    mime="text/csv",
                    key="download_index_audit",
                )
            else:
                st.warning("No index-coverage audit rows are available.")
        except Exception as exc:
            st.warning(f"Index audit could not be read: {type(exc).__name__}: {exc}")

        st.markdown("#### NSE Historical Archive")
        refresh_archive = st.button("🔄 Refresh NSE archive status", key="refresh_nse_archive_status")
        archive_status = nse_archive_data.get_archive_status(refresh=refresh_archive)
        if archive_status.get("available"):
            archive_cols = st.columns(4)
            archive_cols[0].metric("Published Archive Years", f'{archive_status.get("years_published", 0):,}')
            archive_cols[1].metric("Downloaded Locally", f'{archive_status.get("years_downloaded_locally", 0):,}')
            archive_cols[2].metric("Archive First Date", archive_status.get("first_date") or "—")
            archive_cols[3].metric("Archive Latest Date", archive_status.get("latest_date") or "—")
            st.caption(
                f'Archive manifest generated: {archive_status.get("generated_at") or "unknown"}. '
                f'Symbol count: {archive_status.get("total_symbols") or "unknown"}. '
                "The archive stores split/bonus-adjusted daily OHLC and close, plus volume, turnover and trade counts; "
                "cash dividends are not included in the adjusted prices."
            )
        else:
            st.warning(
                "NSE archive manifest is unavailable. Existing stored candles may still be usable, but missing "
                f"historical periods cannot be assumed repaired. Detail: {archive_status.get('error') or 'not reported'}"
            )

        st.markdown("#### Prepare Five Years of All-NSE History")
        st.caption(
            "Download and verify the yearly compressed archive files needed for the last five years of all-NSE "
            "backtests. This stores the archive files locally in the running app without expanding every stock "
            "into memory. Backtests also fetch missing years automatically for their selected date range."
        )
        if st.button(
            "⬇️ Download / verify last 5 years of all-NSE history",
            type="primary",
            key="prefetch_all_nse_five_years",
            use_container_width=True,
        ):
            prefetch_end = pd.Timestamp.now(tz="Asia/Kolkata").date()
            prefetch_start = (pd.Timestamp(prefetch_end) - pd.DateOffset(years=5)).date()
            try:
                with st.spinner("Downloading and verifying the compressed NSE history files..."):
                    prefetch_summary = nse_archive_data.prefetch_years(prefetch_start, prefetch_end)
                st.success(
                    f"Cached {prefetch_summary['years_cached']} yearly archive files "
                    f"({prefetch_summary['megabytes']:.2f} MB)."
                )
                st.caption(
                    f"Archive dates: {prefetch_summary.get('first_date') or '—'} to "
                    f"{prefetch_summary.get('latest_date') or '—'}. "
                    f"Security IDs in archive: {prefetch_summary.get('symbols', 0):,}. "
                    "The archive is cached in yearly files, ready for backtests and scans."
                )
            except Exception as exc:
                st.error(
                    f"Could not cache the all-NSE archive ({type(exc).__name__}): {exc}. "
                    "The scan/backtest loader can still fetch available years on demand."
                )

    with eod_tab:
        st.markdown("#### Generate report for a saved strategy")
        strategy_names = list(st.session_state.get("strategies", {}).keys())
        if not strategy_names:
            st.warning("No strategy is available in this Streamlit session. Open Investing Strategy to configure one first.")
        else:
            report_strategy_name = st.selectbox(
                "Strategy for this report",
                strategy_names,
                key="eod_report_strategy",
            )
            report_strategy = st.session_state.strategies[report_strategy_name]
            report_email_key = f"strategy_notification_email_{report_strategy_name}"
            default_report_email = str(
                report_strategy.get("notification_email", "") or _email_secret("ALERT_EMAIL_TO")
            ).strip()
            report_recipient_email = st.text_input(
                "Email address for this strategy's updates",
                value=default_report_email,
                placeholder="you@example.com",
                key=report_email_key,
                help="This inbox receives this EOD report and can also be saved to the strategy's email settings.",
            )
            if report_recipient_email.strip():
                if eod_reporting.is_valid_email_address(report_recipient_email):
                    report_strategy["notification_email"] = report_recipient_email.strip()
                else:
                    st.warning("Enter a valid email address before sending or saving email updates.")
            elif report_email_key in st.session_state:
                report_strategy["notification_email"] = ""
            st.caption(
                "Generate this after the market close. Signal candidates are calculated from stored historical data "
                "and the chosen strategy; the report is informational and does not place orders."
            )
            if st.button("📝 Generate end-of-day report", type="primary", key="generate_eod_report"):
                try:
                    with st.spinner("Calculating the strategy scan from saved price history..."):
                        groups = report_strategy.get("groups", ["Nifty 500"])
                        report_tickers = list(get_trusted_tickers_by_group(groups))
                        report_tickers.extend(["^NSEI", NIFTY_REGIME_TICKER, GSEC_REGIME_TICKER])
                        if report_strategy.get("use_relative_momentum_filter", False):
                            report_tickers.append(
                                MAJOR_MARKET_INDICES.get(
                                    report_strategy.get("relative_momentum_index", "Nifty 50"), "^NSEI"
                                )
                            )
                        all_prices = fetch_market_data(
                            list(dict.fromkeys(report_tickers)),
                            period="3y",
                        )
                        if all_prices is None or all_prices.empty:
                            raise ValueError("No stored price history was returned for the selected strategy.")
                        reference = all_prices.get("^NSEI", pd.Series(dtype=float)).dropna()
                        signal_date = reference.index[-1] if not reference.empty else all_prices.index[-1]
                        report_prices = all_prices.loc[:signal_date].copy()
                        if report_prices.empty:
                            raise ValueError("No price history exists through the latest reference-index session.")

                        selected_candidates = run_strategy_stock_scanner(
                            report_strategy,
                            price_subset=report_prices,
                            as_of_date=signal_date,
                        )
                        trend_name = report_strategy.get("market_trend_index", "Nifty 50")
                        trend_ticker = MAJOR_MARKET_INDICES.get(trend_name, "^NSEI")
                        trend_series = report_prices[trend_ticker].dropna() if trend_ticker in report_prices else pd.Series(dtype=float)
                        if len(trend_series) >= 200:
                            trend_ema = trend_series.ewm(span=200, adjust=False).mean().iloc[-1]
                            trend_state = "BULLISH (above 200 EMA)" if float(trend_series.iloc[-1]) > float(trend_ema) else "BEARISH (below / at 200 EMA)"
                        else:
                            trend_state = "UNKNOWN (insufficient index history for 200 EMA)"

                        current_positions = report_strategy.get("positions", [])
                        held_symbols = sorted({
                            str(position.get("Symbol", "")).strip()
                            for position in current_positions
                            if isinstance(position, dict) and str(position.get("Symbol", "")).strip()
                        })
                        candidate_rows = []
                        for rank, symbol in enumerate(selected_candidates, start=1):
                            if symbol not in report_prices.columns:
                                continue
                            series = report_prices[symbol].dropna()
                            if series.empty:
                                continue
                            latest_close = float(series.iloc[-1])
                            def _period_return(values, sessions):
                                if len(values) <= sessions:
                                    return float("nan")
                                base = float(values.iloc[-(sessions + 1)])
                                return (float(values.iloc[-1]) / base - 1.0) * 100.0 if base > 0 else float("nan")
                            candidate_rows.append({
                                "Rank": rank,
                                "Symbol": symbol,
                                "Latest Close": latest_close,
                                "1D Return %": _period_return(series, 1),
                                "60D Return %": _period_return(series, 60),
                                "252D Return %": _period_return(series, 252),
                                "Status": "Current holding" if symbol in held_symbols else "Candidate / review",
                            })
                        candidate_frame = pd.DataFrame(candidate_rows)
                        history_status = fyers_history_data.get_data_summary()
                        last_saved_date = history_status.get("latest_date") or str(signal_date.date())
                        generated_at = pd.Timestamp.now(tz="Asia/Kolkata").strftime("%Y-%m-%d %H:%M IST")
                        held_not_selected = [symbol for symbol in held_symbols if symbol not in selected_candidates]
                        report_lines = [
                            "NSE MOMENTUM PORTFOLIO — END-OF-DAY REPORT",
                            f"Generated: {generated_at}",
                            f"Strategy: {report_strategy_name}",
                            f"Signal session: {pd.Timestamp(signal_date).date()}",
                            f"Latest date in FYERS manifest: {last_saved_date}",
                            f"Market regime ({trend_name}): {trend_state}",
                            f"Strategy groups: {', '.join(groups)}",
                            f"Configured rebalance: {report_strategy.get('rebalance_freq', 'Monthly')} / day {report_strategy.get('rebalance_day', 1)}",
                            "",
                            "STRATEGY-ELIGIBLE SELECTIONS",
                        ]
                        if candidate_frame.empty:
                            report_lines.append("No securities passed all configured filters on the latest stored session.")
                        else:
                            report_lines.append("Rank | Symbol | Close | 1D % | 60D % | 252D % | Status")
                            for _, row in candidate_frame.iterrows():
                                def _fmt(value):
                                    return f"{float(value):+.2f}%" if pd.notna(value) and np.isfinite(float(value)) else "n/a"
                                report_lines.append(
                                    f'{int(row["Rank"])} | {row["Symbol"]} | ₹{row["Latest Close"]:,.2f} | '
                                    f'{_fmt(row["1D Return %"])} | {_fmt(row["60D Return %"])} | '
                                    f'{_fmt(row["252D Return %"])} | {row["Status"]}'
                                )
                        report_lines.extend(["", "CURRENT HOLDINGS NOT IN THE SCAN SELECTION"])
                        report_lines.extend(held_not_selected or ["None recorded in this strategy session."])
                        report_lines.extend([
                            "",
                            "DATA / EXECUTION NOTES",
                            "Signals use stored daily closes and your saved rules. A signal is not an order or a guarantee of execution.",
                            "Confirm data health, liquidity, spreads, corporate actions and position sizing before acting.",
                            "This manually generated EOD report is sent only when you press Send. Rebalance-date emails are checked separately when the app runs after 4 p.m. IST on a weekday.",
                        ])
                        report_text = "\n".join(report_lines)
                        st.session_state["_eod_report_text"] = report_text
                        st.session_state["_eod_candidates_frame"] = candidate_frame
                        st.session_state["_eod_report_generated_at"] = generated_at
                        st.session_state["_eod_report_strategy_name"] = report_strategy_name
                except Exception as exc:
                    st.error(f"Could not generate the EOD report: {type(exc).__name__}: {exc}")

            saved_report = st.session_state.get("_eod_report_text")
            if saved_report:
                st.markdown("#### Latest generated report")
                st.code(saved_report, language=None)
                st.download_button(
                    "📥 Download EOD report (TXT)",
                    data=saved_report.encode("utf-8"),
                    file_name=f"nse_momentum_eod_{pd.Timestamp.now(tz='Asia/Kolkata').strftime('%Y%m%d')}.txt",
                    mime="text/plain",
                    key="download_eod_report",
                )
                saved_candidates = st.session_state.get("_eod_candidates_frame")
                if isinstance(saved_candidates, pd.DataFrame) and not saved_candidates.empty:
                    st.dataframe(saved_candidates, use_container_width=True, hide_index=True)
                    st.download_button(
                        "📥 Download report selections (CSV)",
                        data=saved_candidates.to_csv(index=False).encode("utf-8-sig"),
                        file_name="nse_momentum_eod_selections.csv",
                        mime="text/csv",
                        key="download_eod_selections",
                    )

                st.markdown("#### Alert channels")
                st.caption(
                    "Optional Streamlit Secrets: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID; or "
                    "SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM and ALERT_EMAIL_TO."
                )
                def _secret_value(name, default=""):
                    try:
                        value = st.secrets.get(name, default)
                        return str(value or default).strip()
                    except Exception:
                        return str(default or "").strip()

                telegram_token = _secret_value("TELEGRAM_BOT_TOKEN")
                telegram_chat = _secret_value("TELEGRAM_CHAT_ID")
                smtp_host = _secret_value("SMTP_HOST")
                smtp_username = _secret_value("SMTP_USERNAME")
                smtp_password = _secret_value("SMTP_PASSWORD")
                smtp_from = _secret_value("SMTP_FROM", smtp_username)
                smtp_to = report_recipient_email.strip()
                try:
                    smtp_port = int(_secret_value("SMTP_PORT", "587"))
                except ValueError:
                    smtp_port = 587
                status_cols = st.columns(2)
                status_cols[0].write("Telegram: **configured**" if telegram_token and telegram_chat else "Telegram: not configured")
                status_cols[1].write("Email: **configured**" if smtp_host and smtp_from and smtp_to else "Email: not configured")

                if st.button("📨 Send report to configured channels", type="primary", key="send_eod_alerts"):
                    destinations = 0
                    delivered = 0
                    delivery_messages = []
                    if telegram_token and telegram_chat:
                        destinations += 1
                        ok, message = eod_reporting.send_telegram_alert(
                            telegram_token, telegram_chat, saved_report
                        )
                        delivered += int(ok)
                        delivery_messages.append(("Telegram", ok, message))
                    if smtp_host and smtp_from and smtp_to:
                        destinations += 1
                        ok, message = eod_reporting.send_email_alert(
                            subject=f"NSE Momentum EOD Report — {st.session_state.get('_eod_report_strategy_name', 'Strategy')}",
                            body=saved_report,
                            smtp_host=smtp_host,
                            smtp_port=smtp_port,
                            username=smtp_username,
                            password=smtp_password,
                            sender=smtp_from,
                            recipient=smtp_to,
                            use_tls=True,
                        )
                        delivered += int(ok)
                        delivery_messages.append(("Email", ok, message))
                    if destinations == 0:
                        st.warning("No alert channel is configured. Add the Telegram or SMTP keys in Streamlit Secrets.")
                    else:
                        for channel, ok, message in delivery_messages:
                            (st.success if ok else st.error)(f"{channel}: {message}")
                        if delivered:
                            st.success(f"Report delivered through {delivered} of {destinations} configured channel(s).")

    st.markdown("---")
    st.caption(
        "Strategy emails send when you run a scan, execute a rebalance, or the app checks a due rebalance cycle after 4 p.m. IST on a weekday. "
        "Streamlit does not run this check while idle or closed, and strategy settings are session-scoped; guaranteed unattended delivery "
        "requires a durable private strategy store and an external scheduler."
    )
