# Historical stock and index data: how the new pipeline works

## FYERS data (primary app source)

The existing scripts/update_fyers_history.py pipeline downloads daily OHLCV from FYERS API v3 into data/fyers_history/ and data/index_prices/. The app's scanner and backtester continue to use that saved FYERS data. The login-triggered and scheduled workflow is:

- [Update FYERS Historical Price Database](../actions/workflows/update-index-history.yml)

The app's **Price Charts** page plots saved stock/index closes, daily volume, and provides a CSV download.

## NSE all-security archive (supplementary source)

The new scripts/download_nse_history.py downloads NSE daily bhavcopy files, which contain traded securities and include daily OHLC, volume, traded value, trade count, symbol, series and ISIN. It writes yearly Parquet partitions under nse_data/yearly/ so the initial archive can be processed year-by-year instead of holding all 30 years in memory.

- [Update all-stock NSE historical archive](../actions/workflows/update-nse-all-stock-history.yml)

The workflow runs after the weekday market close and uploads a downloadable artifact containing the yearly Parquet partitions. Use **Actions → Update all-stock NSE historical archive → latest successful run → Artifacts** to download the archive. The first run may take a long time and can be limited by NSE archive availability, GitHub runner time, and cache capacity.

## Important accuracy limits

- The NSE archive is supplemental and is **not** the same source as FYERS. The app does not silently merge vendors into the FYERS-only backtest.
- The adjusted OHLC fields are a corporate-action proxy based on NSE previous-close changes; they do not include cash dividends and can misidentify breaks around suspensions or other data gaps.
- IndexInclExcl.xls records company names and event dates, not a stable ticker/ISIN for every historical constituent. The workbook in the app can be inspected and normalized, but it must be reconciled to symbols/ISINs before it is used as a complete point-in-time backtest membership ledger.
- A downloadable Actions artifact is not automatically present in the Streamlit Cloud filesystem. The app uses the NSE archive only when its Parquet partitions are available beside the app; otherwise its charts and backtests continue to use saved FYERS data.
- A first/last date check is not proof that every daily candle or price is correct. Validate the downloaded manifest, failures file and selected dates before trusting backtest returns or max drawdown.
