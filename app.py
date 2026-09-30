from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
import streamlit as st

from fyers_data import (
    build_login_url,
    clean_symbol,
    credentials_available,
    exchange_auth_code,
    fetch_prices,
    get_fyers_access_token,
    get_profile,
)


# ============================================================
# APP CONFIG
# ============================================================

st.set_page_config(
    page_title="NSE Momentum Portfolio",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)


APP_DIR = Path(__file__).resolve().parent

GROUP_FILE = APP_DIR / "universe_groups.csv"


# ============================================================
# DEFAULT UNIVERSE
# ============================================================

DEFAULT_UNIVERSE = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "ICICIBANK",
    "INFY",
    "BHARTIARTL",
    "ITC",
    "SBIN",
    "LT",
    "BAJFINANCE",
    "HINDUNILVR",
    "MARUTI",
    "SUNPHARMA",
    "TATASTEEL",
    "TATAMOTORS",
    "AXISBANK",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "ADANIENT",
    "COALINDIA",
    "TITAN",
    "ULTRACEMCO",
    "WIPRO",
    "NESTLEIND",
    "GRASIM",
    "TECHM",
    "JSWSTEEL",
    "HCLTECH",
    "HEROMOTOCO",
    "DRREDDY",
    "CIPLA",
    "APOLLOHOSP",
    "BAJAJ-AUTO",
    "EICHERMOT",
    "BPCL",
    "DIVISLAB",
    "TATACONSUM",
    "BRITANNIA",
    "BEL",
    "HAL",
    "TRENT",
    "VBL",
]


CUSTOM_DEFENSIVE = [
    "GOLDBEES",
    "LIQUIDBEES",
    "GSEC10YEAR",
]


# ============================================================
# BASIC HELPERS
# ============================================================

def normalize_symbols(values):

    result = []

    seen = set()

    for value in values:

        symbol = str(value).strip().upper()

        symbol = symbol.replace(".NS", "")
        symbol = symbol.replace("NSE:", "")
        symbol = symbol.replace("-EQ", "")

        if symbol and symbol not in seen:

            result.append(symbol)

            seen.add(symbol)

    return result


def parse_int_list(text):

    values = []

    for item in str(text).replace(";", ",").split(","):

        item = item.strip()

        if not item:
            continue

        try:

            number = int(item)

            if number > 0:
                values.append(number)

        except ValueError:

            raise ValueError(
                f"Invalid lookback value: {item}"
            )

    return values


def parse_float_list(text):

    values = []

    for item in str(text).replace(";", ",").split(","):

        item = item.strip()

        if not item:
            continue

        try:

            number = float(item)

            if number >= 0:
                values.append(number)

        except ValueError:

            raise ValueError(
                f"Invalid weight value: {item}"
            )

    return values


# ============================================================
# LOAD GROUP CATALOG
# ============================================================

@st.cache_data
def load_group_catalog():

    if not GROUP_FILE.exists():

        return pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "group",
                "sub_group",
                "asset_type",
                "fyers_symbol",
                "custom_multiplier_enabled",
            ]
        )

    try:

        df = pd.read_csv(GROUP_FILE)

        required = [
            "symbol",
            "name",
            "group",
            "sub_group",
            "asset_type",
            "fyers_symbol",
            "custom_multiplier_enabled",
        ]

        for column in required:

            if column not in df.columns:

                df[column] = ""

        df["symbol"] = (
            df["symbol"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

        return df

    except Exception:

        return pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "group",
                "sub_group",
                "asset_type",
                "fyers_symbol",
                "custom_multiplier_enabled",
            ]
        )


catalog = load_group_catalog()


# ============================================================
# GROUP FUNCTIONS
# ============================================================

