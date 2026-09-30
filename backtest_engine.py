import numpy as np
import pandas as pd


# ============================================================
# TECHNICAL INDICATORS
# ============================================================

def moving_average(series, period, ma_type="EMA"):

    period = int(period)

    if ma_type.upper() == "SMA":
        return series.rolling(period).mean()

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


def atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return true_range.rolling(period).mean()


def volatility_stop(
    df,
    atr_period=14,
    multiplier=2.0
):
    """
    Long-side volatility stop.

    A simplified Chandelier-style trailing stop.
    """

    a = atr(
        df,
        period=atr_period
    )

    highest_high = df["high"].rolling(
        atr_period
    ).max()

    stop = highest_high - (
        a * float(multiplier)
    )

    return stop


# ============================================================
# MARKET ENTRY CONTROL
# ============================================================

def calculate_market_entry_control(
    index_df,
    indicator_type="EMA",
    period=200,
    atr_period=14,
    atr_multiplier=2.0,
):
    """
    Returns a DataFrame containing:
        close
        indicator
        entry_allowed
        market_status
    """

    if index_df is None or index_df.empty:
        return pd.DataFrame()

    df = index_df.copy()

    df["date"] = pd.to_datetime(
        df["date"]
    ).dt.normalize()

    df = df.sort_values("date")

    indicator_type = indicator_type.upper()

    if indicator_type in ["EMA", "SMA"]:

        df["indicator"] = moving_average(
            df["close"],
            period=period,
            ma_type=indicator_type
        )

        # Entry allowed when index is ABOVE MA
        df["entry_allowed"] = (
            df["close"] > df["indicator"]
        )

        df["market_status"] = np.where(
            df["entry_allowed"],
            "ENTRY ALLOWED",
            "ENTRY BLOCKED"
        )

    elif indicator_type in [
        "VSTOP",
        "VOLATILITY STOP"
    ]:

        df["indicator"] = volatility_stop(
            df,
            atr_period=atr_period,
            multiplier=atr_multiplier
        )

        df["entry_allowed"] = (
            df["close"] > df["indicator"]
        )

        df["market_status"] = np.where(
            df["entry_allowed"],
            "ENTRY ALLOWED",
            "ENTRY BLOCKED"
        )

    else:

        df["indicator"] = np.nan
        df["entry_allowed"] = True
        df["market_status"] = "ENTRY ALLOWED"

    return df[
        [
            "date",
            "close",
            "indicator",
            "entry_allowed",
            "market_status"
        ]
    ]


def market_entry_allowed(
    control_df,
    date
):

    if control_df is None or control_df.empty:
        return True

    date = pd.Timestamp(date).normalize()

    eligible = control_df[
        control_df["date"] <= date
    ]

    if eligible.empty:
        return True

    latest = eligible.iloc[-1]

    return bool(
        latest["entry_allowed"]
    )


# ============================================================
# POINT-IN-TIME UNIVERSE
# ============================================================

def get_point_in_time_universe(
    universe_df,
    date,
    selected_group=None
):

    if universe_df is None or universe_df.empty:
        return []

    df = universe_df.copy()

    df["valid_from"] = pd.to_datetime(
        df["valid_from"],
        errors="coerce"
    )

    df["valid_to"] = pd.to_datetime(
        df["valid_to"],
        errors="coerce"
    )

    date = pd.Timestamp(date).normalize()

    mask = (
        (df["valid_from"] <= date) &
        (
            df["valid_to"].isna() |
            (df["valid_to"] >= date)
        )
    )

    df = df[mask]

    if selected_group and selected_group != "All":

        if "group" in df.columns:

            df = df[
                df["group"].astype(str)
                .str.lower()
                ==
                str(selected_group).lower()
            ]

    if "symbol" not in df.columns:
        return []

    return (
        df["symbol"]
        .dropna()
        .astype(str)
        .str.upper()
        .unique()
        .tolist()
    )


# ============================================================
# RETURN + VOLATILITY SCORE
# ============================================================

def calculate_stock_score(
    price_df,
    date,
    lookbacks,
    weights,
    volatility_weight=0.0
):

    if price_df is None or price_df.empty:
        return None

    df = price_df.copy()

    df["date"] = pd.to_datetime(
        df["date"]
    ).dt.normalize()

    df = df.sort_values("date")

    df = df[
        df["date"] <= pd.Timestamp(date).normalize()
    ]

    if df.empty:
        return None

    latest = df.iloc[-1]

    latest_close = latest["close"]

    if pd.isna(latest_close) or latest_close <= 0:
        return None

    score = 0.0
    total_weight = 0.0

    for lookback, weight in zip(
        lookbacks,
        weights
    ):

        lookback = int(lookback)
        weight = float(weight)

        if len(df) <= lookback:
            continue

        old_close = df.iloc[-lookback - 1]["close"]

        if pd.isna(old_close) or old_close <= 0:
            continue

        ret = (
            latest_close / old_close
        ) - 1.0

        score += ret * weight
        total_weight += weight

    if total_weight > 0:
        score = score / total_weight

    # Volatility
    returns = df["close"].pct_change()

    recent_returns = returns.tail(60)

    volatility = recent_returns.std()

    if pd.isna(volatility):
        volatility = 0.0

    # Higher return + lower volatility
    final_score = (
        score -
        float(volatility_weight) * volatility
    )

    return {
        "return_score": score,
        "volatility": volatility,
        "score": final_score,
        "price": latest_close,
    }


