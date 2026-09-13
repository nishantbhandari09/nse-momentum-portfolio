# NSE Momentum Portfolio Dashboard

A Streamlit dashboard for a monthly NSE momentum portfolio strategy.

## Default strategy

- Momentum periods: 252 / 120 / 90 / 60 trading days
- Entry target: Top 20
- Exit buffer: Rank 41 or worse
- Monthly rebalancing
- Equal weighting across the selected momentum periods
- Daily closing prices fetched through `yfinance`
- Batched requests + delay + 24-hour Streamlit cache to reduce repeated downloads

## What you can change from the dashboard

- Target portfolio size / Entry Top N
- Exit rank buffer
- Lookback periods
- Lookback weights
- Data date range
- Batch size and delay
- Stock universe through CSV upload

## Deploy without installing Python

1. Create a GitHub repository.
2. Upload `app.py`, `requirements.txt`, `universe_template.csv`, `.gitignore`, and `README.md`.
3. Open https://share.streamlit.io and connect GitHub.
4. Click **Create app** and select your repository, branch and `app.py`.
5. Deploy.

Streamlit Community Cloud is free. The application will receive a `streamlit.app` URL.

## Important data note

This starter uses Yahoo Finance through `yfinance`; it is not an official NSE market-data feed. For commercial or production use, replace the data layer with an appropriately licensed data source.
