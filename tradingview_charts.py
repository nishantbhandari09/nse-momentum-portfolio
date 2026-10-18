"""TradingView advanced-chart embeds for visualization only.

The scanner and all portfolio/backtest calculations remain powered by FYERS and
the app's stored historical data. This module only creates a browser-side chart.
"""
from __future__ import annotations

import json
import re


_INDEX_SYMBOLS = {
    "^CRSLDX": "NSE:NIFTY500",
    "^NSEI": "NSE:NIFTY",
    "^NSEBANK": "NSE:BANKNIFTY",
    "^CNX200": "NSE:NIFTY200",
    # Nifty sectoral index TradingView aliases.
    "^CNXIT": "NSE:CNXIT",
    "^CNXPHARMA": "NSE:CNXPHARMA",
    "^CNXAUTO": "NSE:CNXAUTO",
    "^CNXFMCG": "NSE:CNXFMCG",
    "^CNXMETAL": "NSE:CNXMETAL",
}


def tv_symbol(ticker: str) -> str:
    """Convert an app ticker such as RELIANCE.NS or ^NSEI to TradingView format."""
    value = str(ticker or "").strip()
    if not value:
        return "NSE:NIFTY"
    if ":" in value:
        return value
    if value in _INDEX_SYMBOLS:
        return _INDEX_SYMBOLS[value]
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
