FYERS setup for the Quantitative Strategy Engine
1. Install the FYERS package
The app now uses `fyers-apiv3` as its primary historical market-data source. Yahoo Finance remains only as a fallback for symbols that FYERS does not return.
Use the supplied `requirements_fyers.txt` as `requirements.txt` in GitHub.
2. FYERS API app
In the FYERS API dashboard, create/activate the API app and note:
App ID
Secret ID
Redirect URL
The Redirect URL must exactly match the URL registered in FYERS. For a Streamlit Community Cloud app, use the deployed `https://...streamlit.app` URL, not the GitHub repository URL.
3. Streamlit Secrets
In Streamlit Community Cloud: App -> Settings -> Secrets.
Paste:
```toml
FYERS_APP_ID = "YOUR_APP_ID"
FYERS_SECRET_ID = "YOUR_APP_SECRET"
FYERS_REDIRECT_URI = "https://YOUR-APP-NAME.streamlit.app"
```
Do not put the Secret ID or an access token in GitHub code.
4. Connect
Open the app. In the left sidebar, click `Connect / Login to FYERS` and complete the FYERS login. FYERS redirects back to the registered URL with an auth code; the app exchanges that code for the access token and keeps it in the current Streamlit browser session.
A fresh FYERS login may be required for the next trading day/session.
5. Data flow
`FYERS -> Strategy Engine -> ranking / EMA / defensive fallback -> portfolio signals`
Yahoo Finance is used only when FYERS does not return a requested symbol.
Important for live order placement
This update is for FYERS market data and authentication. Do not add automated order placement yet. FYERS's current retail-algo rules require a registered static IP for order requests and have additional requirements for automated/third-party platforms. Finish and verify the data layer first.
