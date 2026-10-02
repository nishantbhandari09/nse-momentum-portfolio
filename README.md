# NSE Momentum Portfolio & Quantitative Trading Platform 📈

A full-stack algorithmic trading and portfolio management application built specifically for the **National Stock Exchange (NSE)**. This platform allows quantitative investors to construct custom stock universes, run trend and momentum strategy scans, configure Market Trend Filters (MTF), execute portfolio rebalancing, and track live positions via the **FYERS API**.

---

## ✨ Features

- **📊 Dynamic Dashboard:** Comprehensive view of active strategies, portfolio performance, realized/unrealized P&L, and cash reserves.
- **📁 Group & Universe Manager:** Dual-pane interface to curate custom stock lists into **Normal Momentum Pools** (e.g., Nifty 50, Midcap 100, Sectoral ETFs) or **Defensive Cash Proxies** (e.g., Liquid/Gold ETFs, GILT funds).
- **⚙️ Strategy Builder Engine:** Define quantitative strategy parameters, target allocations, lookback periods, max holdings, and trailing stop-losses.
- **🛡️ Market Trend Filter (MTF) & 200 EMA Rules:** Built-in market regime detection on index benchmarks (e.g., NIFTY 50) using **EMA** or **Volatile Stop (VSTOP)** overlays, combined with stock-level 200-day EMA trend checks.
- **⚡ Rebalance Scanner:** Real-time scanner that evaluates stock momentum ranks, checks index regimes, generates BUY/SELL rebalance signals, and submits execution baskets directly to **FYERS**.
- **📋 Live Positions Tracker:** Complete portfolio manager to track open positions, current market prices (CMP), P&L metrics, and manual order execution/closing.

---

## 🛠️ Tech Stack

### **Frontend**
- **Framework:** React 18 (Vite)
- **Styling:** Tailwind CSS
- **Icons:** Lucide React

### **Backend**
- **Framework:** Python (FastAPI)
- **Market Data & Broker API:** FYERS API v3
- **Data Processing:** Pandas, NumPy, TA-Lib

---

## 📁 Repository Structure

```text
nse-momentum-portfolio/
├── backend/
│   ├── main.py               # FastAPI application & REST endpoints
│   ├── requirements.txt      # Python dependencies
│   ├── .env.example          # Sample environment credentials
│   └── ...
├── frontend/
│   ├── package.json          # Node dependencies
│   ├── vite.config.js        # Vite build configuration
│   ├── src/
│   │   ├── App.jsx           # Root layout & page router
│   │   ├── main.jsx          # React entry point
│   │   └── components/
│   │       ├── Dashboard.jsx
│   │       ├── GroupManager.jsx
│   │       ├── StrategyBuilder.jsx
│   │       ├── RebalanceScanner.jsx
│   │       ├── PositionsTracker.jsx
│   │       └── Navbar.jsx
│   └── public/
├── .gitignore
└── README.md
