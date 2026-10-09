"""Paper-trade journal (SQLite). The doc's rule: log 20-30 setups before sizing up."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from .market import now_ist

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,           -- CALL or PUT
    contract TEXT,
    reason TEXT,
    entry_time TEXT NOT NULL,
    entry_underlying REAL,
    stop_underlying REAL,
    target_underlying REAL,
    entry_premium REAL,
    lots INTEGER DEFAULT 1,
    lot_size INTEGER DEFAULT 1,
    exit_time TEXT,
    exit_underlying REAL,
    exit_premium REAL,
    exit_reason TEXT,
    pnl REAL,
    notes TEXT
);
"""


class Journal:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def open_trade(self, data: dict) -> dict:
        fields = ["symbol", "direction", "contract", "reason", "entry_underlying", "stop_underlying",
                  "target_underlying", "entry_premium", "lots", "lot_size", "notes"]
        values = [data.get(f) for f in fields]
        with self.lock:
            cur = self.conn.execute(
                f"INSERT INTO trades ({','.join(fields)}, entry_time) VALUES ({','.join('?' * len(fields))}, ?)",
                (*values, now_ist().isoformat(timespec="seconds")))
            self.conn.commit()
            tid = cur.lastrowid
        return self.get(tid)

    def close_trade(self, tid: int, exit_premium: float | None, exit_underlying: float | None, reason: str = "") -> dict:
        t = self.get(tid)
        if not t:
            raise KeyError(tid)
        pnl = None
        if exit_premium is not None and t["entry_premium"] is not None:
            pnl = (exit_premium - t["entry_premium"]) * (t["lots"] or 1) * (t["lot_size"] or 1)
        elif exit_underlying is not None and t["entry_underlying"] is not None:
            sign = 1 if t["direction"] == "CALL" else -1
            pnl = sign * (exit_underlying - t["entry_underlying"]) * (t["lots"] or 1) * (t["lot_size"] or 1)
        with self.lock:
            self.conn.execute(
                "UPDATE trades SET exit_time=?, exit_premium=?, exit_underlying=?, exit_reason=?, pnl=? WHERE id=?",
                (now_ist().isoformat(timespec="seconds"), exit_premium, exit_underlying, reason, pnl, tid))
            self.conn.commit()
        return self.get(tid)

    def delete(self, tid: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM trades WHERE id=?", (tid,))
            self.conn.commit()

    def get(self, tid: int) -> dict | None:
        rows = self._rows("SELECT * FROM trades WHERE id=?", (tid,))
        return rows[0] if rows else None

    def all(self) -> list[dict]:
        return self._rows("SELECT * FROM trades ORDER BY id DESC")

    def open_trades(self) -> list[dict]:
        return self._rows("SELECT * FROM trades WHERE exit_time IS NULL")

    def today(self, day: datetime | None = None) -> tuple[int, float]:
        d = (day or now_ist()).date().isoformat()
        rows = self._rows("SELECT pnl FROM trades WHERE exit_time LIKE ? AND pnl IS NOT NULL", (f"{d}%",))
        return sum(1 for r in rows if r["pnl"] < 0), sum(r["pnl"] for r in rows)

    def stats(self) -> dict:
        closed = [r for r in self._rows("SELECT pnl FROM trades WHERE pnl IS NOT NULL")]
        wins = [r["pnl"] for r in closed if r["pnl"] > 0]
        losses = [r["pnl"] for r in closed if r["pnl"] <= 0]
        n = len(closed)
        avg_w = sum(wins) / len(wins) if wins else 0
        avg_l = sum(losses) / len(losses) if losses else 0
        return {
            "trades": n, "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / n * 100, 1) if n else 0,
            "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "expectancy": round(sum(r["pnl"] for r in closed) / n, 2) if n else 0,
            "net_pnl": round(sum(r["pnl"] for r in closed), 2),
            "target_sample": 30,
        }
