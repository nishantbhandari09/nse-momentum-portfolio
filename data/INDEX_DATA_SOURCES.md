# Historical market data sources and limitations

## Index price history

The files under data/index_prices/ are daily OHLC series for major Nifty indices, retrieved from the Nifty Indices historical-data service. The scheduled GitHub Actions workflow backfills the available history and then refreshes recent sessions after the Indian cash-market close. It deliberately overlaps the latest 14 calendar days when refreshing and deduplicates rows by date.

Source portal: https://www.niftyindices.com/reports/historical-data  
Downloader reference: https://github.com/jugaad-py/jugaad-data

## Historical index membership

data/index_membership_history.csv is sourced from the open-source project aditya-jha/nse-historical-membership, which reconstructs membership intervals from public NSE/Nifty Indices press releases and circulars.

Attribution: "NSE press releases / NSE Exchange circulars (publicly published)" and https://github.com/aditya-jha/nse-historical-membership. The upstream CSV is licensed CC BY 4.0; see https://github.com/aditya-jha/nse-historical-membership/blob/main/LICENSE-DATA.

**Important data-quality limitation:** this is reconstructed point-in-time membership, not a certified official daily constituent archive. Upstream documents reliable coverage mainly from April 2018 onward for several major indices and known cardinality drift / incomplete replacement events before 2018. Some indices begin later. The backtester uses the dates provided and must not be described as completely survivorship-bias-free for every index/date.

## Historical index membership

The checked-in file data/index_membership_history.csv is sourced from the open-source project aditya-jha/nse-historical-membership, which reconstructs membership intervals from public NSE/Nifty Indices press releases and circulars.

Attribution: "NSE press releases / NSE Exchange circulars (publicly published)" and https://github.com/aditya-jha/nse-historical-membership. The upstream CSV is licensed CC BY 4.0; see https://github.com/aditya-jha/nse-historical-membership/blob/main/LICENSE-DATA.

**Important data-quality limitation:** this is reconstructed point-in-time membership, not a certified official daily constituent archive. Upstream documents reliable coverage mainly from April 2018 onward for several major indices and known cardinality drift / incomplete replacement events before 2018. The app uses this interval ledger for Nifty 500. The source does not provide an exact Nifty 200 membership series; Nifty 200 backtests currently fall back to the broader Nifty 500 membership calendar, so those results are approximate and should not be described as fully survivorship-bias-free.

## Storage model

Price and membership CSVs are committed to this repository; they are not dependent on Streamlit's ephemeral runtime filesystem. GitHub Actions appends/repairs market-data history on a weekday schedule. If the workflow is disabled or fails for an extended period, open the repository's Actions tab and manually run Update Historical Nifty Index Data.