# ============================================================
# PORTFOLIO BACKTEST
# ============================================================

def run_backtest(
    price_data,
    rebalance_dates,
    universe_df,
    selected_group="All",
    top_n=10,
    lookbacks=(252, 120, 90, 60),
    weights=(0.40, 0.30, 0.20, 0.10),
    volatility_weight=0.0,
    defensive_multipliers=None,
    market_control_df=None,
    allow_market_entry_control=True,
):

    if defensive_multipliers is None:
        defensive_multipliers = {}

    holdings = set()

    history = []
    transactions = []

    for rebalance_date in rebalance_dates:

        date = pd.Timestamp(
            rebalance_date
        ).normalize()

        # ----------------------------------------------------
        # POINT-IN-TIME UNIVERSE
        # ----------------------------------------------------

        eligible_symbols = get_point_in_time_universe(
            universe_df,
            date,
            selected_group
        )

        # ----------------------------------------------------
        # MARKET ENTRY CONTROL
        # ----------------------------------------------------

        if allow_market_entry_control:

            entry_allowed = market_entry_allowed(
                market_control_df,
                date
            )

        else:

            entry_allowed = True

        # ----------------------------------------------------
        # SCORE STOCKS
        # ----------------------------------------------------

        candidates = []

        for symbol in eligible_symbols:

            df = price_data.get(symbol)

            if df is None or df.empty:
                continue

            result = calculate_stock_score(
                df,
                date,
                lookbacks,
                weights,
                volatility_weight
            )

            if result is None:
                continue

            multiplier = defensive_multipliers.get(
                symbol,
                1.0
            )

            result["weighted_score"] = (
                result["score"] *
                float(multiplier)
            )

            result["symbol"] = symbol

            candidates.append(result)

        score_df = pd.DataFrame(
            candidates
        )

        if score_df.empty:

            new_holdings = set(holdings)

        elif entry_allowed:

            score_df = score_df.sort_values(
                "weighted_score",
                ascending=False
            )

            new_holdings = set(
                score_df.head(top_n)["symbol"]
            )

        else:

            # =================================================
            # ONLY EXITS — NO NEW ENTRIES
            # =================================================

            new_holdings = set(holdings)

        # ----------------------------------------------------
        # NORMAL EXITS
        # ----------------------------------------------------

        exited = sorted(
            holdings - new_holdings
        )

        entered = sorted(
            new_holdings - holdings
        )

        # ----------------------------------------------------
        # TRANSACTIONS
        # ----------------------------------------------------

        for symbol in exited:

            transactions.append({
                "date": date,
                "symbol": symbol,
                "action": "EXIT",
                "reason": (
                    "Normal portfolio exit"
                ),
                "market_entry_allowed":
                    entry_allowed,
            })

        for symbol in entered:

            transactions.append({
                "date": date,
                "symbol": symbol,
                "action": "ENTRY",
                "reason": (
                    "Normal ranking entry"
                ),
                "market_entry_allowed":
                    entry_allowed,
            })

        holdings = new_holdings

        # ----------------------------------------------------
        # MONTHLY SNAPSHOT
        # ----------------------------------------------------

        history.append({
            "date": date,
            "market_status": (
                "ENTRY ALLOWED"
                if entry_allowed
                else "ENTRY BLOCKED"
            ),
            "entry_allowed": entry_allowed,
            "holdings": ", ".join(
                sorted(holdings)
            ),
            "entered": ", ".join(
                entered
            ),
            "exited": ", ".join(
                exited
            ),
            "number_of_holdings": len(
                holdings
            ),
        })

    return (
        pd.DataFrame(history),
        pd.DataFrame(transactions)
    )


# ============================================================
# SIMPLE PERFORMANCE CALCULATIONS
# ============================================================

def calculate_performance(
    equity_curve
):

    if equity_curve is None or equity_curve.empty:
        return {}

    df = equity_curve.copy()

    df["date"] = pd.to_datetime(
        df["date"]
    )

    df = df.sort_values("date")

    if "portfolio_value" not in df.columns:
        return {}

    initial = float(
        df["portfolio_value"].iloc[0]
    )

    final = float(
        df["portfolio_value"].iloc[-1]
    )

    years = (
        (
            df["date"].iloc[-1]
            -
            df["date"].iloc[0]
        ).days / 365.25
    )

    if initial <= 0 or years <= 0:
        cagr = np.nan

    else:

        cagr = (
            final / initial
        ) ** (
            1 / years
        ) - 1

    running_max = (
        df["portfolio_value"]
        .cummax()
    )

    drawdown = (
        df["portfolio_value"]
        /
        running_max
    ) - 1

    max_drawdown = drawdown.min()

    return {
        "initial_value": initial,
        "final_value": final,
        "cagr": cagr,
        "max_drawdown": max_drawdown,
    }
