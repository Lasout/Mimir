"""Conseil de trading : combine analyse technique et fondamentale."""

from __future__ import annotations

import pandas as pd

from .analysis.fundamental import fundamental_analysis
from .analysis.technical import technical_analysis

LABELS = [
    (0.60, "ACHAT FORT"),
    (0.25, "ACHAT"),
    (-0.25, "NEUTRE"),
    (-0.60, "PRUDENCE (réduire)"),
    (-1.01, "VENTE / ÉVITER"),
]


def _label(score: float) -> str:
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return LABELS[-1][1]


def advise(df: pd.DataFrame, symbol: str, source: str = "stock", interval: str = "1d",
           tech_weight: float = 0.65) -> dict:
    """Retourne un avis structuré : score, label, raisons, niveaux de risque.

    Horizon suggéré : day trading si intervalle intraday (< 1d), swing sinon.
    """
    tech = technical_analysis(df)
    fund = fundamental_analysis(symbol, source=source)

    if fund["available"]:
        score = tech_weight * tech["score"] + (1 - tech_weight) * fund["score"]
    else:
        score = tech["score"]

    reasons = [f"[Technique] {r}" for r in tech["reasons"]]
    if fund["available"]:
        reasons += [f"[Fondamental] {r}" for r in fund["reasons"]]

    atr_pct = tech["atr_pct"]
    stop_suggestion = None
    if atr_pct > 0:
        stop_suggestion = tech["last_close"] * (1 - max(2.0 * atr_pct, 0.02))

    intraday = interval not in ("1d", "1wk", "1mo")
    horizon = "Day trading (intraday)" if intraday else "Swing trading (quelques jours à quelques semaines)"

    if atr_pct > 0.05:
        risk = "Élevé"
    elif atr_pct > 0.025:
        risk = "Modéré"
    else:
        risk = "Faible"

    return {
        "symbol": symbol,
        "source": source,
        "score": round(float(score), 3),
        "label": _label(score),
        "technical_score": round(tech["score"], 3),
        "fundamental_score": round(fund["score"], 3) if fund["available"] else None,
        "reasons": reasons,
        "last_close": tech["last_close"],
        "rsi": round(tech["rsi"], 1),
        "adx": round(tech["adx"], 1),
        "atr_pct": round(atr_pct, 4),
        "stop_suggestion": round(stop_suggestion, 2) if stop_suggestion else None,
        "risk": risk,
        "horizon": horizon,
        "fundamental_available": fund["available"],
        "fundamental_data": fund.get("data", {}),
    }
