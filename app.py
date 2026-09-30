import io
import os
import zipfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import streamlit as st

from fyers_data import (
    build_login_url,
    clean_symbol,
    credentials_available,
    exchange_auth_code,
    fetch_symbol_history,
    get_access_token,
)

from universe_manager import (
    ensure_data_directories,
    load_current_universe,
    load_historical_constituents,
    load_price_data,
    load_index_data,
    save_price_data,
    save_index_data,
    get_saved_date_range,
)

from backtest_engine import (
    calculate_market_entry_control,
    run_backtest,
)


# ============================================================
# PAGE
# ============================================================

st.set_page_config(
    page_title="FYERS Momentum Portfolio",
    page_icon="📈",
    layout="wide"
)

ensure_data_directories()


# ============================================================
# TITLE
# ============================================================

st.title("📈 FYERS Momentum Portfolio & Backtest")

st.caption(
    "FYERS historical data • Point-in-time universe • "
    "Momentum ranking • Market entry control • Backtesting"
)


# ============================================================
# SESSION
# ============================================================

if "access_token" not in st.session_state:
    st.session_state.access_token = get_access_token()

if "fyers_login" not in st.session_state:
    st.session_state.fyers_login = False


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Settings")

    st.subheader("FYERS")

    if credentials_available():

        st.success(
            "FYERS credentials detected"
        )

        if st.session_state.access_token:

            st.success(
                "Access token available"
            )

        else:

            st.warning(
                "Login required"
            )

            login_url = build_login_url()

            if login_url:

                st.link_button(
                    "🔐 Login to FYERS",
                    login_url
                )

    else:

        st.error(
            "FYERS credentials missing"
        )

        st.info(
            "Add FYERS_APP_ID, FYERS_SECRET_ID "
            "and FYERS_REDIRECT_URI to Streamlit secrets."
        )

    st.divider()

    st.subheader("Backtest")

    start_date = st.date_input(
        "Start Date",
        value=date(
            2020,
            1,
            1
        )
    )

    end_date = st.date_input(
        "End Date",
        value=date.today()
    )


# ============================================================
# FYERS CALLBACK
# ============================================================

query_params = st.query_params

auth_code = query_params.get(
    "auth_code"
)

if auth_code:

    try:

        token = exchange_auth_code(
            auth_code
        )

        st.session_state.access_token = token

        st.success(
            "FYERS login successful."
        )

        st.query_params.clear()

    except Exception as e:

        st.error(
            f"FYERS login failed: {e}"
        )


# ============================================================
# LOAD UNIVERSE
# ============================================================

current_universe = load_current_universe()

historical_universe = load_historical_constituents()

if current_universe.empty:

    st.warning(
        "universe_groups.csv is empty."
    )


# ============================================================
# TABS
# ============================================================

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "📊 Strategy",
        "📥 Data Manager",
        "🧪 Backtest",
        "📚 Universe"
    ]
)


# ============================================================
# TAB 1 — STRATEGY
# ============================================================

