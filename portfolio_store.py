from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path("data/momentum_portfolio.db")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db() -> None:
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS strategies(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS capital_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                event_date TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                FOREIGN KEY(strategy_id) REFERENCES strategies(id)
            );
            CREATE TABLE IF NOT EXISTS virtual_holdings(
                strategy_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                qty INTEGER NOT NULL,
                avg_price REAL NOT NULL,
                first_entry_date TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(strategy_id, symbol)
            );
            CREATE TABLE IF NOT EXISTS virtual_cash(
                strategy_id INTEGER PRIMARY KEY,
                cash REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS transactions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL,
                portfolio_mode TEXT NOT NULL,
                event_date TEXT NOT NULL,
                action TEXT NOT NULL,
                symbol TEXT,
                qty INTEGER,
                price REAL,
                amount REAL,
                rank INTEGER,
                reason TEXT,
                order_id TEXT,
                status TEXT,
                pnl REAL,
                raw_json TEXT,
                FOREIGN KEY(strategy_id) REFERENCES strategies(id)
            );
            CREATE TABLE IF NOT EXISTS managed_symbols(
                strategy_id INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                managed_since TEXT NOT NULL,
                mode TEXT NOT NULL,
                PRIMARY KEY(strategy_id, symbol, mode)
            );
            """
        )


def save_strategy(name: str, config: Dict[str, Any]) -> int:
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        row = con.execute("SELECT id FROM strategies WHERE name=?", (name,)).fetchone()
        if row:
            con.execute("UPDATE strategies SET config_json=?, updated_at=? WHERE id=?", (json.dumps(config), now, row["id"]))
            return int(row["id"])
        cur = con.execute("INSERT INTO strategies(name, config_json, created_at, updated_at) VALUES(?,?,?,?)",
                          (name, json.dumps(config), now, now))
        return int(cur.lastrowid)


def list_strategies() -> List[Dict[str, Any]]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM strategies ORDER BY name").fetchall()
    return [{"id": int(r["id"]), "name": r["name"], "config": json.loads(r["config_json"]), "updated_at": r["updated_at"]} for r in rows]


def get_strategy(strategy_id: int) -> Dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()
    return {"id": int(row["id"]), "name": row["name"], "config": json.loads(row["config_json"])} if row else None


def add_capital(strategy_id: int, amount: float, event_type: str, note: str = "") -> None:
    if amount <= 0:
        raise ValueError("Amount must be positive.")
    with _conn() as con:
        con.execute("INSERT INTO capital_ledger(strategy_id,event_date,event_type,amount,note) VALUES(?,?,?,?,?)",
                     (strategy_id, date.today().isoformat(), event_type, float(amount), note))
        row = con.execute("SELECT cash FROM virtual_cash WHERE strategy_id=?", (strategy_id,)).fetchone()
        if row:
            con.execute("UPDATE virtual_cash SET cash=cash+? WHERE strategy_id=?", (float(amount), strategy_id))
        else:
            con.execute("INSERT INTO virtual_cash(strategy_id,cash) VALUES(?,?)", (strategy_id, float(amount)))


def capital_summary(strategy_id: int) -> Dict[str, float]:
    with _conn() as con:
        rows = con.execute("SELECT event_type, SUM(amount) AS total FROM capital_ledger WHERE strategy_id=? GROUP BY event_type", (strategy_id,)).fetchall()
        cash = con.execute("SELECT cash FROM virtual_cash WHERE strategy_id=?", (strategy_id,)).fetchone()
    vals = {r["event_type"]: float(r["total"] or 0) for r in rows}
    return {
        "investment": vals.get("Investment", 0.0),
        "add_funds": vals.get("Add Funds", 0.0),
        "total_capital": vals.get("Investment", 0.0) + vals.get("Add Funds", 0.0),
        "virtual_cash": float(cash["cash"] if cash else 0.0),
    }


def get_virtual_holdings(strategy_id: int) -> List[Dict[str, Any]]:
    with _conn() as con:
        rows = con.execute("SELECT symbol,qty,avg_price,first_entry_date FROM virtual_holdings WHERE strategy_id=? ORDER BY symbol", (strategy_id,)).fetchall()
    return [dict(r) for r in rows]


def set_virtual_holding(strategy_id: int, symbol: str, qty: int, avg_price: float, entry_date: str) -> None:
    now = datetime.utcnow().isoformat()
    with _conn() as con:
        if qty <= 0:
            con.execute("DELETE FROM virtual_holdings WHERE strategy_id=? AND symbol=?", (strategy_id, symbol))
        else:
            row = con.execute("SELECT symbol FROM virtual_holdings WHERE strategy_id=? AND symbol=?", (strategy_id, symbol)).fetchone()
            if row:
                con.execute("UPDATE virtual_holdings SET qty=?, avg_price=?, updated_at=? WHERE strategy_id=? AND symbol=?",
                             (int(qty), float(avg_price), now, strategy_id, symbol))
            else:
                con.execute("INSERT INTO virtual_holdings(strategy_id,symbol,qty,avg_price,first_entry_date,updated_at) VALUES(?,?,?,?,?,?)",
                             (strategy_id, symbol, int(qty), float(avg_price), entry_date, now))


def update_virtual_cash(strategy_id: int, delta: float) -> None:
    with _conn() as con:
        row = con.execute("SELECT cash FROM virtual_cash WHERE strategy_id=?", (strategy_id,)).fetchone()
        if not row:
            con.execute("INSERT INTO virtual_cash(strategy_id,cash) VALUES(?,?)", (strategy_id, float(delta)))
        else:
            con.execute("UPDATE virtual_cash SET cash=cash+? WHERE strategy_id=?", (float(delta), strategy_id))


def record_transaction(strategy_id: int, mode: str, action: str, symbol: str = "", qty: int = 0, price: float = 0,
                       amount: float = 0, rank: int | None = None, reason: str = "", order_id: str = "",
                       status: str = "", pnl: float = 0, raw: Any = None, event_date: str | None = None) -> None:
    with _conn() as con:
        con.execute(
            "INSERT INTO transactions(strategy_id,portfolio_mode,event_date,action,symbol,qty,price,amount,rank,reason,order_id,status,pnl,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (strategy_id, mode, event_date or datetime.now().isoformat(), action, symbol, int(qty), float(price), float(amount), rank,
             reason, order_id, status, float(pnl), json.dumps(raw) if raw is not None else "")
        )


def transaction_history(strategy_id: int, mode: str | None = None):
    with _conn() as con:
        if mode:
            rows = con.execute("SELECT * FROM transactions WHERE strategy_id=? AND portfolio_mode=? ORDER BY id DESC", (strategy_id, mode)).fetchall()
        else:
            rows = con.execute("SELECT * FROM transactions WHERE strategy_id=? ORDER BY id DESC", (strategy_id,)).fetchall()
    import pandas as pd
    return pd.DataFrame([dict(r) for r in rows])


def mark_managed(strategy_id: int, symbol: str, mode: str) -> None:
    with _conn() as con:
        con.execute("INSERT OR IGNORE INTO managed_symbols(strategy_id,symbol,managed_since,mode) VALUES(?,?,?,?)",
                     (strategy_id, symbol, date.today().isoformat(), mode))


def unmark_managed(strategy_id: int, symbol: str, mode: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM managed_symbols WHERE strategy_id=? AND symbol=? AND mode=?", (strategy_id, symbol, mode))


def get_managed_symbols(strategy_id: int, mode: str) -> List[str]:
    with _conn() as con:
        rows = con.execute("SELECT symbol FROM managed_symbols WHERE strategy_id=? AND mode=? ORDER BY symbol", (strategy_id, mode)).fetchall()
    return [r["symbol"] for r in rows]
