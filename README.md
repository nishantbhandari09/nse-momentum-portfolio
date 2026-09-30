# NSE Momentum Portfolio — Final

This project is a rules-based momentum scanner, strategy library, portfolio execution layer and historical backtester using FYERS as the market-data/broker API.

## App sections

1. **Scanner** — select a universe, momentum periods/weights, stock conditions, Top N entry rank, Exit Rank, and the market-entry gate. Scanning never places orders.
2. **Portfolio** — choose a saved strategy, add Investment/Add Funds, review proposed exits/entries, then choose **VIRTUAL** or **REAL** execution. REAL sends live FYERS orders only after an explicit confirmation.
3. **Monthly Backtest** — historical monthly/quarterly rebalances, market gate state, entries, exits, cash, equity curve, CAGR, return, drawdown and transaction log.
4. **Data Manager** — sync FYERS symbol master, build ETF/index groups, download and incrementally cache daily history in Parquet.
5. **Saved Strategies** — reuse the exact saved configuration.

## FYERS Secrets

In Streamlit Cloud → App → Settings → Secrets:

```toml
FYERS_APP_ID = "YOUR_NEW_FYERS_APP_ID"
FYERS_SECRET_ID = "YOUR_FYERS_SECRET"
FYERS_REDIRECT_URI = "https://nse-momentum-portfolio-65jtdnqcquj9px8qr77f8d.streamlit.app"
FYERS_ACCESS_TOKEN = ""
```

The current FYERS v3 authentication flow uses the registered redirect URI, returns an `auth_code`, and exchanges it using `appIdHash = SHA256(app_id:secret_id)` for an access token. See FYERS's current API authentication guidance.

## Live trading

The Portfolio page has two modes:

- **VIRTUAL**: updates the local model portfolio database; no broker orders.
- **REAL**: calls FYERS order placement using market orders for the proposed CNC equity/ETF entries and exits.

REAL mode is intentionally locked behind two confirmations:
1. checkbox confirming live orders;
2. the exact phrase `EXECUTE LIVE`.

The app also tracks which symbols are **managed by this strategy**, so it does not automatically sell unrelated FYERS holdings.

For first-time real execution, existing FYERS holdings are treated as unmanaged until the app has bought them through this strategy. This is a safety boundary.

## Important FYERS 2026 requirement

Live API order placement requires the new compliant FYERS app, a validated/whitelisted static IP and the current API credentials. Streamlit Cloud does not give this application the same fixed public IP as your home connection, so the **REAL** mode may be rejected until the app is deployed on a server/host whose outbound static IP is the one whitelisted in FYERS.

Use VIRTUAL mode while testing the strategy in Streamlit Cloud.

## ETF and index universes

After deploying, go to **Data Manager** → **Sync FYERS NSE Symbol Master**, then **Sync NIFTY 50/100/200/500**.

The app derives these dynamic groups:

- `ALL_ETF` — all ETF-like NSE symbols found in FYERS symbol master.
- `DOMESTIC_ETF` — ETF symbols not classified as overseas/international by the keyword classifier.
- `INTERNATIONAL_ETF` — ETF symbols whose description contains common overseas-market terms such as Nasdaq, S&P, MSCI, Japan, China, Hang Seng, global, international, etc.
- `DEFENSIVE` — ETF symbols with gold, government securities, G-Sec, liquid, overnight or treasury-style descriptions.
- `ALL_INDEX` — NSE index symbols from the FYERS symbol master.

The classification is deliberately editable: use `universe_groups.csv` for custom mappings.

## Historical backtest methodology

The backtester uses:

- ranking signal at the rebalance-date close;
- execution at the next available trading-day open to avoid look-ahead bias;
- separate `Top N` entry rank and `Exit Rank`;
- normal exits continue even when the market-entry gate is blocked;
- market gate blocks NEW entries only;
- equal-target capital sizing across new positions without leverage;
- transaction-cost and slippage assumptions;
- month-by-month holdings, entries, exits, cash and equity;
- end-of-period holdings and a complete trade log.

Point-in-time constituent accuracy requires real historical membership data in `historical_constituents.csv`. The file is a framework until populated. Without it, a group may fall back to the currently available symbol set, which introduces survivorship bias.

## Local persistence

Portfolio state is stored in `data/momentum_portfolio.db`; prices are stored as Parquet files under `data/prices` and `data/indices`. Streamlit Cloud storage is not a permanent database. For persistent live use, deploy on a server with a persistent disk/database and a compliant fixed outbound IP.
