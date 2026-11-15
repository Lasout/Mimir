"""Analyse fondamentale à partir des données publiques Yahoo Finance.

Fonctionne surtout pour les actions/ETF. Pour crypto/forex, retourne une
structure vide (le score fondamental n'est alors pas pris en compte).
"""

from __future__ import annotations

import math


def _safe(info: dict, key: str):
    v = info.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def fundamental_analysis(symbol: str, source: str = "stock") -> dict:
    """Retourne score [-1, 1], raisons, et données clés. Sans réseau -> vide."""
    if source != "stock":
        return {"available": False, "score": 0.0, "reasons": ["Analyse fondamentale non disponible pour cette classe d'actifs."], "data": {}}

    import yfinance as yf

    reasons: list[str] = []
    scores: list[tuple[float, float]] = []  # (poids, score)
    data: dict = {}
    try:
        ticker = yf.Ticker(symbol.upper())
        info = {}
        try:
            info = ticker.info or {}
        except Exception:
            info = {}
        if not info:
            # repli sur fast_info (plus léger)
            fi = ticker.fast_info
            info = {
                "trailingPE": getattr(fi, "trailing_pe", None),
                "currentPrice": getattr(fi, "last_price", None),
            }
    except Exception as exc:
        return {"available": False, "score": 0.0,
                "reasons": [f"Données fondamentales indisponibles ({exc})."], "data": {}}

    pe = _safe(info, "trailingPE")
    sector_pe = _safe(info, "sector PE") or _safe(info, "industryPE")
    peg = _safe(info, "pegRatio")
    margin = _safe(info, "profitMargins")
    op_margin = _safe(info, "operatingMargins")
    rev_growth = _safe(info, "revenueGrowth")
    earn_growth = _safe(info, "earningsGrowth")
    debt_eq = _safe(info, "debtToEquity")
    rec = _safe(info, "recommendationKey")
    target = _safe(info, "targetMeanPrice")
    price = _safe(info, "currentPrice") or _safe(info, "regularMarketPrice")
    fwd_pe = _safe(info, "forwardPE")

    data = {"pe": pe, "forward_pe": fwd_pe, "peg": peg, "profit_margin": margin,
            "operating_margin": op_margin, "revenue_growth": rev_growth,
            "earnings_growth": earn_growth, "debt_to_equity": debt_eq,
            "recommendation": rec, "target_mean_price": target, "price": price,
            "sector": _safe(info, "sector"), "industry": _safe(info, "industry")}

    # Valorisation
    if pe is not None and pe > 0:
        if sector_pe and sector_pe > 0:
            rel = pe / sector_pe
            val_score = _clip(-(rel - 1.0) * 1.5)  # moins cher que le secteur -> +
            reasons.append(f"PER {pe:.1f} vs secteur {sector_pe:.1f} ({'sous' if rel < 1 else 'sur'}valorisé).")
        else:
            val_score = _clip((25.0 - pe) / 25.0)
            reasons.append(f"PER de {pe:.1f}.")
        scores.append((0.30, val_score))
    if peg is not None and 0 < peg < 5:
        scores.append((0.15, _clip((2.0 - peg) / 2.0)))
        reasons.append(f"PEG de {peg:.2f} ({'croissance à prix raisonnable' if peg < 1.5 else 'croissance coûteuse'}).")

    # Qualité / croissance
    if margin is not None:
        scores.append((0.15, _clip(margin * 4.0)))
        reasons.append(f"Marge nette de {margin * 100:.1f} %.")
    if rev_growth is not None:
        scores.append((0.15, _clip(rev_growth * 5.0)))
        reasons.append(f"Croissance du chiffre d'affaires : {rev_growth * 100:+.1f} %.")
    if earn_growth is not None:
        scores.append((0.10, _clip(earn_growth * 5.0)))
        reasons.append(f"Croissance du BPA : {earn_growth * 100:+.1f} %.")

    # Structure financière
    if debt_eq is not None:
        scores.append((0.10, _clip((150.0 - debt_eq) / 150.0)))
        reasons.append(f"Dette/capitaux propres : {debt_eq:.0f} %.")

    # Avis analystes + objectif de cours
    rec_map = {"strong_buy": 1.0, "buy": 0.6, "outperform": 0.5, "hold": 0.0,
               "market_perform": 0.0, "underperform": -0.5, "sell": -0.8, "strong_sell": -1.0}
    if rec in rec_map:
        scores.append((0.05, rec_map[rec]))
        reasons.append(f"Recommandation médiane des analystes : {rec}.")
    if price and target and price > 0:
        upside = target / price - 1.0
        scores.append((0.05, _clip(upside * 3.0)))
        reasons.append(f"Objectif de cours moyen {target:.2f} ({upside * 100:+.1f} % vs cours actuel).")

    # Composantes avancées (surprise de BPA, relatif secteur, sentiment news RSS) :
    # mises en cache dans le store, jamais bloquantes en cas d'échec réseau.
    try:
        from .advanced import advanced_fundamental
        adv = advanced_fundamental(symbol, source=source)
        if adv.get("available"):
            for w, s in adv["components"]:
                scores.append((w, s))
            reasons.extend(f"[Avancé] {r}" for r in adv["reasons"])
            data.update(adv.get("data", {}))
    except Exception:
        pass

    if not scores:
        return {"available": False, "score": 0.0,
                "reasons": ["Données fondamentales insuffisantes pour ce titre."], "data": data}

    total_w = sum(w for w, _ in scores)
    score = sum(w * s for w, s in scores) / total_w if total_w > 0 else 0.0
    return {"available": True, "score": float(_clip(score)), "reasons": reasons, "data": data}
