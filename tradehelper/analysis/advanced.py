"""Fondamental avancé : surprise de BPA, valorisation relative au secteur, sentiment news (RSS).

Toutes les fonctions réseau prennent un fetcher injectable (tests hors-ligne) et les
résultats sont mis en cache dans la table `meta` du store (TTL par composante).
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET

import pandas as pd

from ..config import load_config
from ..timeutil import utcnow_naive
from ..data.store import Store, default_store

# ---------------------------------------------------------------------------
# Sentiment news : lexique bilingue fr/en, polarité par titre de flux RSS
# ---------------------------------------------------------------------------

BULL_WORDS = {
    "hausse", "progresse", "bondit", "record", "explosion", "décolle", "surperforme",
    "beat", "dépass", "hausse des bénéfices", "recommandé", "achat", "buy", "upgrade",
    "hausse des objectifs", "record de ventes", "croissance", "rally", "bullish",
    "outperform", "strong buy", "relève", "excellent", "solide", "déaget",
    "rise", "gain", "surge", "beats", "upgrade", "raised guidance", "profit jump",
}
BEAR_WORDS = {
    "chute", " plonge", "effondre", "faillite", "dégringole", "avertissement",
    "warning", "downgrade", "vente", "sell", "underperform", "miss", "perte",
    "crise", "licenciements", "enquête", "amende", "recule", "sous-performance",
    "drop", "fall", "plunge", "misses", "warns", "lawsuit", "fine", "layoffs", "bearish",
    "bankruptcy", "delisting", "recall",
}

NEWS_TTL = 6 * 3600
EPS_TTL = 24 * 3600
SECTOR_TTL = 24 * 3600


def parse_rss_titles(xml_text: str) -> list[str]:
    """Extrait les titres des <item> d'un flux RSS (Yahoo Finance ou autre)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [t.text.strip() for t in root.findall(".//item/title") if t.text and t.text.strip()]


def news_sentiment_score(titles: list[str]) -> dict:
    """Score de sentiment [-1, 1] sur une liste de titres (lexique pondéré, négations ignorées)."""
    if not titles:
        return {"score": 0.0, "n": 0, "pos": 0, "neg": 0}
    pos = neg = 0
    total = 0.0
    for title in titles:
        low = f" {title.lower()} "
        b = sum(1 for w in BULL_WORDS if w in low)
        s = sum(1 for w in BEAR_WORDS if w in low)
        if b > s:
            pos += 1
            total += min(1.0, (b - s) / 2.0)
        elif s > b:
            neg += 1
            total -= min(1.0, (s - b) / 2.0)
    score = max(-1.0, min(1.0, total / len(titles)))
    return {"score": round(float(score), 3), "n": len(titles), "pos": pos, "neg": neg}


def fetch_news_titles(symbol: str, timeout: int = 10) -> list[str]:
    """Titres du flux RSS Yahoo Finance du titre (réseau)."""
    import requests
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol.upper()}&region=US&lang=en-US"
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return parse_rss_titles(r.text)


# ---------------------------------------------------------------------------
# Surprise de BPA : (résultat publié - consensus) / |consensus| sur les derniers trimestres
# ---------------------------------------------------------------------------

def eps_surprise(earnings_dates: pd.DataFrame | None) -> dict:
    """Score [-1, 1] à partir du DataFrame yfinance `get_earnings_dates`
    (colonnes 'Reported EPS' et 'EPS Estimate')."""
    if earnings_dates is None or earnings_dates.empty:
        return {"available": False, "score": 0.0, "avg_surprise_pct": None, "n": 0}
    rep = pd.to_numeric(earnings_dates.get("Reported EPS"), errors="coerce")
    est = pd.to_numeric(earnings_dates.get("EPS Estimate"), errors="coerce")
    mask = rep.notna() & est.notna() & (est.abs() > 1e-9)
    if not mask.any():
        return {"available": False, "score": 0.0, "avg_surprise_pct": None, "n": 0}
    surprises = ((rep[mask] - est[mask]) / est[mask].abs()).tail(4)
    avg = float(surprises.mean())
    score = max(-1.0, min(1.0, avg * 5.0))  # +20 % de surprise -> +1
    return {"available": True, "score": round(score, 3),
            "avg_surprise_pct": round(avg * 100, 1), "n": int(len(surprises))}


def fetch_earnings_dates_df(symbol: str, limit: int = 8) -> pd.DataFrame | None:
    """Historique des publications (yfinance), ou None. Réseau."""
    import yfinance as yf
    try:
        return yf.Ticker(symbol.upper()).get_earnings_dates(limit=limit)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Valorisation relative au secteur : médianes des pairs de même secteur
# ---------------------------------------------------------------------------

