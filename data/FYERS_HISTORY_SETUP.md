# FYERS historical price database

This app's scanner and backtester read persisted daily OHLCV files stamped `FYERS API`. They do not fall back to Yahoo Finance prices or the old NSE index-price cache.

## Stored files

- `data/fyers_history/<ticker>.csv`: daily Open, High, Low, Close and Volume for each equity/ETF symbol.
- `data/index_prices/*.csv`: daily OHLCV for the nine index series the app uses.
- `data/fyers_history_manifest.csv`: resolved symbol, first/last date, row count and backfill status.
- `data/fyers_history_failures.csv`: API requests needing retry or review.

Each symbol file is merged by trading date. Recent candles are refreshed with an overlap; older history is downloaded in chunks no larger than 366 calendar days. Re-running the workflow resumes the backfill and eventually reaches the earliest candle returned by FYERS, or the configured floor `FYERS_HISTORY_START_DATE`.

## Configure automatic updates when you log in

The Streamlit app and GitHub Actions are separate runtimes. To make the historical database update whenever you connect to FYERS, the app securely copies the current login token into a GitHub Actions secret and triggers the updater. The token is encrypted with GitHub's repository public key before upload; it is not put in a workflow input, repository file or log.

### One-time setup

1. Create a **fine-grained personal access token** on GitHub, restricted to this repository: `nishantbhandari09/nse-momentum-portfolio`.
2. Give it only these repository permissions:
   - **Secrets: Read and write**
   - **Contents: Read and write** (to start the repository-dispatch workflow)
3. In Streamlit Community Cloud, open this app's **Settings → Secrets** and add:
   ```toml
   GITHUB_ACTIONS_TOKEN = "paste-your-github-token-here"
   ```
   Keep this token private. Never put it in the repository or chat.
4. Save the Streamlit settings and log into FYERS from the app once. As a safety check, the app will display the verified FYERS account ID and will not start a history update until you authorize that account.
5. Copy the displayed ID into Streamlit Secrets:
   ```toml
   FYERS_SYNC_ALLOWED_USER_ID = "your-verified-fyers-account-id"
   ```
   Save the setting, disconnect FYERS in the app, and connect again.

After this one-time setup, each successful login from the authorized account automatically updates the GitHub Actions secrets `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN`, then triggers **Update FYERS Historical Price Database**. The account check prevents other visitors to the publicly accessible app from overwriting your updater credentials. No separate FYERS PIN or refresh-token secret is required for this login-triggered flow. The access token is short-lived, so the updater runs using the fresh token sent after each login. Do not manually start a run days later and expect an old token to remain valid; log in again first.

If live quotes are available, the connected app fetches them from your current FYERS session while scanning and valuing holdings. The history workflow separately downloads available daily OHLCV candles and commits the progress to `data/fyers_history/`, `data/index_prices/` and the manifest. It may take multiple runs to complete large historical backfills.

If the app reports that the update could not be triggered, check that the GitHub token is limited to this repository and has the two permissions above. The login and live-quote connection can still work even while historical saving is not configured.

## FYERS history limits and backfill speed

FYERS' current support page says daily history is limited to the last 5 years on the Standard plan and provides the full available daily history (20+ years) on Prime. The history endpoint allows up to 366 calendar days in one daily-history request. Source: https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans and https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md.

The updater is conservative for the Standard plan: `FYERS_MAX_REQUESTS_PER_RUN=4500` and `FYERS_REQUEST_DELAY_SECONDS=1.25`. The updater backfills a limited number of chunks per run, so a large universe is filled over multiple workflow runs. After confirming an eligible Prime plan, faster settings can be added as GitHub Actions Variables:

- `FYERS_MAX_REQUESTS_PER_RUN=20000`
- `FYERS_REQUEST_DELAY_SECONDS=0.15`
- `FYERS_HISTORY_START_DATE=1990-01-01` (optional override for Prime / older-history entitlement; without this variable the updater probes the plan's accessible history)

Do not raise the request budget or speed unless your account's actual API rate limits allow it. The updater records pending/error/no-history statuses in the manifest; it does not claim an instrument has history when FYERS returns no usable candles.

## What this means for backtest accuracy

The downloaded daily OHLCV values are the candles returned by FYERS. They are not Yahoo Finance `auto_adjust` prices. Daily candles improve provider consistency, but price history alone does not guarantee total-return accuracy: verify FYERS' treatment of split/bonus adjustments and dividends before relying on long-period returns. The current portfolio simulator also does not automatically add dividend cash flows, brokerage, taxes, slippage, or market-impact costs. Those items can change CAGR, total return and maximum drawdown.

Historical index membership is separate from price data. `data/index_membership_history.csv` is reconstructed external data; attribution, source and gaps are documented in `data/INDEX_DATA_SOURCES.md`. Exact constituent histories are not available for every index/date, especially pre-2018; Nifty 200 can use a wider Nifty 500 membership proxy. Therefore, using FYERS prices does not by itself remove survivorship bias.

## Inspect progress

Open **Actions → Update FYERS Historical Price Database** and inspect the run logs. In the app, expand **FYERS Historical Data Status** to see symbol count, candle count, oldest/newest saved dates and pending backfills. A successful code validation only checks Python syntax; the first real download is not complete until the workflow has been run with valid FYERS credentials and its manifest shows populated rows.