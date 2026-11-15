"""Exports de rapports : PNG (graphiques), CSV (screener, trades), TXT (rapports texte).

Fonctions pures (testables hors-ligne) ; la GUI ne fait qu'appeler
`filedialog.asksaveasfilename` puis ces fonctions.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd


def export_figure_png(fig, path: str | Path) -> Path:
    """Sauvegarde une Figure matplotlib en PNG (fond = thème courant)."""
    path = Path(path)
    fig.savefig(path, dpi=150)
    return path


# colonnes conservées dans l'ordre d'affichage du screener
SCREENER_COLS = [
    "symbol",
    "type",
    "last_close",
    "label",
    "score",
    "strategy_label",
    "sharpe",
    "total_return",
    "n_trades",
    "win_rate",
    "position",
    "horizon",
    "entry",
    "stop",
    "target",
    "rsi",
    "adx",
    "atr_pct",
    "risk",
    "best_strategy",
    "error",
]


def export_screener_csv(rows: list[dict], path: str | Path) -> Path:
    path = Path(path)
    extra = sorted({k for r in rows for k in r} - set(SCREENER_COLS))
    cols = [c for c in SCREENER_COLS if any(c in r for r in rows)] + extra
    if not cols:
        cols = sorted({k for r in rows for k in r})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return path


def export_trades_csv(trades: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    df = trades.copy()
    for c in ("entry_time", "exit_time"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c]).dt.strftime("%Y-%m-%d %H:%M:%S")
    df.to_csv(path, index=False, encoding="utf-8")
    return path


def export_text_report(text: str, path: str | Path) -> Path:
    path = Path(path)
    path.write_text(text, encoding="utf-8")
    return path
