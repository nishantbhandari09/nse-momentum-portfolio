# FYERS historical price database

This app's scanner and backtester read persisted daily OHLCV files stamped `FYERS API`. They do not fall back to Yahoo Finance prices or the old NSE index-price cache.

## Stored files

- `data/fyers_history/<ticker>.csv`: daily Open, High, Low, Close and Volume for each equity/ETF symbol.
- `data/index_prices/*.csv`: daily OHLCV for the nine index series the app uses.
- `data/fyers_history_manifest.csv`: resolved symbol, first/last date, row count and backfill status.
- `data/fyers_history_failures.csv`: API requests needing retry or review.

Each symbol file is merged by trading date. Recent candles are refreshed with an overlap; older history is downloaded in chunks no larger than 366 calendar days. Re-running the workflow resumes the backfill and eventually reaches the earliest candle returned by FYERS, or the configured floor `FYERS_HISTORY_START_DATE`.

## Configure GitHub Actions credentials

The FYERS login in Streamlit and GitHub Actions are separate. A token in Streamlit Secrets is NOT automatically available to the GitHub workflow.

1. Open the Streamlit app and complete a fresh login through **FYERS Connection**.
2. Expand **Set up scheduled FYERS history updates** and tick the checkbox to reveal the refresh token. Copy it privately.
3. On GitHub, open this repository → **Settings → Secrets and variables → Actions → New repository secret**.
4. Before running, open your FYERS API app settings and ensure **Market Data → Historical** permission is enabled. Then add these Actions secrets:

| Secret | Value |
|---|---|
| `FYERS_APP_ID` | Activated FYERS App ID |
| `FYERS_SECRET_ID` | Secret belonging to that same app |
| `FYERS_REFRESH_TOKEN` | Refresh token copied from the connected app |
| `FYERS_PIN` | Your FYERS PIN, requested by the refresh-token endpoint |

Never put tokens, the app secret or the PIN in code, commits, issues, or chat. If the refresh-token exchange is not accepted, you can temporarily use `FYERS_ACCESS_TOKEN` as a repository secret, but access tokens expire and are not suitable for indefinite automation.

After saving secrets, open **Actions → Update FYERS Historical Price Database → Run workflow**. It also runs on weekdays after market close. If credentials are missing, the workflow deliberately skips downloading instead of fetching prices from another provider.

FYERS currently documents refresh tokens as valid for 15 days, and its developer reference warns that the refresh-token flow may be discontinued. Scheduled history refresh may therefore require periodically completing a fresh FYERS login and replacing `FYERS_REFRESH_TOKEN` in GitHub Actions Secrets. Treat that as a credential-maintenance requirement, not an automatic guarantee. The updater will stop visibly on an authentication error rather than switch to another price vendor.

## FYERS history limits and backfill speed

FYERS' current support page says daily history is limited to the last 5 years on the Standard plan and provides the full available daily history (20+ years) on Prime. The history endpoint allows up to 366 calendar days in one daily-history request. Source: https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans and https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md.

The default workflow is conservative for the Standard plan: `FYERS_MAX_REQUESTS_PER_RUN=4500` and `FYERS_REQUEST_DELAY_SECONDS=1.25`. The updater backfills a limited number of chunks per run, so a large universe is filled over multiple workflow runs. After confirming an eligible Prime plan, faster settings can be added as GitHub Actions Variables:

- `FYERS_MAX_REQUESTS_PER_RUN=20000`
- `FYERS_REQUEST_DELAY_SECONDS=0.15`
- `FYERS_HISTORY_START_DATE=1990-01-01` (optional override for Prime / older-history entitlement; without this variable the updater probes the plan's accessible history)

Do not raise the request budget or speed unless your account's actual API rate limits allow it. The updater records pending/error/no-history statuses in the manifest; it does not claim an instrument has history when FYERS returns no usable candles.

## What this means for backtest accuracy

The downloaded daily OHLCV values are the candles returned by FYERS. They are not Yahoo Finance `auto_adjust` prices. Daily candles improve provider consistency, but price history alone does not guarantee total-return accuracy: verify FYERS' treatment of split/bonus adjustments and dividends before relying on long-period returns. The current portfolio simulator also does not automatically add dividend cash flows, brokerage, taxes, slippage, or market-impact costs. Those items can change CAGR, total return and maximum drawdown.

Historical index membership is separate from price data. `data/index_membership_history.csv` is reconstructed external data; attribution, source and gaps are documented in `data/INDEX_DATA_SOURCES.md`. Exact constituent histories are not available for every index/date, especially pre-2018; Nifty 200 can use a wider Nifty 500 membership proxy. Therefore, using FYERS prices does not by itself remove survivorship bias.

## Inspect progress

Open **Actions → Update FYERS Historical Price Database** and inspect the run logs. In the app, expand **FYERS Historical Data Status** to see symbol count, candle count, oldest/newest saved dates and pending backfills. A successful code validation only checks Python syntax; the first real download is not complete until the workflow has been run with valid FYERS credentials and its manifest shows populated rows.