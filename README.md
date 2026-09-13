NSE Momentum Portfolio & Backtesting Tool
This Streamlit app runs from GitHub + Streamlit Community Cloud and uses Zerodha Kite Connect for historical NSE daily data.
Files
`app.py` — complete application
`requirements.txt` — Python dependencies
`.streamlit/secrets.toml` — do not commit this file; enter its contents in Streamlit Cloud Secrets
Zerodha Secrets
Use:
```toml
KITE_API_KEY = "your_kite_api_key"
KITE_API_SECRET = "your_kite_api_secret"
```
Do not put these values directly into `app.py` or GitHub.
Zerodha Redirect URL
After Streamlit Cloud deploys the app, copy the deployed URL, for example:
`https://your-app-name.streamlit.app`
Use that deployed URL as the redirect URL in the Zerodha Kite Connect developer console. The application handles the `request_token` returned to that URL and exchanges it for the day's access token.
GitHub → Streamlit Cloud
Create a GitHub repository.
Upload `app.py`, `requirements.txt`, and this README.
Do not upload API secrets.
Open Streamlit Community Cloud and deploy `app.py` from the GitHub repository.
Open Advanced settings → Secrets and paste the `KITE_API_KEY` and `KITE_API_SECRET` values.
Copy the Streamlit app URL into Zerodha's redirect URL field.
Open the app and click `Login / Refresh Zerodha` each trading day.
Scanner logic
The Portfolio section and Backtesting section are deliberately separate.
Portfolio:
current Nifty 500 universe
configurable Top N (default 20)
momentum ranking using 252/120/90/60 trading-day returns
configurable EMA filter(s)
single-number 52-week retracement threshold
entry / hold-buffer / exit-zone labels
current scanner table and CSV export
Backtesting:
independent start and end dates
monthly rebalance
same momentum/EMA/retracement conditions
entry/exit buffer logic
equity curve, CAGR, total return, drawdown, win rate and turnover
Important: using the current Nifty 500 constituent list for historical testing introduces survivorship bias. For serious historical research, use a dated historical constituent file.
