Nifty 500 Momentum Portfolio & Backtesting Tool
One Streamlit app with two tabs:
Portfolio / Live Signals
Backtesting
Data sources
Zerodha Kite Connect
NSE / CSV upload
Google Finance / Google Sheets CSV
The universe is the current Nifty 500 constituent list by default. You can optionally upload your own Nifty 500 universe CSV.
Zerodha Redirect URL
The Kite Connect Redirect URL must be the deployed Streamlit app URL, for example:
`https://your-app.streamlit.app`
Do not use the GitHub repository URL as the Zerodha Redirect URL.
Kite Connect sends a `request_token` to the registered redirect URL after login. The app exchanges it for the daily access token.
Streamlit Secrets
Add these under the deployed app's Secrets settings:
```toml
KITE_API_KEY = "your_api_key"
KITE_API_SECRET = "your_api_secret"
```
Never commit these to GitHub.
CSV formats supported
Wide
```text
Date,RELIANCE,TCS,HDFCBANK
2025-01-01,1200,4000,1700
2025-01-02,1210,4015,1710
```
Long
```text
Date,Symbol,Close
2025-01-01,RELIANCE,1200
2025-01-01,TCS,4000
```
Google Finance
Google Finance is normally used through the `GOOGLEFINANCE` function in Google Sheets. Export the sheet as CSV and paste the CSV export URL into the app.
Strategy
Default momentum lookbacks: 252, 120, 90, 60 trading days.
Top N default: 20.
Exit buffer default: rank 41+.
EMA entry rules are configurable.
52-week retracement is a single threshold, not a min/max range.
From 52W High: price can be at most X% below the high.
From 52W Low: price must be at least X% up from the low-range position.
Deployment
Push `app.py`, `requirements.txt`, and `README.md` to your GitHub repository and deploy `app.py` using Streamlit Community Cloud.
