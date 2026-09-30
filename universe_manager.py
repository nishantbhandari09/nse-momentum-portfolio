import os
import pandas as pd


DATA_DIR = "data"
UNIVERSE_DIR = os.path.join(
    DATA_DIR,
    "universe"
)

PRICES_DIR = os.path.join(
    DATA_DIR,
    "prices"
)

INDICES_DIR = os.path.join(
    DATA_DIR,
    "indices"
)


def ensure_data_directories():

    os.makedirs(
        UNIVERSE_DIR,
        exist_ok=True
    )

    os.makedirs(
        PRICES_DIR,
        exist_ok=True
    )

    os.makedirs(
        INDICES_DIR,
        exist_ok=True
    )


# ============================================================
# HISTORICAL CONSTITUENTS
# ============================================================

def load_historical_constituents(
    path="historical_constituents.csv"
):

    if not os.path.exists(path):

        return pd.DataFrame(
            columns=[
                "symbol",
                "group",
                "valid_from",
                "valid_to",
            ]
        )

    df = pd.read_csv(path)

    required = [
        "symbol",
        "group",
        "valid_from",
        "valid_to",
    ]

    for col in required:

        if col not in df.columns:
            df[col] = None

    df["symbol"] = (
        df["symbol"]
        .astype(str)
        .str.upper()
        .str.strip()
    )

    df["valid_from"] = pd.to_datetime(
        df["valid_from"],
        errors="coerce"
    )

    df["valid_to"] = pd.to_datetime(
        df["valid_to"],
        errors="coerce"
    )

    return df


# ============================================================
# CURRENT UNIVERSE FALLBACK
# ============================================================

def load_current_universe(
    path="universe_groups.csv"
):

    if not os.path.exists(path):

        return pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "group",
                "sub_group",
                "asset_type",
                "fyers_symbol",
            ]
        )

    df = pd.read_csv(path)

    if "symbol" in df.columns:

        df["symbol"] = (
            df["symbol"]
            .astype(str)
            .str.upper()
            .str.strip()
        )

    return df


# ============================================================
# BUILD A HISTORICAL UNIVERSE
# ============================================================

def build_point_in_time_universe(
    current_universe,
    historical_constituents,
    date
):

    date = pd.Timestamp(
        date
    ).normalize()

    # Prefer historical membership
    if (
        historical_constituents is not None
        and not historical_constituents.empty
    ):

        df = historical_constituents.copy()

        df["valid_from"] = pd.to_datetime(
            df["valid_from"],
            errors="coerce"
        )

        df["valid_to"] = pd.to_datetime(
            df["valid_to"],
            errors="coerce"
        )

        mask = (
            (df["valid_from"] <= date) &
            (
                df["valid_to"].isna() |
                (df["valid_to"] >= date)
            )
        )

        return df[mask].copy()

    # If historical data isn't available,
    # use current universe but mark it as fallback.
    return current_universe.copy()


# ============================================================
# PRICE DATABASE
# ============================================================

def price_file(symbol):

    safe = (
        str(symbol)
        .upper()
        .replace("/", "_")
        .replace(":", "_")
        .replace("-", "_")
    )

    return os.path.join(
        PRICES_DIR,
        f"{safe}.parquet"
    )


def save_price_data(
    symbol,
    df
):

    ensure_data_directories()

    if df is None or df.empty:
        return

    path = price_file(symbol)

    new_df = df.copy()

    new_df["date"] = pd.to_datetime(
        new_df["date"]
    ).dt.normalize()

    if os.path.exists(path):

        old_df = pd.read_parquet(
            path
        )

        old_df["date"] = pd.to_datetime(
            old_df["date"]
        ).dt.normalize()

        combined = pd.concat(
            [
                old_df,
                new_df
            ],
            ignore_index=True
        )

    else:

        combined = new_df

    combined = (
        combined
        .drop_duplicates(
            subset=["date"],
            keep="last"
        )
        .sort_values("date")
        .reset_index(drop=True)
    )

    combined.to_parquet(
        path,
        index=False
    )


def load_price_data(
    symbol
):

    path = price_file(symbol)

    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_parquet(
        path
    )

    df["date"] = pd.to_datetime(
        df["date"]
    ).dt.normalize()

    return df.sort_values(
        "date"
    ).reset_index(
        drop=True
    )


# ============================================================
# INDEX DATABASE
# ============================================================

def index_file(index_name):

    safe = (
        str(index_name)
        .upper()
        .replace(" ", "_")
        .replace("/", "_")
    )

    return os.path.join(
        INDICES_DIR,
        f"{safe}.parquet"
    )


def save_index_data(
    index_name,
    df
):

    ensure_data_directories()

    if df is None or df.empty:
        return

    path = index_file(
        index_name
    )

    if os.path.exists(path):

        old = pd.read_parquet(
            path
        )

        combined = pd.concat(
            [
                old,
                df
            ],
            ignore_index=True
        )

    else:

        combined = df.copy()

    combined["date"] = pd.to_datetime(
        combined["date"]
    ).dt.normalize()

    combined = (
        combined
        .drop_duplicates(
            subset=["date"],
            keep="last"
        )
        .sort_values("date")
    )

    combined.to_parquet(
        path,
        index=False
    )


def load_index_data(
    index_name
):

    path = index_file(
        index_name
    )

    if not os.path.exists(path):
        return pd.DataFrame()

    df = pd.read_parquet(
        path
    )

    df["date"] = pd.to_datetime(
        df["date"]
    ).dt.normalize()

    return df.sort_values(
        "date"
    ).reset_index(
        drop=True
    )


# ============================================================
# DATA RANGE
# ============================================================

def get_saved_date_range(
    symbol
):

    df = load_price_data(
        symbol
    )

    if df.empty:
        return None, None

    return (
        df["date"].min(),
        df["date"].max()
    )
