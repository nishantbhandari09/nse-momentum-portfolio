# Historical stock and index data pipelines

## FYERS data (primary source)

The `scripts/update_fyers_history.py` pipeline stores daily OHLCV from FYERS API v3 in `data/fyers_history/` and `data/index_prices/`. The login-triggered and scheduled workflow is:

- [Update FYERS Historical Price Database](../actions/workflows/update-index-history.yml)

FYERS data remains the preferred value wherever the saved history overlaps the supplemental NSE archive. The index and benchmark checks still run before a backtest is accepted.

## NSE all-security archive (historical gap filler)

The `scripts/download_nse_history.py` workflow downloads NSE daily bhavcopy data, including daily OHLC, volume, turnover, trade count, symbol, series and ISIN. It creates resumable yearly Parquet partitions under `nse_data/yearly/`. `scripts/build_derived.py` converts these into compact, split/bonus-adjusted yearly price files with ISIN-based symbol aliasing and a SHA-256 manifest.

- [Update all-stock NSE historical archive](../actions/workflows/update-nse-all-stock-history.yml)
- [Permanent NSE data Release](../releases/tag/nse-data)

The workflow maintains the permanent Release tag `nse-data`. Its assets are `prices_adj_YYYY.parquet`, `symbols.parquet`, and `manifest.json`. Each year's asset is uploaded only when its content hash changes; the manifest is refreshed after every run. The normal GitHub Actions artifact is also kept for troubleshooting, but the Streamlit app no longer depends on that expiring artifact.

### How Streamlit uses it

The app's **NSE All-Stock Historical Archive Status** panel checks whether the Release exists and shows the published date range plus how many yearly files are cached in the current app runtime. A backtest requests only the years needed for its date range and warm-up period. The downloader verifies each asset against its SHA-256 hash and caches it in `nse_data/derived/`. The backtest merges these prices underneath saved FYERS prices for missing/older stock and ETF observations.

To initialize the archive, open the all-stock workflow and choose **Run workflow**. Wait for a green check and confirm the `nse-data` Release exists. Subsequent weekday runs add the latest data and refresh changed yearly assets automatically. The first full backfill may take a long time; later runs reuse cached raw downloads.

## Accuracy limits

- NSE adjusted prices are a split/bonus adjustment proxy based on NSE previous-close changes. They exclude cash dividends and can misidentify changes around suspensions or other data gaps; results are price returns, not total returns.
- The archive covers securities with NSE cash-market bhavcopy rows. It does not provide index levels. Index/benchmark candles continue to come from the separately audited saved index history.
- Point-in-time index membership is a separate dataset. The app uses its membership calendar, and history values/ISIN mapping still need review for rare renames, corporate actions, delisted securities, and missing records.
- A green workflow and full date range are not proof every candle is correct. Check the downloaded manifest, failures file, internal gaps, large one-day changes and specific trade dates before relying on CAGR or maximum drawdown.
