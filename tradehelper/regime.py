"""Régime de marché — SPEC §3.6 AUD-05.

Taguer chaque métrique du régime de la période testée (haussier / plat / baissier,
déduit de la tendance d'un indice de référence ou de l'actif lui-même). Règle :
aucune conclusion préliminaire hors échantillon couvrant **au moins 2 régimes**.
"""

from __future__ import annotations

import pandas as pd

REGIMES = ("haussier", "plat", "baissier")


def detect_regime(df: pd.DataFrame, window: int = 50, slope_bars: int = 10, threshold: float = 0.05) -> dict:
    """Classifie le régime de la fenêtre récente de `df` (colonnes OHLCV + Close).

    - rendement sur `window` barres + position vs SMA(window) + pente de la SMA ;
    - haussier : ret > +threshold et close > SMA ; baissier : ret < -threshold et
      close < SMA ; sinon plat.
    """
    close = df["Close"].dropna()
    n = len(close)
    w = min(window, max(2, n // 2))
    sma = close.rolling(w).mean()
    last = float(close.iloc[-1])
    ref = float(sma.iloc[-1]) if pd.notna(sma.iloc[-1]) else last
    ret = last / float(close.iloc[-w]) - 1.0 if n > w else 0.0
    if len(sma.dropna()) > slope_bars:
        s0 = float(sma.dropna().iloc[-slope_bars])
        slope = (ref - s0) / s0 if s0 else 0.0
    else:
        slope = 0.0
    if ret > threshold and last > ref and slope > 0:
        label = "haussier"
    elif ret < -threshold and last < ref and slope < 0:
        label = "baissier"
    else:
        label = "plat"
    return {
        "label": label,
        "ret_window": float(ret),
        "close_vs_sma": last / ref - 1.0 if ref else 0.0,
        "sma_slope": float(slope),
        "window": w,
    }


def regime_coverage(labels: list[str]) -> dict:
    """Couverture des régimes sur un échantillon de labels (ex. lignes du screener).

    AUD-05 : `ok` = au moins 2 régimes distincts couverts."""
    counts = {r: labels.count(r) for r in REGIMES}
    covered = sum(1 for r in REGIMES if counts[r] > 0)
    return {"counts": counts, "n_regimes": covered, "ok": covered >= 2}
