# Historical market data sources and limitations

## Price history

The app's scanner and backtester use daily price files stamped `FYERS API`. The scheduled workflow uses FYERS API v3 History (`/data/history`, daily resolution) and stores the returned OHLCV values in `data/fyers_history/` for equities/ETFs and `data/index_prices/` for the nine index tickers used by the app. Repeated runs refresh recent dates, deduplicate by date, and backfill older data in 366-calendar-day chunks.

See [FYERS history setup, credentials, rate limits and backtest caveats](FYERS_HISTORY_SETUP.md). The history updater requires GitHub Actions Secrets to be configured separately from Streamlit Secrets; without credentials it skips rather than using another vendor.

Official API references:
- FYERS data/history endpoint and candle format: https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md
- FYERS Standard/Prime history range and API limits: https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans

## Historical index membership

`data/index_membership_history.csv` is a reconstructed constituent-membership ledger from the open-source project aditya-jha/nse-historical-membership. Membership is separate from the price source; FYERS candles do not provide the dates when historical index constituents entered or left an index.

Attribution: "NSE press releases / NSE Exchange circulars (publicly published)" and https://github.com/aditya-jha/nse-historical-membership. The upstream membership CSV is licensed CC BY 4.0; see https://github.com/aditya-jha/nse-historical-membership/blob/main/LICENSE-DATA.

**Important limitation:** membership is reconstructed, not a certified complete official daily constituent ledger. Upstream documents stronger coverage mainly from April 2018 onward for several indices, with incomplete replacements/cardinality drift before 2018. Exact Nifty 200 membership history is not provided by this source; Nifty 200 backtests may use the broader Nifty 500 membership calendar. Point-in-time backtests can still have survivorship bias.

## Interpreting returns and maximum drawdown

FYERS OHLCV rows are kept as returned by the History API. Do not assume that these candles are split/bonus/dividend-adjusted total-return series without verifying FYERS' adjustment conventions. The current backtester does not automatically credit dividend cashflows or subtract transaction costs, taxes, slippage or market impact. Those limitations can materially affect returns, CAGR and maximum drawdown.

## Data integrity behavior

The price readers require a `Source` column containing `FYERS API`, preventing older NSE/Yahoo files or external price archives from being silently mixed into a FYERS backtest. The manifest at `data/fyers_history_manifest.csv` records symbol mapping, oldest/newest stored dates, row counts and backfill statuses; failed API requests are listed in `data/fyers_history_failures.csv`.