with tab1:

    st.header(
        "Momentum Strategy"
    )

    col1, col2, col3 = st.columns(3)

    with col1:

        groups = ["All"]

        if not current_universe.empty:
            groups += sorted(
                current_universe[
                    "group"
                ]
                .dropna()
                .astype(str)
                .unique()
                .tolist()
            )

        selected_group = st.selectbox(
            "Universe / Group",
            groups
        )

    with col2:

        top_n = st.number_input(
            "Top N Holdings",
            min_value=1,
            max_value=100,
            value=10
        )

    with col3:

        rebalance_frequency = st.selectbox(
            "Rebalance",
            [
                "Monthly",
                "Quarterly"
            ]
        )

    st.subheader(
        "Return Ranking"
    )

    c1, c2, c3, c4 = st.columns(4)

    with c1:
        lb1 = st.number_input(
            "Lookback 1",
            min_value=20,
            max_value=500,
            value=252
        )

    with c2:
        lb2 = st.number_input(
            "Lookback 2",
            min_value=20,
            max_value=500,
            value=120
        )

    with c3:
        lb3 = st.number_input(
            "Lookback 3",
            min_value=20,
            max_value=500,
            value=90
        )

    with c4:
        lb4 = st.number_input(
            "Lookback 4",
            min_value=20,
            max_value=500,
            value=60
        )

    st.write(
        "Lookback Weights"
    )

    w1, w2, w3, w4 = st.columns(4)

    with w1:
        weight1 = st.number_input(
            "252D Weight",
            min_value=0.0,
            max_value=10.0,
            value=0.40
        )

    with w2:
        weight2 = st.number_input(
            "120D Weight",
            min_value=0.0,
            max_value=10.0,
            value=0.30
        )

    with w3:
        weight3 = st.number_input(
            "90D Weight",
            min_value=0.0,
            max_value=10.0,
            value=0.20
        )

    with w4:
        weight4 = st.number_input(
            "60D Weight",
            min_value=0.0,
            max_value=10.0,
            value=0.10
        )

    volatility_weight = st.number_input(
        "Volatility Penalty",
        min_value=0.0,
        max_value=10.0,
        value=0.0,
        step=0.05
    )

    st.divider()

    # ========================================================
    # DEFENSIVE
    # ========================================================

    st.subheader(
        "🛡️ Defensive Group"
    )

    defensive_enabled = st.checkbox(
        "Enable Defensive Assets",
        value=True
    )

    defensive_multipliers = {}

    if defensive_enabled:

        d1, d2, d3 = st.columns(3)

        with d1:

            gold_multiplier = st.selectbox(
                "Gold",
                [1.0, 2.0, 3.0],
                index=0
            )

        with d2:

            gsec_multiplier = st.selectbox(
                "G-Sec",
                [1.0, 2.0, 3.0],
                index=0
            )

        with d3:

            liquid_multiplier = st.selectbox(
                "LiquidBees",
                [1.0, 2.0, 3.0],
                index=0
            )

        defensive_multipliers = {
            "GOLDBEES":
                gold_multiplier,

            "GSEC":
                gsec_multiplier,

            "LIQUIDBEES":
                liquid_multiplier,
        }

    st.divider()

    # ========================================================
    # MARKET ENTRY CONTROL
    # ========================================================

    st.subheader(
        "🚦 Market Entry Control"
    )

    market_control_enabled = st.checkbox(
        "Enable Market Entry Control",
        value=False
    )

    market_control_df = pd.DataFrame()

    if market_control_enabled:

        mc1, mc2 = st.columns(2)

        with mc1:

            index_options = [
                "NIFTY 50",
                "NIFTY 100",
                "NIFTY 200",
                "NIFTY 500",
                "BANK NIFTY",
            ]

            selected_index = st.selectbox(
                "Control Index",
                index_options
            )

        with mc2:

            indicator_type = st.selectbox(
                "Indicator",
                [
                    "EMA",
                    "SMA",
                    "VSTOP"
                ]
            )

        if indicator_type in [
            "EMA",
            "SMA"
        ]:

            period = st.number_input(
                "Moving Average Period",
                min_value=2,
                max_value=500,
                value=200
            )

            atr_period = 14
            atr_multiplier = 2.0

        else:

            period = 200

            atr_period = st.number_input(
                "ATR Period",
                min_value=2,
                max_value=100,
                value=14
            )

            atr_multiplier = st.number_input(
                "VStop ATR Multiplier",
                min_value=0.1,
                max_value=10.0,
                value=2.0,
                step=0.1
            )

        st.info(
            "When the selected index is below the selected "
            "indicator, new entries are blocked. Existing "
            "stocks continue to follow normal exit rules."
        )


# ============================================================
# TAB 2 — DATA MANAGER
# ============================================================

