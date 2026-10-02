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
- Live FYERS quotes
- Current FYERS holdings view
- Live momentum scan
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
    ├── prices/
    ├── indices/
    └── reference/
~~~

## Application flow

~~~text
Streamlit app.py
      |
      +--> fyers_data.py
      |       +--> FYERS auth
      |       +--> quotes
      |       +--> historical candles
      |       +--> holdings/funds
      |
      +--> universe_manager.py
      |       +--> FYERS symbol master
      |       +--> NIFTY constituent groups
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

FYERS is the primary historical and live-data source.

The app caches historical data locally as Parquet files. Missing ranges are requested automatically when a scan or backtest needs them. A small Yahoo Finance fallback remains only for symbols for which FYERS returns no usable history; it is not the primary source.

## Running locally

~~~bash
pip install -r requirements.txt
streamlit run app.py
~~~

## Streamlit Cloud

Create the app from Streamlit Community Cloud using app.py as the entrypoint, then add the FYERS secrets in the Cloud Secrets panel.

Version 2 is a research and monitoring tool and does not submit trades.
