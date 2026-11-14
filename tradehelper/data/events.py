"""Calendrier des annonces (earnings) pour les actions, mis en cache dans le store."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import load_config
from ..timeutil import utcnow_naive
from .store import Store, default_store


def fetch_earnings_date(symbol: str, store: Store | None = None, cfg: dict | None = None) -> dt.date | None:
    """Prochaine date de publication des résultats (Yahoo), ou None.

    Cache 24 h dans la table meta du store. Uniquement pour les actions.
    """
    cfg = cfg or load_config()
    store = store or default_store()
    key = f"earnings|{symbol.upper()}"
    cached = store.meta_get(key)
    if cached:
        fetched_at = pd.Timestamp(cached.get("fetched_at"))
        if (utcnow_naive() - fetched_at).total_seconds() < cfg.get("events_ttl_seconds", 86400):
            d = cached.get("date")
            return dt.date.fromisoformat(d) if d else None
    try:
        import yfinance as yf
        cal = yf.Ticker(symbol.upper()).calendar or {}
        dates = cal.get("Earnings Date") or []
        next_date = dates[0] if dates else None
        if isinstance(next_date, pd.Timestamp):
            next_date = next_date.date()
        elif hasattr(next_date, "date"):
            next_date = next_date.date()
    except Exception:
        next_date = None
    store.meta_set(key, {"fetched_at": utcnow_naive().isoformat(),
                         "date": next_date.isoformat() if next_date else None})
    return next_date