with tab2:

    st.header(
        "📥 FYERS Historical Data Manager"
    )

    st.write(
        "Download historical OHLCV data from FYERS and "
        "store it locally for repeated backtesting."
    )

    if not st.session_state.access_token:

        st.warning(
            "Login to FYERS before downloading data."
        )

    else:

        if current_universe.empty:

            st.error(
                "No symbols found in universe_groups.csv"
            )

        else:

            all_symbols = (
                current_universe[
                    "symbol"
                ]
                .dropna()
                .astype(str)
                .str.upper()
                .unique()
                .tolist()
            )

            data_mode = st.radio(
                "Download Mode",
                [
                    "Single Symbol",
                    "Entire Group"
                ],
                horizontal=True
            )

            if data_mode == "Single Symbol":

                selected_symbol = st.selectbox(
                    "Symbol",
                    all_symbols
                )

                symbols_to_download = [
                    selected_symbol
                ]

            else:

                data_group = st.selectbox(
                    "Group",
                    sorted(
                        current_universe[
                            "group"
                        ]
                        .dropna()
                        .astype(str)
                        .unique()
                        .tolist()
                    )
                )

                symbols_to_download = (
                    current_universe[
                        current_universe[
                            "group"
                        ].astype(str)
                        == data_group
                    ]["symbol"]
                    .dropna()
                    .astype(str)
                    .str.upper()
                    .unique()
                    .tolist()
                )

            d1, d2 = st.columns(2)

            with d1:

                download_start = st.date_input(
                    "Historical Start",
                    value=date(
                        2018,
                        1,
                        1
                    ),
                    key="download_start"
                )

            with d2:

                download_end = st.date_input(
                    "Historical End",
                    value=date.today(),
                    key="download_end"
                )

            if st.button(
                "⬇️ Fetch & Store Historical Data",
                type="primary"
            ):

                progress = st.progress(0)

                status = st.empty()

                successful = []
                failed = []

                total = len(
                    symbols_to_download
                )

                for i, symbol in enumerate(
                    symbols_to_download
                ):

                    status.write(
                        f"Downloading {symbol}..."
                    )

                    try:

                        df = fetch_symbol_history(
                            symbol=symbol,
                            start_date=download_start,
                            end_date=download_end,
                            resolution="1D",
                            access_token=
                                st.session_state.access_token
                        )

                        if df.empty:

                            failed.append(
                                (
                                    symbol,
                                    "No data returned"
                                )
                            )

                        else:

                            save_price_data(
                                symbol,
                                df
                            )

                            successful.append(
                                symbol
                            )

                    except Exception as e:

                        failed.append(
                            (
                                symbol,
                                str(e)
                            )
                        )

                    progress.progress(
                        int(
                            ((i + 1) / total)
                            * 100
                        )
                    )

                status.empty()

                st.success(
                    f"Stored {len(successful)} symbols."
                )

                if failed:

                    st.warning(
                        f"{len(failed)} symbols failed."
                    )

                    st.dataframe(
                        pd.DataFrame(
                            failed,
                            columns=[
                                "symbol",
                                "error"
                            ]
                        ),
                        use_container_width=True
                    )

            st.divider()

            st.subheader(
                "Stored Data"
            )

            rows = []

            for symbol in all_symbols:

                first, last = get_saved_date_range(
                    symbol
                )

                if first is not None:

                    rows.append({
                        "Symbol":
                            symbol,
                        "Start":
                            first.date(),
                        "End":
                            last.date(),
                    })

            if rows:

                stored_df = pd.DataFrame(
                    rows
                )

                st.dataframe(
                    stored_df,
                    use_container_width=True
                )

                csv = stored_df.to_csv(
                    index=False
                ).encode()

                st.download_button(
                    "📥 Download Data Inventory",
                    csv,
                    "data_inventory.csv",
                    "text/csv"
                )

            else:

                st.info(
                    "No historical data stored yet."
                )


# ============================================================
# TAB 3 — BACKTEST
# ============================================================

