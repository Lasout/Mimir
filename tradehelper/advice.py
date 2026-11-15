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


def advise(
    df: pd.DataFrame, symbol: str, source: str = "stock", interval: str = "1d", tech_weight: float = 0.65
) -> dict:
    """Retourne un avis structuré : score, label, raisons, niveaux de risque.

    Horizon suggéré : day trading si intervalle intraday (< 1d), swing sinon.
    """
    tech = technical_analysis(df)
    fund = fundamental_analysis(symbol, source=source)

    score = tech_weight * tech["score"] + (1 - tech_weight) * fund["score"] if fund["available"] else tech["score"]

    reasons = [f"[Technique] {r}" for r in tech["reasons"]]
    if fund["available"]:
        reasons += [f"[Fondamental] {r}" for r in fund["reasons"]]

    # calendrier des annonces (actions uniquement, pas les ETF, mis en cache 24 h)
    earnings_date = None
    from .data.universe import ETF_LIST

    if source == "stock" and symbol.upper() not in ETF_LIST:
        try:
            from .data.events import fetch_earnings_date

            earnings_date = fetch_earnings_date(symbol)
            if earnings_date is not None:
                import datetime as dt

                days = (earnings_date - dt.date.today()).days
                if 0 <= days <= 14:
                    reasons.append(
                        f"[Calendrier] Publication des résultats le {earnings_date:%d/%m/%Y} "
                        f"(dans {days} j) : volatilité attendue, prudence sur la taille de position."
                    )
                else:
                    reasons.append(f"[Calendrier] Prochaine publication des résultats le {earnings_date:%d/%m/%Y}.")
        except Exception:
            earnings_date = None

    # réunions de banques centrales à venir (BCE / FOMC), cache 24 h
    cb_events: list[dict] = []
    try:
        import datetime as dt

        from .data.events import fetch_central_bank_events

        cb_events = fetch_central_bank_events()
        for e in cb_events:
            days = (e["date"] - dt.date.today()).days
            if 0 <= days <= 14:
                reasons.append(
                    f"[Banques centrales] Réunion {e['bank']} le {e['date']:%d/%m/%Y} "
                    f"(dans {days} j) : volatilité attendue, prudence sur la taille de position."
                )
    except Exception:
        cb_events = []

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
        "earnings_date": earnings_date.isoformat() if earnings_date else None,
        "cb_events": [{"date": e["date"].isoformat(), "bank": e["bank"]} for e in cb_events[:5]],
        "fundamental_available": fund["available"],
        "fundamental_data": fund.get("data", {}),
    }
