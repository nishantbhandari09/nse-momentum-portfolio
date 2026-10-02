from pydantic import BaseModel, Field
from typing import List, Optional
import sqlite3
import json

# Database Initialization
def init_db():
    conn = sqlite3.connect("quant_investor.db")
    cursor = conn.cursor()
    
    # Groups Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        group_type TEXT DEFAULT 'normal', -- 'normal' or 'defensive'
        constituents TEXT NOT NULL -- JSON list of symbols
    )
    """)
    
    # Strategies Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        group1_id INTEGER NOT NULL,
        group2_id INTEGER NOT NULL,
        defensive_multiplier REAL DEFAULT 1.5,
        mtf_enabled INTEGER DEFAULT 1,
        mtf_chart_type TEXT DEFAULT 'OHLC',
        mtf_filter_action TEXT DEFAULT 'Exit as per Strategy. No New Entry.',
        mtf_mode TEXT DEFAULT 'Index Filter',
        mtf_scrip TEXT DEFAULT 'NSE:NIFTY50-INDEX',
        mtf_indicator TEXT DEFAULT 'EMA', -- 'EMA' or 'VSTOP'
        mtf_indicator_period INTEGER DEFAULT 20,
        mtf_vstop_multiplier REAL DEFAULT 3.0,
        stock_ema_filter INTEGER DEFAULT 1, -- 1 = Only stocks above 200 EMA
        lookback_period INTEGER DEFAULT 252,
        max_holdings INTEGER DEFAULT 5,
        stop_loss_pct REAL DEFAULT 20.0,
        target_pct REAL DEFAULT 0.0,
        rebalance_frequency TEXT DEFAULT 'Monthly'
    )
    """)
    
    # Positions Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        strategy_id INTEGER,
        symbol TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        buy_price REAL NOT NULL,
        entry_date TEXT NOT NULL,
        status TEXT DEFAULT 'OPEN', -- 'OPEN' or 'CLOSED'
        exit_price REAL,
        exit_date TEXT
    )
    """)
    
    conn.commit()
    conn.close()

# Schemas
class GroupSchema(BaseModel):
    id: Optional[int] = None
    name: str
    group_type: str = "normal"  # "normal" or "defensive"
    constituents: List[str]

class StrategySchema(BaseModel):
    id: Optional[int] = None
    name: str
    group1_id: int
    group2_id: int
    defensive_multiplier: float = 1.5
    mtf_enabled: bool = True
    mtf_chart_type: str = "OHLC"
    mtf_filter_action: str = "Exit as per Strategy. No New Entry."
    mtf_mode: str = "Index Filter"
    mtf_scrip: str = "NSE:NIFTY50-INDEX"
    mtf_indicator: str = "EMA"  # "EMA" or "VSTOP"
    mtf_indicator_period: int = 20
    mtf_vstop_multiplier: float = 3.0
    stock_ema_filter: bool = True  # True = Stock CMP > 200 EMA
    lookback_period: int = 252
    max_holdings: int = 5
    stop_loss_pct: float = 20.0
    target_pct: float = 0.0
    rebalance_frequency: str = "Monthly"

class BacktestRequest(BaseModel):
    strategy_id: int
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD
    initial_capital: float = 100000.0