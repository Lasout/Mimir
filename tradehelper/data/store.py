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
CREATE TABLE IF NOT EXISTS watchlist (
    source    TEXT NOT NULL,
    symbol    TEXT NOT NULL,
    added_at  TEXT NOT NULL,
    PRIMARY KEY (source, symbol)
);
CREATE TABLE IF NOT EXISTS runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    summary    TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs (created_at);
CREATE TABLE IF NOT EXISTS paper_positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol      TEXT NOT NULL,
    source      TEXT NOT NULL,
    side        TEXT NOT NULL,          -- "long" | "short"
    qty         REAL NOT NULL,
    entry_price REAL NOT NULL,
    entry_time  TEXT NOT NULL,
    stop        REAL,
    target      REAL,
    strategy    TEXT,
    reason      TEXT,
    status      TEXT NOT NULL DEFAULT 'open',  -- "open" | "closed"
    exit_price  REAL,
    exit_time   TEXT,
    pnl         REAL,
    exit_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_status ON paper_positions (status);
-- Moteur paper conforme (SPEC §3.5, PAP-07) : une ligne = un trade, du signal à la sortie.
-- trade_id = hash(strategy_version, asset, signal_date) -> idempotence (PAP-05).
CREATE TABLE IF NOT EXISTS paper_trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id         TEXT NOT NULL UNIQUE,
    strategy_version TEXT NOT NULL,
    signal_date      TEXT NOT NULL,           -- jour J de génération du conseil
    asset            TEXT NOT NULL,
    source           TEXT NOT NULL,
    direction        TEXT NOT NULL,           -- "long" | "short"
    planned_entry    REAL NOT NULL,
    stop             REAL NOT NULL,
    target           REAL NOT NULL,
    risk_pct         REAL NOT NULL,           -- % du capital engagé sur le trade
    fill_price       REAL,                    -- null tant que non rempli (fill = open J+1, PAP-01)
    fill_date        TEXT,
    exit_date        TEXT,
    exit_price       REAL,
    exit_reason      TEXT,                    -- stop | target | règle de sortie | fin de période
    qty              REAL,
    fees             REAL,                    -- cumul entrée+sortie (PAP-06)
    slippage_cost    REAL,
    pnl              REAL,                    -- net de frais et slippage, en monnaie
    r_planned        REAL,
    r_realized       REAL,                    -- null tant que ouvert
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending | open | closed
    created_at       TEXT NOT NULL            -- horloge réelle (PAP-05)
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status);
CREATE TABLE IF NOT EXISTS journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    symbol      TEXT,
    kind        TEXT NOT NULL,          -- "note" | "entrée" | "sortie" | "idée"
    text        TEXT NOT NULL,
    image_path  TEXT                    -- capture d'écran éventuelle (PNG exporté)
);
CREATE INDEX IF NOT EXISTS idx_journal_created ON journal (created_at);
"""

COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._pq: bool | None = None  # pyarrow disponible ?
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------- cache Parquet
    def _parquet_ok(self) -> bool:
        if self._pq is None:
            try:
                import pyarrow  # noqa: F401

                self._pq = True
            except Exception:
                self._pq = False
        return self._pq

    def _pq_path(self, source: str, symbol: str, interval: str) -> Path:
        return self.path.parent / "parquet" / f"{source}_{symbol.upper()}_{interval}.parquet"

    def _write_parquet(self, df: pd.DataFrame, source: str, symbol: str, interval: str) -> None:
        """Snapshot Parquet (lecture columnar rapide). Best effort : silencieux si échec.

        `df` peut avoir l'index datetime OU une colonne 'ts'."""
        if not self._parquet_ok() or df.empty:
            return
        try:
            out = df.copy()
            if "ts" in out.columns:
                out["ts"] = pd.to_datetime(out["ts"])
                out = out.set_index("ts")
            else:
                out.index = pd.to_datetime(out.index)
            out.index.name = "ts"
            p = self._pq_path(source, symbol, interval)
            p.parent.mkdir(parents=True, exist_ok=True)
            out.reset_index().to_parquet(p, index=False)
        except Exception:
            pass

    def _write_parquet_full(self, source: str, symbol: str, interval: str) -> None:
        """Réécrit le snapshot Parquet depuis SQLite (complet, pas seulement l'incrément)."""
        if not self._parquet_ok():
            return
        try:
            df = self._read_sql(source, symbol, interval)
            self._write_parquet(df, source, symbol, interval)
        except Exception:
            pass

    def _read_parquet(self, source: str, symbol: str, interval: str) -> pd.DataFrame | None:
        if not self._parquet_ok():
            return None
        p = self._pq_path(source, symbol, interval)
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception:
            return None

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
            (
                source,
                symbol.upper(),
                interval,
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                float(r["Open"]),
                float(r["High"]),
                float(r["Low"]),
                float(r["Close"]),
                None if pd.isna(r["Volume"]) else float(r["Volume"]),
            )
            for ts, r in df.iterrows()
        ]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO bars (source, symbol, interval, ts, open, high, low, close, volume)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
        self._write_parquet_full(source, symbol, interval)
        return len(rows)

    # ------------------------------------------------------------- lecture
    def _read_sql(
        self,
        source: str,
        symbol: str,
        interval: str,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        """Lecture SQLite directe (source de vérité), avec colonne 'ts'."""
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
            return pd.read_sql_query(q, self._conn, params=args, parse_dates=["ts"])

    def read(
        self,
        source: str,
        symbol: str,
        interval: str,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
    ) -> pd.DataFrame:
        # cache Parquet d'abord (lecture columnar), avec garde-fou : le snapshot doit
        # couvrir au moins toutes les lignes SQLite, sinon on retombe sur SQLite
        df = self._read_parquet(source, symbol, interval)
        if df is not None:
            try:
                if len(df) < self.count(source, symbol, interval):
                    df = None  # snapshot incomplet/périmé -> SQLite
            except Exception:
                df = None
        if df is None:
            df = self._read_sql(source, symbol, interval, start, end)
        if df is None or df.empty:
            return pd.DataFrame(columns=COLUMNS)
        df = df.copy()
        df["ts"] = pd.to_datetime(df["ts"])
        if start is not None:
            df = df[df["ts"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["ts"] <= pd.Timestamp(end)]
        if df.empty:
            return pd.DataFrame(columns=COLUMNS)
        df = df.sort_values("ts").set_index("ts")
        df.columns = COLUMNS
        df.index.name = None
        return df

    def last_ts(self, source: str, symbol: str, interval: str) -> pd.Timestamp | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(ts) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval),
            ).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def first_ts(self, source: str, symbol: str, interval: str) -> pd.Timestamp | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(ts) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval),
            ).fetchone()
        return pd.Timestamp(row[0]) if row and row[0] else None

    def count(self, source: str, symbol: str, interval: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM bars WHERE source=? AND symbol=? AND interval=?",
                (source, symbol.upper(), interval),
            ).fetchone()
        return int(row[0])

    def count_source(self, source: str) -> int:
        """Nombre total de barres stockées pour une source (0 si aucune)."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM bars WHERE source=?", (source,)).fetchone()
        return int(row[0])

    def clear_bars(self) -> int:
        """Vide toutes les barres (ex. changement de mode d'ajustement des prix)
        + les snapshots Parquet. Retourne le nombre de lignes supprimées."""
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
            self._conn.execute("DELETE FROM bars")
            self._conn.commit()
        pq_dir = self.path.parent / "parquet"
        removed = 0
        if pq_dir.exists():
            for p in pq_dir.glob("*.parquet"):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return int(n)

    # ------------------------------------------------------------- meta
    def meta_get(self, key: str):
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def meta_set(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, json.dumps(value)))
            self._conn.commit()

    # ------------------------------------------------------------- watchlist
    def watch_add(self, source: str, symbol: str) -> None:
        from ..timeutil import utcnow_naive

        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO watchlist (source, symbol, added_at) VALUES (?, ?, ?)",
                (source, symbol.upper(), utcnow_naive().strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._conn.commit()

    def watch_remove(self, source: str, symbol: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM watchlist WHERE source=? AND symbol=?", (source, symbol.upper()))
            self._conn.commit()

    def watch_list(self) -> list[tuple[str, str, str]]:
        """Retourne [(source, symbol, added_at)] triés par source puis symbole."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT source, symbol, added_at FROM watchlist ORDER BY source, symbol"
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # ------------------------------------------------------------- runs (historique)
    def log_run(self, kind: str, symbol: str, summary: str, payload: dict) -> int:
        from ..timeutil import utcnow_naive

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO runs (kind, symbol, summary, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    kind,
                    symbol.upper(),
                    summary,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def list_runs(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, symbol, summary, payload, created_at FROM runs"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {"id": r[0], "kind": r[1], "symbol": r[2], "summary": r[3], "payload": json.loads(r[4]), "created_at": r[5]}
            for r in rows
        ]

    def clear_runs(self) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            self._conn.execute("DELETE FROM runs")
            self._conn.commit()
        return int(n)

    # ------------------------------------------------------------- paper trading
    def paper_add(self, pos: dict) -> int:
        from ..timeutil import utcnow_naive

        pos = dict(pos)
        pos.setdefault("entry_time", utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"))
        pos.setdefault("status", "open")
        cols = (
            "symbol",
            "source",
            "side",
            "qty",
            "entry_price",
            "entry_time",
            "stop",
            "target",
            "strategy",
            "reason",
            "status",
            "exit_price",
            "exit_time",
            "pnl",
            "exit_reason",
        )
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO paper_positions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(pos.get(c) for c in cols),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def paper_update(self, pid: int, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE paper_positions SET {sets} WHERE id=?", (*fields.values(), pid))
            self._conn.commit()

    def paper_list(self, status: str | None = None) -> list[dict]:
        q = (
            "SELECT id, symbol, source, side, qty, entry_price, entry_time, stop, target, "
            "strategy, reason, status, exit_price, exit_time, pnl, exit_reason "
            "FROM paper_positions"
        )
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        keys = (
            "id",
            "symbol",
            "source",
            "side",
            "qty",
            "entry_price",
            "entry_time",
            "stop",
            "target",
            "strategy",
            "reason",
            "status",
            "exit_price",
            "exit_time",
            "pnl",
            "exit_reason",
        )
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def paper_clear(self) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
            self._conn.execute("DELETE FROM paper_positions")
            self._conn.commit()
        return int(n)

    # ------------------------------------------- moteur paper conforme (paper_trades)
    PTRADE_COLS = (
        "trade_id",
        "strategy_version",
        "signal_date",
        "asset",
        "source",
        "direction",
        "planned_entry",
        "stop",
        "target",
        "risk_pct",
        "fill_price",
        "fill_date",
        "exit_date",
        "exit_price",
        "exit_reason",
        "qty",
        "fees",
        "slippage_cost",
        "pnl",
        "r_planned",
        "r_realized",
        "status",
        "created_at",
    )

    def ptrade_add(self, trade: dict) -> int:
        from ..timeutil import utcnow_naive

        trade = dict(trade)
        trade.setdefault("created_at", utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"))
        trade.setdefault("status", "pending")
        cols = self.PTRADE_COLS
        with self._lock:
            cur = self._conn.execute(
                f"INSERT OR IGNORE INTO paper_trades ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                tuple(trade.get(c) for c in cols),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def ptrade_get(self, trade_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(self.PTRADE_COLS)} FROM paper_trades WHERE trade_id=?",
                (trade_id,),
            ).fetchone()
        return dict(zip(self.PTRADE_COLS, row, strict=True)) if row else None

    def ptrade_update(self, trade_id: str, **fields) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE paper_trades SET {sets} WHERE trade_id=?", (*fields.values(), trade_id))
            self._conn.commit()

    def ptrade_list(self, status: str | None = None) -> list[dict]:
        q = f"SELECT {', '.join(self.PTRADE_COLS)} FROM paper_trades"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY signal_date, trade_id"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(zip(self.PTRADE_COLS, r, strict=True)) for r in rows]

    def ptrade_clear(self) -> int:
        with self._lock:
            n = self._conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
            self._conn.execute("DELETE FROM paper_trades")
            self._conn.commit()
        return int(n)

    # ------------------------------------------------------------- journal
    def journal_add(self, symbol: str | None, kind: str, text: str, image_path: str | None = None) -> int:
        from ..timeutil import utcnow_naive

        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO journal (created_at, symbol, kind, text, image_path) VALUES (?, ?, ?, ?, ?)",
                (utcnow_naive().strftime("%Y-%m-%d %H:%M:%S"), symbol, kind, text, image_path),
            )
            self._conn.commit()
        return int(cur.lastrowid)

    def journal_list(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, symbol, kind, text, image_path FROM journal"
                " ORDER BY created_at DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {"id": r[0], "created_at": r[1], "symbol": r[2], "kind": r[3], "text": r[4], "image_path": r[5]}
            for r in rows
        ]

    def journal_delete(self, jid: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM journal WHERE id=?", (jid,))
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