def _median(vals) -> float | None:
    vals = [v for v in vals if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not vals:
        return None
    return float(pd.Series(vals).median())


def sector_relative(info: dict, peers_info: list[dict]) -> dict:
    """Compare le PER/la marge du titre aux médianes de ses pairs (même secteur si possible)."""
    pe = info.get("trailingPE")
    if not isinstance(pe, (int, float)) or pe <= 0:
        return {"available": False, "score": 0.0, "sector_median_pe": None, "n_peers": len(peers_info)}
    sector = info.get("sector")
    peers = [p for p in peers_info if not sector or p.get("sector") in (None, sector)] or peers_info
    med_pe = _median([p.get("trailingPE") for p in peers])
    med_margin = _median([p.get("profitMargins") for p in peers])
    margin = info.get("profitMargins")
    if med_pe is None:
        return {"available": False, "score": 0.0, "sector_median_pe": None, "n_peers": len(peers)}
    rel = pe / med_pe
    val_score = max(-1.0, min(1.0, -(rel - 1.0) * 1.5))
    margin_score = 0.0
    if med_margin is not None and isinstance(margin, (int, float)):
        margin_score = max(-1.0, min(1.0, (margin - med_margin) * 4.0))
    score = 0.7 * val_score + 0.3 * margin_score
    return {"available": True, "score": round(float(score), 3), "pe": float(pe),
            "sector_median_pe": round(med_pe, 1), "n_peers": len(peers)}


def fetch_peers_info(peers: list[str], limit: int = 8, info_fetcher=None) -> list[dict]:
    """Infos yfinance d'une liste de pairs (réseau ; `info_fetcher` injectable pour les tests)."""
    if info_fetcher is None:
        def info_fetcher(sym: str) -> dict:
            import yfinance as yf
            try:
                return yf.Ticker(sym).info or {}
            except Exception:
                return {}
    out = []
    for p in peers[:limit]:
        try:
            info = info_fetcher(p)
        except Exception:
            continue
        if info:
            out.append(info)
    return out


# ---------------------------------------------------------------------------
# Orchestration + cache
# ---------------------------------------------------------------------------

def _cached(store: Store, key: str, ttl: float):
    c = store.meta_get(key)
    if c:
        try:
            if (utcnow_naive() - pd.Timestamp(c["fetched_at"])).total_seconds() < ttl:
                return c["value"]
        except Exception:
            return None
    return None


def _cache(store: Store, key: str, value):
    store.meta_set(key, {"fetched_at": utcnow_naive().isoformat(), "value": value})


def advanced_fundamental(symbol: str, source: str = "stock", store: Store | None = None,
                         cfg: dict | None = None, fetchers: dict | None = None) -> dict:
    """Combine surprise de BPA + relatif secteur + sentiment news.

    `fetchers` (tests) : {"news": fn(symbol)->list[str], "eps": fn(symbol)->DataFrame|None,
                          "peers_info": fn(peers, limit)->list[dict], "info": fn(symbol)->dict,
                          "peers": list[str]}.
    Retourne {"available", "score", "reasons", "components": [(poids, score)], "data"}.
    """
    if source != "stock":
        return {"available": False, "score": 0.0, "reasons": [], "components": [], "data": {}}
    cfg = cfg or load_config()
    store = store or default_store()
    f = fetchers or {}
    sym = symbol.upper()
    components: list[tuple[float, float]] = []
    reasons: list[str] = []
    data: dict = {}

    # --- sentiment news (RSS) ---
    try:
        titles = _cached(store, f"adv.news|{sym}", NEWS_TTL)
        if titles is None:
            fn = f.get("news") or fetch_news_titles
            titles = fn(sym)
            _cache(store, f"adv.news|{sym}", titles)
        ns = news_sentiment_score(titles)
        data["news"] = ns
        if ns["n"]:
            components.append((0.10, ns["score"]))
            polar = "positif" if ns["score"] > 0.15 else ("négatif" if ns["score"] < -0.15 else "neutre")
            reasons.append(f"Sentiment news ({ns['n']} titres RSS, {ns['pos']} positifs / {ns['neg']} négatifs) : {polar} ({ns['score']:+.2f}).")
    except Exception:
        pass

    # --- surprise de BPA ---
    try:
        edf = _cached(store, f"adv.eps|{sym}", EPS_TTL)
        if edf is None:
            fn = f.get("eps") or fetch_earnings_dates_df
            edf = fn(sym)
            _cache(store, f"adv.eps|{sym}", edf.to_dict("list") if edf is not None else None)
        elif isinstance(edf, dict):
            edf = pd.DataFrame(edf) if edf else None
        eps = eps_surprise(edf)
        data["eps_surprise"] = eps
        if eps["available"]:
            components.append((0.10, eps["score"]))
            reasons.append(f"Surprise de BPA moyenne sur {eps['n']} trimestre(s) : {eps['avg_surprise_pct']:+.1f} % vs consensus.")
    except Exception:
        pass

    # --- valorisation relative au secteur ---
    try:
        info = _cached(store, f"adv.info|{sym}", SECTOR_TTL)
        if info is None:
            fn = f.get("info")
            if fn is None:
                import yfinance as yf
                try:
                    info = yf.Ticker(sym).info or {}
                except Exception:
                    info = {}
            else:
                info = fn(sym)
            _cache(store, f"adv.info|{sym}", info)
        peers = f.get("peers")
        if peers is None:
            from ..data.universe import universe_for
            peers = [s for s in universe_for("stock") if s != sym][:12]
        pinfo = _cached(store, f"adv.peers|{sym}", SECTOR_TTL)
        if pinfo is None:
            pinfo = fetch_peers_info(peers, limit=8, info_fetcher=f.get("peers_info"))
            _cache(store, f"adv.peers|{sym}", pinfo)
        rel = sector_relative(info or {}, pinfo or [])
        data["sector_relative"] = rel
        if rel["available"]:
            components.append((0.10, rel["score"]))
            reasons.append(f"PER {rel['pe']:.1f} vs médiane secteur {rel['sector_median_pe']:.1f} "
                           f"({rel['n_peers']} pairs) : {'sous' if rel['pe'] < rel['sector_median_pe'] else 'sur'}valorisé.")
    except Exception:
        pass

    if not components:
        return {"available": False, "score": 0.0, "reasons": [], "components": [], "data": data}
    total_w = sum(w for w, _ in components)
    score = sum(w * s for w, s in components) / total_w if total_w else 0.0
    return {"available": True, "score": round(float(max(-1.0, min(1.0, score))), 3),
            "reasons": reasons, "components": components, "data": data}