with tab3:

    st.header(
        "🧪 Backtest"
    )

    if not st.session_state.access_token:

        st.warning(
            "Login to FYERS first."
        )

    elif current_universe.empty:

        st.error(
            "Universe is empty."
        )

    else:

        if st.button(
            "▶️ Run Backtest",
            type="primary"
        ):

            with st.spinner(
                "Preparing backtest..."
            ):

                # ------------------------------------------------
                # REBALANCE DATES
                # ------------------------------------------------

                dates = pd.date_range(
                    start=start_date,
                    end=end_date,
                    freq=(
                        "ME"
                        if rebalance_frequency
                        == "Monthly"
                        else "QE"
                    )
                )

                # ------------------------------------------------
                # LOAD PRICE DATA
                # ------------------------------------------------

                symbols = (
                    current_universe[
                        "symbol"
                    ]
                    .dropna()
                    .astype(str)
                    .str.upper()
                    .unique()
                    .tolist()
                )

                price_data = {}

                missing = []

                for symbol in symbols:

                    df = load_price_data(
                        symbol
                    )

                    if df.empty:

                        missing.append(
                            symbol
                        )

                    else:

                        price_data[
                            symbol
                        ] = df

                if not price_data:

                    st.error(
                        "No stored historical data found. "
                        "Go to Data Manager and download FYERS data first."
                    )

                    st.stop()

                # ------------------------------------------------
                # MARKET INDEX
                # ------------------------------------------------

                if market_control_enabled:

                    index_symbol = clean_symbol(
                        selected_index
                    )

                    index_df = load_index_data(
                        selected_index
                    )

                    # If index is not already stored,
                    # fetch it automatically.
                    if index_df.empty:

                        st.info(
                            f"Downloading {selected_index} "
                            "historical index data..."
                        )

                        index_df = fetch_symbol_history(
                            symbol=index_symbol,
                            start_date=start_date,
                            end_date=end_date,
                            resolution="1D",
                            access_token=
                                st.session_state.access_token
                        )

                        if not index_df.empty:

                            save_index_data(
                                selected_index,
                                index_df
                            )

                    if index_df.empty:

                        st.error(
                            "Unable to obtain index data."
                        )

                        st.stop()

                    market_control_df = (
                        calculate_market_entry_control(
                            index_df,
                            indicator_type=
                                indicator_type,
                            period=period,
                            atr_period=atr_period,
                            atr_multiplier=
                                atr_multiplier
                        )
                    )

                else:

                    market_control_df = pd.DataFrame()

                # ------------------------------------------------
                # RUN ENGINE
                # ------------------------------------------------

                history_df, transactions_df = (
                    run_backtest(
                        price_data=price_data,
                        rebalance_dates=dates,
                        universe_df=
                            historical_universe,
                        selected_group=
                            selected_group,
                        top_n=top_n,
                        lookbacks=(
                            lb1,
                            lb2,
                            lb3,
                            lb4
                        ),
                        weights=(
                            weight1,
                            weight2,
                            weight3,
                            weight4
                        ),
                        volatility_weight=
                            volatility_weight,
                        defensive_multipliers=
                            defensive_multipliers,
                        market_control_df=
                            market_control_df,
                        allow_market_entry_control=
                            market_control_enabled,
                    )
                )

                # ------------------------------------------------
                # RESULTS
                # ------------------------------------------------

                st.success(
                    "Backtest completed."
                )

                if not history_df.empty:

                    st.subheader(
                        "Monthly Portfolio"
                    )

                    st.dataframe(
                        history_df,
                        use_container_width=True
                    )

                    csv = history_df.to_csv(
                        index=False
                    ).encode()

                    st.download_button(
                        "📥 Download Portfolio History",
                        csv,
                        "portfolio_history.csv",
                        "text/csv"
                    )

                if not transactions_df.empty:

                    st.subheader(
                        "Entries & Exits"
                    )

                    st.dataframe(
                        transactions_df,
                        use_container_width=True
                    )

                    csv = transactions_df.to_csv(
                        index=False
                    ).encode()

                    st.download_button(
                        "📥 Download Transactions",
                        csv,
                        "transactions.csv",
                        "text/csv"
                    )

                if market_control_enabled:

                    st.subheader(
                        "🚦 Market Entry Control"
                    )

                    st.dataframe(
                        market_control_df.tail(100),
                        use_container_width=True
                    )

                    st.caption(
                        "ENTRY BLOCKED means new stocks "
                        "were not permitted. Existing "
                        "holdings continued under normal "
                        "exit logic."
                    )

                if missing:

                    st.warning(
                        f"{len(missing)} symbols have no "
                        "stored historical data."
                    )


# ============================================================
# TAB 4 — UNIVERSE
# ============================================================

with tab4:

    st.header(
        "📚 Universe Management"
    )

    st.subheader(
        "Current Universe"
    )

    if current_universe.empty:

        st.info(
            "No universe data."
        )

    else:

        st.dataframe(
            current_universe,
            use_container_width=True
        )

    st.subheader(
        "Historical Constituents"
    )

    if historical_universe.empty:

        st.warning(
            "historical_constituents.csv is empty."
        )

        st.info(
            "For rigorous historical backtesting, "
            "populate this file with the actual "
            "constituent validity periods."
        )

    else:

        st.dataframe(
            historical_universe,
            use_container_width=True
        )

        st.caption(
            "The backtest uses valid_from and valid_to "
            "to determine which securities were eligible "
            "on each historical rebalance date."
        )
