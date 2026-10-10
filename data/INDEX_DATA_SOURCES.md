# Historical market data sources and limitations

## Index price history

The files under data/index_prices/ are daily OHLC series for major Nifty indices, retrieved from the Nifty Indices historical-data service. The scheduled GitHub Actions workflow backfills the available history and then refreshes recent sessions after the Indian cash-market close. It deliberately overlaps the latest 14 calendar days when refreshing and deduplicates rows by date.

Source portal: https://www.niftyindices.com/reports/historical-data  
Downloader reference: https://github.com/jugaad-py/jugaad-data

## Historical index membership

data/index_membership_history.csv is sourced from the open-source project aditya-jha/nse-historical-membership, which reconstructs membership intervals from public NSE/Nifty Indices press releases and circulars.

Attribution: "NSE press releases / NSE Exchange circulars (publicly published)" and https://github.com/aditya-jha/nse-historical-membership. The upstream CSV is licensed CC BY 4.0; see https://github.com/aditya-jha/nse-historical-membership/blob/main/LICENSE-DATA.

**Important data-quality limitation:** this is reconstructed point-in-time membership, not a certified official daily constituent archive. Upstream documents reliable coverage mainly from April 2018 onward for several major indices and known cardinality drift / incomplete replacement events before 2018. Some indices begin later. The backtester uses the dates provided and must not be described as completely survivorship-bias-free for every index/date.

## Nifty 200 / Nifty 500 historical constituent snapshots

The local files under data/index_membership_snapshots/ contain dated Nifty 200 and Nifty 500 constituent snapshots from the public Auto-Index-Constituents-Tracker dataset:
- https://github.com/floyds1995/Auto-Index-Constituents-Tracker
- https://github.com/floyds1995/Auto-Index-Constituents-Tracker/blob/main/NSE/Nifty_200.csv
- https://github.com/floyds1995/Auto-Index-Constituents-Tracker/blob/main/NSE/Nifty_500.csv

The updater refreshes these snapshots from their upstream CSVs. The current files start in April 2013 and contain periodic (usually monthly) snapshots, not every intraday or exact effective-date change. The backtester treats each snapshot date as its effective date, so this is a practical point-in-time approximation—not a certified complete daily index ledger. Earlier dates fall back to the existing Nifty 500 membership interval source; for Nifty 200 before its first snapshot, that fallback is the broader Nifty 500 universe, not exact Nifty 200 membership.

The upstream project documents that NSE owns the index data and that redistribution is subject to NSE's terms of use. Check those terms before redistributing this public repository or reusing the datasets commercially. Symbol renames and corporate actions can also leave gaps in historic price coverage, so historical constituents with no matching price series cannot be fully evaluated.

## Storage model

Price and membership CSVs are committed to this repository; they are not dependent on Streamlit's ephemeral runtime filesystem. GitHub Actions appends/repairs market-data history on a weekday schedule. If the workflow is disabled or fails for an extended period, open the repository's Actions tab and manually run Update Historical Nifty Index Data.