def get_group_names():

    if catalog.empty:
        return []

    values = (
        catalog["group"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    return sorted(
        [
            value
            for value in values
            if value
        ]
    )


def get_subgroup_names(group=None):

    if catalog.empty:
        return []

    data = catalog.copy()

    if group:

        data = data[
            data["group"] == group
        ]

    values = (
        data["sub_group"]
        .dropna()
        .astype(str)
        .str.strip()
        .unique()
        .tolist()
    )

    return sorted(
        [
            value
            for value in values
            if value
        ]
    )


def get_symbols_for_group(
    group,
    subgroup=None,
):

    if catalog.empty:

        return []

    data = catalog[
        catalog["group"] == group
    ].copy()

    if subgroup:

        data = data[
            data["sub_group"] == subgroup
        ]

    return normalize_symbols(
        data["symbol"].tolist()
    )


def get_asset_metadata(symbol):

    if catalog.empty:

        return {
            "group": "",
            "sub_group": "",
            "asset_type": "",
            "name": symbol,
        }

    rows = catalog[
        catalog["symbol"] == symbol
    ]

    if rows.empty:

        return {
            "group": "",
            "sub_group": "",
            "asset_type": "",
            "name": symbol,
        }

    row = rows.iloc[0]

    return {
        "group": row.get("group", ""),
        "sub_group": row.get("sub_group", ""),
        "asset_type": row.get("asset_type", ""),
        "name": row.get("name", symbol),
    }


# ============================================================
# FYERS AUTH
# ============================================================

def get_current_auth_code():

    query_params = st.query_params

    auth_code = query_params.get(
        "auth_code"
    )

    if isinstance(auth_code, list):

        if auth_code:
            return auth_code[0]

        return None

    return auth_code


def fyers_login_section():

    st.sidebar.subheader(
        "🔐 FYERS Connection"
    )

    if not credentials_available():

        st.sidebar.error(
            "FYERS credentials are missing."
        )

        st.sidebar.caption(
            "Add FYERS_APP_ID, FYERS_SECRET_ID and FYERS_REDIRECT_URI in Replit Secrets."
        )

        return None

    auth_code = get_current_auth_code()

    if auth_code:

        if (
            st.session_state.get(
                "processed_auth_code"
            )
            != auth_code
        ):

            token, error = exchange_auth_code(
                auth_code
            )

            if token:

                st.session_state[
                    "fyers_access_token"
                ] = token

                st.session_state[
                    "processed_auth_code"
                ] = auth_code

                st.query_params.clear()

                st.rerun()

            else:

                st.sidebar.error(
                    f"FYERS login failed: {error}"
                )

    token = st.session_state.get(
        "fyers_access_token"
    )

    if token:

        profile = get_profile(token)

        if (
            isinstance(profile, dict)
            and profile.get("s") == "ok"
        ):

            st.sidebar.success(
                "🟢 FYERS connected"
            )

            return token

        else:

            st.session_state.pop(
                "fyers_access_token",
                None
            )

    login_url = build_login_url()

    if login_url:

        st.sidebar.link_button(
            "🔑 Login / Connect to FYERS",
            login_url,
            use_container_width=True,
        )

    st.sidebar.caption(
        "FYERS access tokens are session/day dependent. "
        "A fresh login may be required for a new trading session."
    )

    return None


# ============================================================
# RETURN CALCULATION
# ============================================================

def calculate_return_score(
    prices,
    as_of_date,
    lookbacks,
    weights,
):

    available = prices.index[
        prices.index
        <= pd.Timestamp(as_of_date)
    ]

    if len(available) == 0:

        return pd.Series(dtype=float)

    actual_date = available[-1]

    idx = prices.index.get_loc(
        actual_date
    )

    score_parts = []

    valid_weights = []

    for lookback, weight in zip(
        lookbacks,
        weights,
    ):

        if idx - lookback < 0:

            continue

        start_prices = prices.iloc[
            idx - lookback
        ]

        end_prices = prices.iloc[
            idx
        ]

        returns = (
            end_prices / start_prices
        ) - 1

        returns = (
            returns
            .replace(
                [np.inf, -np.inf],
                np.nan,
            )
            .dropna()
        )

        if returns.empty:

            continue

        score_parts.append(
            returns
        )

        valid_weights.append(
            weight
        )

    if not score_parts:

        return pd.Series(dtype=float)

    weights_arr = np.asarray(
        valid_weights,
        dtype=float,
    )

    if weights_arr.sum() == 0:

        weights_arr = (
            np.ones(
                len(weights_arr)
            )
            / len(weights_arr)
        )

    else:

        weights_arr = (
            weights_arr
            / weights_arr.sum()
        )

    return_matrix = pd.concat(
        score_parts,
        axis=1,
    )

    return_matrix.columns = [
        f"Return_{i}"
        for i in range(
            len(return_matrix.columns)
        )
    ]

    composite = return_matrix.mul(
        weights_arr,
        axis=1,
    ).sum(
        axis=1,
        skipna=True,
    )

    return composite.sort_values(
        ascending=False
    )


# ============================================================
# CUSTOM DEFENSIVE MULTIPLIERS
# ============================================================

def apply_defensive_multipliers(
    scores,
    multipliers,
):

    adjusted = scores.copy()

    for symbol, multiplier in multipliers.items():

        if symbol in adjusted.index:

            adjusted.loc[symbol] = (
                adjusted.loc[symbol]
                * float(multiplier)
            )

    return adjusted.sort_values(
        ascending=False
    )


# ============================================================
# RANKING
# ============================================================

def rank_on_date(
    prices,
    as_of_date,
    lookbacks,
    weights,
    defensive_multipliers=None,
):

    scores = calculate_return_score(
        prices,
        as_of_date,
        lookbacks,
        weights,
    )

    if scores.empty:

        return pd.Series(dtype=float)

    if defensive_multipliers:

        scores = apply_defensive_multipliers(
            scores,
            defensive_multipliers,
        )

    ranks = scores.rank(
        ascending=False,
        method="min",
    )

    return ranks.sort_values()


# ============================================================
# REBALANCING
# ============================================================

def rebalance(
    current_portfolio,
    ranks,
    target_n,
    exit_rank,
):

    if ranks.empty:

        return (
            current_portfolio,
            [],
            [],
        )

    keep = [
        symbol
        for symbol in current_portfolio
        if (
            symbol in ranks.index
            and ranks[symbol] < exit_rank
        )
    ]

    exits = [
        symbol
        for symbol in current_portfolio
        if symbol not in keep
    ]

    needed = max(
        0,
        target_n - len(keep),
    )

    entries = []

    for symbol in ranks.index:

        if symbol not in keep:

            entries.append(symbol)

            if len(entries) >= needed:

                break

    return (
        keep + entries,
        entries,
        exits,
    )


# ============================================================
# MONTH-END REBALANCING
# ============================================================

def monthly_rebalance_dates(
    prices
):

    month_ends = (
        prices
        .resample("ME")
        .last()
        .index
    )

    dates = []

    for month_end in month_ends:

        available = prices.index[
            prices.index
            <= month_end
        ]

        if len(available):

            dates.append(
                available[-1]
            )

    return sorted(
        set(dates)
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    prices,
    lookbacks,
    weights,
    target_n,
    exit_rank,
    start_date,
    defensive_multipliers=None,
):

    rebalance_dates = (
        monthly_rebalance_dates(
            prices
        )
    )

    max_lookback = max(
        lookbacks
    )

    valid_dates = []

    for d in rebalance_dates:

        idx = prices.index.get_loc(d)

        if (
            idx >= max_lookback
            and d >= pd.Timestamp(
                start_date
            )
        ):

            valid_dates.append(d)

    portfolio = []

    rows = []

    equity = 1.0

    previous_date = None

    previous_holdings = []

    for current_date in valid_dates:

        ranks = rank_on_date(
            prices,
            current_date,
            lookbacks,
            weights,
            defensive_multipliers,
        )

        new_portfolio, entries, exits = (
            rebalance(
                portfolio,
                ranks,
                target_n,
                exit_rank,
            )
        )

        monthly_return = np.nan

        if (
            previous_date is not None
            and previous_holdings
        ):

            valid_holdings = [
                x
                for x in previous_holdings
                if x in prices.columns
            ]

            if valid_holdings:

                prev_prices = prices.loc[
                    previous_date,
                    valid_holdings,
                ]

                curr_prices = prices.loc[
                    current_date,
                    valid_holdings,
                ]

                aligned = pd.concat(
                    [
                        prev_prices.rename(
                            "prev"
                        ),
                        curr_prices.rename(
                            "curr"
                        ),
                    ],
                    axis=1,
                ).dropna()

                if not aligned.empty:

                    monthly_return = float(
                        (
                            aligned["curr"]
                            / aligned["prev"]
                            - 1
                        ).mean()
                    )

                    equity *= (
                        1 + monthly_return
                    )

        rows.append(
            {
                "Date": current_date,
                "Portfolio Size": len(
                    new_portfolio
                ),
                "Holdings": ", ".join(
                    new_portfolio
                ),
                "New Entries": ", ".join(
                    entries
                ),
                "Exits": ", ".join(
                    exits
                ),
                "Turnover": (
                    len(entries)
                    + len(exits)
                ),
                "Portfolio Return": (
                    monthly_return
                ),
                "Equity": equity,
            }
        )

        portfolio = new_portfolio

        previous_date = current_date

        previous_holdings = list(
            new_portfolio
        )

    return pd.DataFrame(rows)


# ============================================================
# METRICS
# ============================================================

def portfolio_metrics(
    history
):

    if history.empty:

        return {
            "CAGR": np.nan,
            "Max Drawdown": np.nan,
            "Avg Monthly Return": np.nan,
            "Turnover": 0,
        }

    returns = history[
        "Portfolio Return"
    ].dropna()

    equity = history[
        "Equity"
    ].dropna()

    if equity.empty:

        cagr = np.nan
        max_dd = np.nan

    else:

        periods = max(
            (
                history["Date"].iloc[-1]
                - history["Date"].iloc[0]
            ).days
            / 365.25,
            1 / 365.25,
        )

        cagr = (
            equity.iloc[-1]
            ** (1 / periods)
            - 1
        )

        peak = equity.cummax()

        drawdown = (
            equity / peak
            - 1
        )

        max_dd = drawdown.min()

    return {
        "CAGR": cagr,
        "Max Drawdown": max_dd,
        "Avg Monthly Return": (
            returns.mean()
            if not returns.empty
            else np.nan
        ),
        "Turnover": int(
            history[
                "Turnover"
            ]
            .fillna(0)
            .sum()
        ),
    }


# ============================================================
# SIDEBAR
# ============================================================

st.title(
    "📈 NSE Momentum Portfolio"
)

st.caption(
    "FYERS-powered multi-period return ranking + monthly rebalancing + NSE/custom defensive groups"
)


# ------------------------------------------------------------
# FYERS
# ------------------------------------------------------------

fyers_token = fyers_login_section()


# ------------------------------------------------------------
# STRATEGY
# ------------------------------------------------------------

with st.sidebar:

    st.header(
        "Strategy"
    )

    target_n = st.number_input(
        "Target holdings / Entry Top N",
        min_value=1,
        max_value=500,
        value=20,
        step=1,
    )

    exit_rank = st.number_input(
        "Exit when rank reaches",
        min_value=2,
        max_value=1000,
        value=41,
        step=1,
    )

    lookback_text = st.text_input(
        "Return lookback periods",
        "252,120,90,60",
    )

    weight_text = st.text_input(
        "Return weights",
        "40,30,20,10",
    )

    st.divider()

    st.header(
        "Data"
    )

    end_date = st.date_input(
        "Data end date",
        value=date.today(),
    )

    start_date = st.date_input(
        "Data start date",
        value=(
            end_date
            - timedelta(days=365 * 5)
        ),
    )

    delay = st.number_input(
        "Delay between FYERS requests",
        min_value=0.0,
        max_value=2.0,
        value=0.05,
        step=0.05,
    )


# ============================================================
# GROUP SELECTION
# ============================================================

st.sidebar.divider()

st.sidebar.header(
    "Universe Groups"
)

group_names = get_group_names()

if not group_names:

    selected_groups = []

else:

    selected_groups = st.sidebar.multiselect(
        "Select NSE / market groups",
        options=group_names,
        default=[
            g
            for g in [
                "NSE Equity",
                "Nifty 50",
            ]
            if g in group_names
        ],
    )


selected_subgroups = []

for group in selected_groups:

    subgroups = get_subgroup_names(
        group
    )

    if subgroups:

        chosen = st.sidebar.multiselect(
            f"{group} sub-groups",
            options=subgroups,
            default=[],
            key=f"subgroup_{group}",
        )

        selected_subgroups.extend(
            [
                (
                    group,
                    subgroup
                )
                for subgroup in chosen
            ]
        )


# ============================================================
# CUSTOM DEFENSIVE GROUP
# ============================================================

st.sidebar.divider()

st.sidebar.header(
    "Custom Defensive Group"
)

include_defensive = st.sidebar.checkbox(
    "Include Gold / G-Sec / LiquidBees",
    value=False,
)


defensive_multipliers = {}


if include_defensive:

    st.sidebar.caption(
        "Multiplier is applied to the return score before final ranking."
    )

    gold_weight = st.sidebar.selectbox(
        "Gold weight",
        [1.0, 2.0, 3.0],
        index=0,
    )

    gsec_weight = st.sidebar.selectbox(
        "G-Sec weight",
        [1.0, 2.0, 3.0],
        index=0,
    )

    liquid_weight = st.sidebar.selectbox(
        "LiquidBees weight",
        [1.0, 2.0, 3.0],
        index=0,
    )

    defensive_multipliers = {
        "GOLDBEES": gold_weight,
        "GSEC10YEAR": gsec_weight,
        "LIQUIDBEES": liquid_weight,
    }


# ============================================================
# BUILD UNIVERSE
# ============================================================

universe = []


for group in selected_groups:

    # If subgroups have explicitly been selected,
    # use those.
    group_subgroups = [
        subgroup
        for g, subgroup
        in selected_subgroups
        if g == group
    ]

    if group_subgroups:

        for subgroup in group_subgroups:

            universe.extend(
                get_symbols_for_group(
                    group,
                    subgroup,
                )
            )

    else:

        universe.extend(
            get_symbols_for_group(
                group
            )
        )


if include_defensive:

    universe.extend(
        CUSTOM_DEFENSIVE
    )


universe = normalize_symbols(
    universe
)


# Fallback when no catalog has
# yet been populated.

if not universe:

    universe = DEFAULT_UNIVERSE.copy()

    if include_defensive:

        universe.extend(
            CUSTOM_DEFENSIVE
        )

    universe = normalize_symbols(
        universe
    )


# ============================================================
# VALIDATE STRATEGY
# ============================================================

try:

    lookbacks = parse_int_list(
        lookback_text
    )

    if not lookbacks:

        raise ValueError(
            "Enter at least one lookback period."
        )

    weights = parse_float_list(
        weight_text
    )

    if len(weights) != len(
        lookbacks
    ):

        raise ValueError(
            "Number of weights must match number of lookbacks."
        )

except ValueError as exc:

    st.error(str(exc))

    st.stop()


if exit_rank <= target_n:

    st.warning(
        "Exit rank is normally kept above Target N to create a holding buffer."
    )


# ============================================================
# UNIVERSE SUMMARY
# ============================================================

st.info(
    f"""
**Universe:** {len(universe)} securities  
**Entry:** Top {target_n}  
**Exit buffer:** Rank {exit_rank}+  
**Lookbacks:** {", ".join(map(str, lookbacks))} days  
**Defensive group:** {"ON" if include_defensive else "OFF"}
"""
)


# ============================================================
# RUN
# ============================================================

if st.button(
    "▶ Run / Update Data",
    type="primary",
    use_container_width=True,
):

    if not fyers_token:

        st.error(
            "Connect to FYERS before running the strategy."
        )

        st.stop()

    if pd.Timestamp(
        start_date
    ) >= pd.Timestamp(
        end_date
    ):

        st.error(
            "Data start date must be before data end date."
        )

        st.stop()

    with st.status(
        "Updating data and running strategy...",
        expanded=True,
    ) as status:

        st.write(
            f"Requesting FYERS historical data for {len(universe)} securities..."
        )

        prices, unavailable = fetch_prices(
            fyers_token,
            tuple(universe),
            str(start_date),
            str(end_date),
            float(delay),
        )

        if prices.empty:

            status.update(
                label="FYERS data failed",
                state="error",
            )

            st.error(
                "FYERS returned no historical data."
            )

            st.stop()

        st.write(
            f"Received data for {len(prices.columns)} securities."
        )

        if unavailable:

            st.warning(
                f"{len(unavailable)} securities returned no FYERS history."
            )

        st.write(
            "Calculating return scores, rankings and monthly portfolio changes..."
        )

        history = run_backtest(
            prices,
            lookbacks,
            weights,
            int(target_n),
            int(exit_rank),
            str(start_date),
            defensive_multipliers,
        )

        st.session_state.update(
            prices=prices,
            history=history,
            unavailable=unavailable,
            universe=universe,
            defensive_multipliers=(
                defensive_multipliers
            ),
            last_config={
                "target_n": int(target_n),
                "exit_rank": int(exit_rank),
                "lookbacks": lookbacks,
                "weights": weights,
            },
        )

        status.update(
            label="Done",
            state="complete",
            expanded=False,
        )


# ============================================================
# RESULTS
# ============================================================

if "history" not in st.session_state:

    st.warning(
        "Select your universe and strategy settings, connect FYERS, then click Run / Update Data."
    )

    st.stop()


prices = st.session_state[
    "prices"
]

history = st.session_state[
    "history"
]

unavailable = st.session_state.get(
    "unavailable",
    []
)


# ============================================================
# METRICS
# ============================================================

metrics = portfolio_metrics(
    history
)

latest_date = prices.index[-1].date()


m1, m2, m3, m4, m5 = st.columns(5)


m1.metric(
    "Latest data",
    latest_date.strftime(
        "%d %b %Y"
    ),
)

m2.metric(
    "Securities",
    len(prices.columns),
)

m3.metric(
    "CAGR",
    (
        "—"
        if pd.isna(
            metrics["CAGR"]
        )
        else f"{metrics['CAGR']:.1%}"
    ),
)

m4.metric(
    "Max drawdown",
    (
        "—"
        if pd.isna(
            metrics["Max Drawdown"]
        )
        else f"{metrics['Max Drawdown']:.1%}"
    ),
)

m5.metric(
    "Turnover",
    f"{metrics['Turnover']}",
)


# ============================================================
# CURRENT RANKING
# ============================================================

latest_ranks = rank_on_date(
    prices,
    latest_date,
    lookbacks,
    weights,
    defensive_multipliers,
)


ranking_view = pd.DataFrame(
    {
        "Symbol":
            latest_ranks.index,
        "Rank":
            latest_ranks.values,
    }
)


ranking_view[
    "Name"
] = ranking_view[
    "Symbol"
].map(
    lambda x:
        get_asset_metadata(x)[
            "name"
        ]
)


ranking_view[
    "Group"
] = ranking_view[
    "Symbol"
].map(
    lambda x:
        get_asset_metadata(x)[
            "group"
        ]
)


ranking_view[
    "Sub Group"
] = ranking_view[
    "Symbol"
].map(
    lambda x:
        get_asset_metadata(x)[
            "sub_group"
        ]
)


ranking_view[
    "Asset Type"
] = ranking_view[
    "Symbol"
].map(
    lambda x:
        get_asset_metadata(x)[
            "asset_type"
        ]
)


ranking_view[
    "Zone"
] = np.select(
    [
        ranking_view[
            "Rank"
        ] <= target_n,

        ranking_view[
            "Rank"
        ] < exit_rank,
    ],
    [
        "ENTRY / TOP HOLDINGS",
        "BUFFER / HOLD",
    ],
    default="EXIT ZONE",
)


st.subheader(
    f"Current Ranking — {latest_date}"
)


st.dataframe(
    ranking_view.head(
        max(
            int(exit_rank),
            int(target_n)
        ) + 20
    ),
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# LATEST REBALANCE
# ============================================================

if not history.empty:

    latest_rebalance = history.iloc[-1]

    st.subheader(
        "Latest Monthly Rebalance"
    )

    left, mid, right = st.columns(3)

    with left:

        st.markdown(
            "**Current holdings**"
        )

        holdings = (
            latest_rebalance[
                "Holdings"
            ].split(", ")
            if latest_rebalance[
                "Holdings"
            ]
            else []
        )

        st.dataframe(
            pd.DataFrame(
                {
                    "Holding":
                        holdings
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with mid:

        st.markdown(
            "**New entries**"
        )

        entries = (
            latest_rebalance[
                "New Entries"
            ].split(", ")
            if latest_rebalance[
                "New Entries"
            ]
            else []
        )

        st.dataframe(
            pd.DataFrame(
                {
                    "New entry":
                        entries
                }
            ),
            use_container_width=True,
            hide_index=True,
        )

    with right:

        st.markdown(
            "**Exits**"
        )

        exits = (
            latest_rebalance[
                "Exits"
            ].split(", ")
            if latest_rebalance[
                "Exits"
            ]
            else []
        )

        st.dataframe(
            pd.DataFrame(
                {
                    "Exit":
                        exits
                }
            ),
            use_container_width=True,
            hide_index=True,
        )


# ============================================================
# BACKTEST HISTORY
# ============================================================

st.subheader(
    "Backtest / Rebalance History"
)


history_view = history.copy()


history_view[
    "Date"
] = pd.to_datetime(
    history_view["Date"]
).dt.date


history_view[
    "Portfolio Return"
] = history_view[
    "Portfolio Return"
].apply(
    lambda x:
        None
        if pd.isna(x)
        else f"{x:.2%}"
)


history_view[
    "Equity"
] = history_view[
    "Equity"
].apply(
    lambda x:
        f"{x:.3f}"
)


st.dataframe(
    history_view,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# GROUP BREAKDOWN
# ============================================================

st.subheader(
    "Universe Group Breakdown"
)


group_breakdown = (
    ranking_view
    .groupby(
        [
            "Group",
            "Sub Group",
        ],
        dropna=False,
    )
    .size()
    .reset_index(
        name="Securities"
    )
)


st.dataframe(
    group_breakdown,
    use_container_width=True,
    hide_index=True,
)


# ============================================================
# DEFENSIVE SETTINGS
# ============================================================

if include_defensive:

    st.subheader(
        "Custom Defensive Group"
    )

    defensive_rows = []

    for symbol, multiplier in (
        defensive_multipliers.items()
    ):

        metadata = get_asset_metadata(
            symbol
        )

        defensive_rows.append(
            {
                "Symbol":
                    symbol,

                "Name":
                    metadata[
                        "name"
                    ],

                "Group":
                    "Custom Defensive",

                "Type":
                    metadata[
                        "sub_group"
                    ],

                "Ranking Weight":
                    f"{multiplier:.1f}x",
            }
        )

    st.dataframe(
        pd.DataFrame(
            defensive_rows
        ),
        use_container_width=True,
        hide_index=True,
    )


# ============================================================
# UNAVAILABLE DATA
# ============================================================

if unavailable:

    with st.expander(
        "FYERS symbols with unavailable data"
    ):

        st.write(
            unavailable
        )


# ============================================================
# DOWNLOAD
# ============================================================

csv_history = history.to_csv(
    index=False
).encode(
    "utf-8"
)


st.download_button(
    "Download rebalance history CSV",
    data=csv_history,
    file_name=(
        "momentum_rebalance_history.csv"
    ),
    mime="text/csv",
)


ranking_csv = ranking_view.to_csv(
    index=False
).encode(
    "utf-8"
)


st.download_button(
    "Download current ranking CSV",
    data=ranking_csv,
    file_name=(
        "current_momentum_ranking.csv"
    ),
    mime="text/csv",
)


# ============================================================
# FOOTER
# ============================================================

st.caption(
    "Market data: FYERS API. "
    "Research/backtesting tool only. "
    "No order placement is implemented."
)
