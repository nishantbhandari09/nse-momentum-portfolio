"""TradingView advanced-chart embeds for visualization only.

The scanner and all portfolio/backtest calculations remain powered by FYERS and
the app's stored historical data. This module only creates a browser-side chart.
"""
from __future__ import annotations

import json
import re


_INDEX_SYMBOLS = {
    # Use the exact TradingView exchange tickers. Nifty 500/200 are CNX500/CNX200,
    # not NIFTY500/NIFTY200.
    "^CRSLDX": "NSE:CNX500",
    "^NSEI": "NSE:NIFTY",
    "^NSEBANK": "NSE:BANKNIFTY",
    "^CNX200": "NSE:CNX200",
    # Nifty sectoral index TradingView aliases.
    "^CNXIT": "NSE:CNXIT",
    "^CNXPHARMA": "NSE:CNXPHARMA",
    "^CNXAUTO": "NSE:CNXAUTO",
    "^CNXFMCG": "NSE:CNXFMCG",
    "^CNXMETAL": "NSE:CNXMETAL",
}

# Additional TradingView-only index charts. They can be charted without local
# OHLC files; locally calculated metrics are shown only when verified FYERS data
# exists for the selected symbol.
TRADINGVIEW_ONLY_INDEXES = {
    "TV:NIFTY100": {"label": "Nifty 100", "symbol": "NSE:NIFTY100"},
    "TV:NIFTYNEXT50": {"label": "Nifty Next 50", "symbol": "NSE:NIFTYJR"},
    "TV:NIFTYMIDCAP100": {"label": "Nifty Midcap 100", "symbol": "NSE:CNXMIDCAP"},
    "TV:NIFTYSMALLCAP100": {"label": "Nifty Smallcap 100", "symbol": "NSE:CNXSMALLCAP"},
    "TV:INDIAVIX": {"label": "India VIX", "symbol": "NSE:INDIAVIX"},
    "TV:NIFTYFINANCE": {"label": "Nifty Financial Services", "symbol": "NSE:CNXFINANCE"},
    "TV:NIFTYMEDIA": {"label": "Nifty Media", "symbol": "NSE:CNXMEDIA"},
    "TV:NIFTYINFRA": {"label": "Nifty Infrastructure", "symbol": "NSE:CNXINFRA"},
    "TV:NIFTYREALTY": {"label": "Nifty Realty", "symbol": "NSE:CNXREALTY"},
    "TV:NIFTYPSUBANK": {"label": "Nifty PSU Bank", "symbol": "NSE:CNXPSUBANK"},
    "TV:NIFTYPVTBANK": {"label": "Nifty Private Bank", "symbol": "NSE:NIFTYPVTBANK"},
    "TV:NIFTYENERGY": {"label": "Nifty Energy", "symbol": "NSE:CNXENERGY"},
    "TV:NIFTYFIN25": {"label": "Nifty Financial Services 25/50", "symbol": "NSE:NIFTYFINSRV25_50"},
    "TV:NIFTYHEALTHCARE": {"label": "Nifty Healthcare", "symbol": "NSE:NIFTY_HEALTHCARE"},
    "TV:NIFTYOILGAS": {"label": "Nifty Oil & Gas", "symbol": "NSE:NIFTY_OIL_AND_GAS"},
}


def tv_symbol(ticker: str) -> str:
    """Convert an app ticker such as RELIANCE.NS or ^NSEI to TradingView format."""
    value = str(ticker or "").strip()
    if not value:
        return "NSE:NIFTY"
    if value in _INDEX_SYMBOLS:
        return _INDEX_SYMBOLS[value]
    if value in TRADINGVIEW_ONLY_INDEXES:
        return TRADINGVIEW_ONLY_INDEXES[value]["symbol"]
    if ":" in value:
        return value
    if value.endswith(".NS"):
        value = value[:-3]
    value = value.lstrip("^")
    # TradingView ticker IDs use underscores for ampersands and hyphens.
    value = re.sub(r"[&-]", "_", value.upper())
    return f"NSE:{value}"


def advanced_chart_html(
    symbol: str,
    interval: str = "D",
    height: int = 560,
    theme: str = "light",
) -> str:
    """Return the TradingView chart HTML; no chart values are returned to Python."""
    valid_intervals = {"1", "3", "5", "15", "30", "45", "60", "120", "180", "240", "D", "W", "M"}
    if interval not in valid_intervals:
        interval = "D"
    height = max(320, min(int(height), 900))
    chart_id = "tradingview_chart"
    config = {
        "autosize": True,
        "symbol": tv_symbol(symbol),
        "interval": interval,
        "timezone": "Asia/Kolkata",
        "theme": "dark" if str(theme).lower() == "dark" else "light",
        "style": "1",
        "locale": "en",
        "allow_symbol_change": True,
        "hide_side_toolbar": False,
        "withdateranges": True,
        "studies": ["STD;SMA", "Volume@tv-basicstudies"],
        "container_id": chart_id,
    }
    # Avoid a user-controlled symbol accidentally terminating the script tag.
    encoded_config = json.dumps(config).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"""<div class="tradingview-widget-container" style="height:{height}px;width:100%">
<div id="{chart_id}" style="height:{height}px;width:100%"></div>
<script src="https://s3.tradingview.com/tv.js"></script>
<script>new TradingView.widget({encoded_config});</script>
</div>"""
