"""Stockage persistant des séries OHLCV en SQLite.

Une seule table `bars` (clé primaire source/symbol/interval/ts) + une table `meta`
(clé-valeur JSON) pour les timestamps de dernier téléchargement et le cache du
calendrier des annonces. Connexion thread-safe (lock interne).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    source   TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts       TEXT NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL NOT NULL,
    volume   REAL,
    PRIMARY KEY (source, symbol, interval, ts)
);
CREATE INDEX IF NOT EXISTS idx_bars_lookup ON bars (source, symbol, interval, ts);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- écriture
    def upsert(self, df: pd.DataFrame, source: str, symbol: str, interval: str) -> int:
        """Insère/remplace des barres. Retourne le nombre de lignes écrites."""
        if df.empty:
            return 0
        df = df.copy()
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        rows = [
            (source, symbol.upper(), interval, ts.strftime("%Y-%m-%d %H:%M:%S"),
             float(r["Open"]), float(r["High"]), float(r["Low"]), float(r["Close"]),
             None if pd.isna(r["Volume"]) else float(r["Volume"]))
            for ts, r in df.iterrows()
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO bars (source, symbol, interval, ts, open, high, low, close, volume)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            self._conn.commit()
        return len(rows)

    # ------------------------------------------------------------- lecture
    def read(self, source: str, symbol: str, interval: str,
             start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.DataFrame:
        q = "SELECT ts, open, high, low, close, volume FROM bars WHERE source=? AND symbol=? AND interval=?"
        args: list = [source, symbol.upper(), interval]
        if start is not None:
            q += " AND ts >= ?"
            args.append(pd.Timestamp(start).strftime("%Y-%m-%d %H:%M:%S"))
        if end is not None:
            q += " AND ts <= ?"
            args.append(pd.Timestamp(end).strftime("%Y-%m-%d %H:%M:%S"))
        q += " ORDER BY ts"
        with self._lock:
            df = pd.read_sql_query(q, self._conn, params=args, parse_dates=["ts"])
        if df.empty:
            return pd.DataFrame(columns=COLUMNS)
        df = df.set_index("ts")
        df.columns = COLUMNS
        df.index.name = None
        return df

    def last_ts(self, source: str, symbol: str, interval: str) -> pd.Timestamp | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval)).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def first_ts(self, source: str, symbol: str, interval: str) -> pd.Timestamp | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval)).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def count(self, source: str, symbol: str, interval: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval)).fetchone()
        return int(row[0])

    # ------------------------------------------------------------- meta
    def meta_get(self, key: str):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def meta_set(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                               (key, json.dumps(value)))
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_default_store: Store | None = None
_default_lock = threading.Lock()


def default_store() -> Store:
    """Store singleton construit depuis la configuration."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            from ..config import load_config
            _default_store = Store(load_config()["db_path"])
        return _default_store
