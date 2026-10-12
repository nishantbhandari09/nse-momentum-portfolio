# Historical market data sources and limitations

## Price history

The app's scanner and backtester use daily price files stamped `FYERS API`. After a successful FYERS login, the app securely updates the current `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` GitHub Actions secrets and dispatches the history updater. The workflow uses FYERS API v3 History (`/data/history`, daily resolution) and stores returned OHLCV in `data/fyers_history/` for equities/ETFs and `data/index_prices/` for the nine index tickers used by the app. Repeated runs refresh recent dates, deduplicate by date, and backfill older data in 366-calendar-day chunks.

See [FYERS history setup, credentials, rate limits and backtest caveats](FYERS_HISTORY_SETUP.md). One-time setup requires a repository-restricted GitHub fine-grained token in Streamlit Secrets with `Secrets: read/write` and `Contents: read/write`. The FYERS PIN is not needed for the login-triggered updater. The workflow will stop rather than use another price vendor if the login or token-sync step is not configured.

Official API references:
- FYERS data/history endpoint and candle format: https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md
- FYERS Standard/Prime history range and API limits: https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans

## Index launch dates and requested history start

The app keeps the **formal launch date** separate from the **earliest requested history date**. Some Nifty indices have a base/history series before the formal launch date; that earlier history is not the same as live index history. The coverage audit shows both dates and checks whether the saved file reaches the target. A complete date range still does not prove every close is numerically identical to NSE Indices' record.

Official Nifty Indices factsheets used for the launch dates currently published:
- [Nifty 50](https://www.niftyindices.com/Factsheet/ind_nifty50.pdf): launch April 22, 1996.
- [Nifty Bank](https://www.niftyindices.com/Factsheet/ind_nifty_bank.pdf): launch September 15, 2003; base date January 1, 2000.
- [Nifty 200](https://www.niftyindices.com/Factsheet/ind_nifty_200.pdf): launch July 19, 2011; base date January 1, 2004.
- [Nifty Pharma](https://niftyindices.com/Factsheet/ind_nifty_pharma.pdf): launch July 1, 2005; base date January 1, 2001.
- [Nifty Auto](https://www.niftyindices.com/Factsheet/ind_nifty_auto.pdf) and [Nifty Metal](https://www.niftyindices.com/Factsheet/ind_nifty_metal.pdf): launch July 12, 2011; base date January 1, 2004.
- [Nifty FMCG](https://www.niftyindices.com/Factsheet/ind_nifty_FMCG.pdf): launch September 22, 1999; base date January 1, 1996.
- The current [Nifty 500](https://www.niftyindices.com/Factsheet/ind_nifty_500.pdf) and [Nifty IT](https://niftyindices.com/Factsheet/ind_nifty_it.pdf) factsheets leave the launch-date field blank. The app labels these as “Not published” rather than guessing.

## Historical index membership

`data/index_membership_history.csv` is a reconstructed constituent-membership ledger from the open-source project aditya-jha/nse-historical-membership. Membership is separate from the price source; FYERS candles do not provide the dates when historical index constituents entered or left an index.

Attribution: "NSE press releases / NSE Exchange circulars (publicly published)" and https://github.com/aditya-jha/nse-historical-membership. The upstream membership CSV is licensed CC BY 4.0; see https://github.com/aditya-jha/nse-historical-membership/blob/main/LICENSE-DATA.

**Important limitation:** membership is reconstructed, not a certified complete official daily constituent ledger. Upstream documents stronger coverage mainly from April 2018 onward for several indices, with incomplete replacements/cardinality drift before 2018. Exact Nifty 200 membership history is not provided by this source; Nifty 200 backtests may use the broader Nifty 500 membership calendar. Point-in-time backtests can still have survivorship bias.

## Interpreting returns and maximum drawdown

FYERS OHLCV rows are kept as returned by the History API. Do not assume that these candles are split/bonus/dividend-adjusted total-return series without verifying FYERS' adjustment conventions. The current backtester does not automatically credit dividend cashflows or subtract transaction costs, taxes, slippage or market impact. Those limitations can materially affect returns, CAGR and maximum drawdown.

## Data integrity behavior

The price readers require a `Source` column containing `FYERS API`, preventing older NSE/Yahoo files or external price archives from being silently mixed into a FYERS backtest. The manifest at `data/fyers_history_manifest.csv` records symbol mapping, oldest/newest stored dates, row counts and backfill statuses; failed API requests are listed in `data/fyers_history_failures.csv`.