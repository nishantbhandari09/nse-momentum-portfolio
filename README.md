# NSE Momentum Portfolio

A Streamlit-based NSE momentum scanner, portfolio monitor, and backtesting application using **FYERS as the market-data source**.

## Current scope

### Version 1
- FYERS authentication and historical OHLCV data
- Automatic local history cache
- NIFTY 50 / 100 / 200 / 500 and BANKNIFTY groups
- ETF / domestic ETF / international ETF / defensive / index groups
- Weighted 252D / 120D / 90D / 60D momentum ranking
- EMA and SMA filters
- 52-week high / breakout filters
- Market entry gate using EMA, SMA or VSTOP
- Monthly / quarterly backtesting
- Virtual portfolio
- Strategy storage in SQLite

### Version 2
- FYERS v3 Market Data WebSocket for live prices and volume
- Persistent WebSocket connection with dynamic symbol subscriptions
- Current FYERS holdings view
- Live momentum scan using cached history plus today's live OHLCV bar
- Automatic live-monitor refresh
- Rank-based HOLD / ENTRY / EXIT-RANK alerts
- Read-only live monitoring
- **No live order execution**

The application deliberately keeps order execution disabled in Version 2. FYERS is used as the data source and account/holdings source only.

## Repository structure

~~~text
nse-momentum-portfolio/
├── app.py
├── fyers_data.py
├── strategy_engine.py
├── portfolio_store.py
├── universe_manager.py
├── requirements.txt
├── universe_groups.csv
├── historical_constituents.csv
├── .gitignore
├── .streamlit/
│   └── config.toml
└── data/
    ├── prices/             # stocks + legacy cache
    ├── indices/             # broad/index history + legacy index cache
    ├── sectors/             # sector-index history
    ├── etfs/                # ETF history
    └── reference/           # symbol master + universe catalog
~~~

## Application flow

~~~text
Streamlit app.py
      |
      +--> market_data.py
      |       +--> common history/quote interface
      |       +--> asset classification
      |       +--> cache routing
      |       |
      |       +--> FYERS provider (current primary source)
      |       +--> Yahoo fallback for supported instruments
      |
      +--> fyers_data.py
      |       +--> FYERS auth
      |       +--> REST history / quotes
      |       +--> holdings / funds
      |
      +--> fyers_live.py
      |       +--> FYERS WebSocket
      |       +--> live LTP / OHLC / volume
      |
      +--> universe_manager.py
      |       +--> symbol master
      |       +--> NIFTY constituent groups
      |       +--> sector-index groups
      |       +--> ETF / defensive groups
      |
      +--> strategy_engine.py
      |       +--> momentum ranking
      |       +--> technical filters
      |       +--> market gate
      |       +--> backtest
      |
      +--> portfolio_store.py
              +--> strategies
              +--> virtual holdings
              +--> transactions
              +--> capital
~~~

## Streamlit deployment

- Repository: nishantbhandari09/nse-momentum-portfolio
- Branch: main
- Entrypoint: app.py

requirements.txt is in the repository root so Streamlit Community Cloud can install the Python dependencies.

## FYERS secrets

Do **not** commit FYERS credentials to GitHub.

For Streamlit Community Cloud, add these values in the app's Secrets settings:

~~~toml
FYERS_APP_ID = "your_fyers_app_id"
FYERS_SECRET_ID = "your_fyers_secret_id"
FYERS_REDIRECT_URI = "https://YOUR-APP-NAME.streamlit.app/"
FYERS_ACCESS_TOKEN = ""
~~~

The deployed redirect URI must match the redirect URI configured in the FYERS application.

For local development, use .streamlit/secrets.toml; that file is ignored by Git.

## Data behaviour

Historical candles are cached locally as Parquet files. The scanner loads each symbol's history once and reuses it for both momentum ranking and technical filters, so repeated scans do not make duplicate history reads.

market_data.py is the common interface used by the strategy layer. It classifies an instrument as STOCK, INDEX, SECTOR or ETF and routes its history to the appropriate cache directory. Existing files in the old data/prices and data/indices locations are still read and migrated into the newer asset-specific cache locations when used.

A stock, index, sector index or ETF is therefore represented only by its symbol and optional metadata; there is no separate Python file for each instrument.

During a live/today scan, the FYERS v3 WebSocket overlays the latest SymbolUpdate values (LTP, OHLC and traded volume) on top of the cached daily history. The first historical preload is still a separate job; once the cache is populated, live scans use cached history and the WebSocket feed.

The current primary provider is FYERS. A Yahoo fallback remains available through the existing FYERS history helper for supported instruments. The common interface is deliberately provider-neutral so another provider adapter can be added later without rewriting the scanner or strategy engine.

## Running locally

~~~bash
pip install -r requirements.txt
streamlit run app.py
~~~

## Streamlit Cloud

Create the app from Streamlit Community Cloud using app.py as the entrypoint, then add the FYERS secrets in the Cloud Secrets panel.

Version 2 is a research and monitoring tool and does not submit trades.
