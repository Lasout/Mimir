"""Calendrier des annonces (earnings) + réunions de banques centrales (BCE / Fed).

Les deux sont mis en cache dans le store. Les parsers (HTML -> dates) sont purs et
testés hors-ligne avec des fixtures ; le réseau passe par un fetcher injectable.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re

import pandas as pd

from ..config import load_config
from ..timeutil import utcnow_naive
from .store import Store, default_store

FED_CAL_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
ECB_CAL_URL = "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html"

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def parse_fed_calendar(html: str) -> list[dict]:
    """Parse la page FOMC (federalreserve.gov) : sections « 2026 FOMC Meetings » puis
    « March 17-18* ... ». Retourne [{"date", "bank": "FOMC"}] — jour 2 (décision).
    """
    plain = _plain(html)
    heads = [(m.start(), int(m.group(1))) for m in re.finditer(r"((?:19|20)\d{2})\s+FOMC Meetings", plain)]
    events: list[dict] = []
    for i, (pos, year) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(plain)
        for m in re.finditer(r"([A-Z][a-z]+)\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\b", plain[pos:end]):
            month = _MONTHS.get(m.group(1).lower())
            if month:
                events.append({"date": dt.date(year, month, int(m.group(3))), "bank": "FOMC"})
    return events


def parse_ecb_calendar(html: str) -> list[dict]:
    """Parse la page « Meetings of the Governing Council » de la BCE.

    Texte aplati de la forme « 16/12/2026 Governing Council of the ECB: monetary policy
    meeting in Frankfurt (Day 1) 17/12/2026 ... (Day 2), followed by press conference ».
    On ne garde que les jours de décision (Day 2 + conférence de presse), en excluant
    les « non-monetary policy meeting ».
    """
    plain = _plain(html)
    date_matches = list(re.finditer(r"\b(\d{2})/(\d{2})/(\d{4})\b", plain))
    events: list[dict] = []
    for i, m in enumerate(date_matches):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        end = date_matches[i + 1].start() if i + 1 < len(date_matches) else len(plain)
        desc = plain[m.end() : end].lower()
        if "monetary policy meeting" in desc and "non-monetary" not in desc:
            events.append({"date": dt.date(y, mo, d), "bank": "BCE", "decision": "day 2" in desc})
    day2 = [e for e in events if e["decision"]]
    return [{"date": e["date"], "bank": e["bank"]} for e in (day2 or events)]


def fetch_central_bank_events(
    store: Store | None = None, cfg: dict | None = None, fetcher=None, horizon_days: int = 90
) -> list[dict]:
    """Prochaines réunions BCE/Fed (décision) à venir, triées, dans `horizon_days` jours.

    Cache 24 h dans le store (meta 'cb_events'). `fetcher(url) -> html` injectable.
    """
    cfg = cfg or load_config()
    store = store or default_store()
    if fetcher is None:

        def fetcher(url: str) -> str:
            import requests

            r = requests.get(
                url, timeout=cfg.get("request_timeout", 20), headers={"User-Agent": "Mozilla/5.0 tradeHelper/0.1"}
            )
            r.raise_for_status()
            return r.text

    key = "cb_events"
    cached = store.meta_get(key)
    if cached:
        fetched_at = pd.Timestamp(cached.get("fetched_at"))
        if (utcnow_naive() - fetched_at).total_seconds() < cfg.get("events_ttl_seconds", 86400):
            return [{"date": dt.date.fromisoformat(e["date"]), "bank": e["bank"]} for e in cached.get("events", [])]
    events: list[dict] = []
    with contextlib.suppress(Exception):
        events += parse_fed_calendar(fetcher(FED_CAL_URL))
    with contextlib.suppress(Exception):
        events += parse_ecb_calendar(fetcher(ECB_CAL_URL))
    today = dt.date.today()
    events = sorted({(e["date"], e["bank"]) for e in events})
    out = [
        {"date": d, "bank": b}
        for d, b in events
        if today - dt.timedelta(days=1) <= d <= today + dt.timedelta(days=horizon_days)
    ]
    store.meta_set(
        key,
        {
            "fetched_at": utcnow_naive().isoformat(),
            "events": [{"date": e["date"].isoformat(), "bank": e["bank"]} for e in out],
        },
    )
    return out


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
        if isinstance(next_date, pd.Timestamp) or hasattr(next_date, "date"):
            next_date = next_date.date()
    except Exception:
        next_date = None
    store.meta_set(
        key, {"fetched_at": utcnow_naive().isoformat(), "date": next_date.isoformat() if next_date else None}
    )
    return next_date
