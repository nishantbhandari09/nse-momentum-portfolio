# Nifty 500 Momentum Portfolio & Backtesting Tool

One Streamlit application providing real-time scanning and historical backtesting for Nifty 500 stocks.

## Architecture & Data Flow

- **Yahoo Finance**: Downloads multi-year daily OHLCV data concurrently for Nifty 500 stocks to power technical indicators, 252-session High/Low calculations, EMAs, momentum ranking, and backtesting without rate-limit issues.
- **Zerodha Kite Connect**: Fetches live real-time LTP quotes to overlay on top of Yahoo Finance's daily history for current portfolio scanning.
- **Alternative Sources**: Supports direct CSV uploads and Google Finance CSV URL exports.

## Streamlit Secrets

Configure your credentials under app secrets:

```toml
KITE_API_KEY = "your_api_key"
KITE_API_SECRET = "your_api_secret"
