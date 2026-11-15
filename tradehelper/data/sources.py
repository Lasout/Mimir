"""Point d'entrée des données de marché.

`load_data(source, symbole, période, intervalle)` sert les données depuis le store
SQLite persistant et ne télécharge que la **plage manquante** (mise à jour incrémentale),
en parcourant la chaîne de fournisseurs configurée jusqu'au premier qui réussit.

Périodes : 1mo, 3mo, 6mo, 1y, 2y, 5y, max (max = tout l'historique disponible).
"""

from __future__ import annotations

import pandas as pd

from ..config import load_config, ttl_for_interval
from ..timeutil import utcnow_naive
from .providers import DataError, normalize, providers_for
from .store import Store, default_store

SOURCE_LABELS = {
    "stock": "Action / ETF (Yahoo Finance)",
    "crypto": "Cryptomonnaie (Binance)",
    "forex": "Forex (taux BCE)",
}

STOCK_INTERVALS = {"1d", "1wk", "1mo", "1h", "4h", "30m", "15m", "5m"}
CRYPTO_INTERVALS = {"1d", "12h", "8h", "4h", "2h", "1h", "30m", "15m", "5m"}
PERIOD_TO_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 365, "2y": 730, "5y": 1826, "max": None}

INTERVAL_TIMEDELTA = {
    "1d": pd.Timedelta(days=1), "1wk": pd.Timedelta(weeks=1), "1mo": pd.Timedelta(days=30),
    "12h": pd.Timedelta(hours=12), "8h": pd.Timedelta(hours=8), "4h": pd.Timedelta(hours=4),
    "2h": pd.Timedelta(hours=2), "1h": pd.Timedelta(hours=1), "30m": pd.Timedelta(minutes=30),
    "15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5),
}


def interval_timedelta(interval: str) -> pd.Timedelta:
    return INTERVAL_TIMEDELTA.get(interval, pd.Timedelta(days=1))


def _check_interval(source: str, interval: str) -> None:
    allowed = {"stock": STOCK_INTERVALS, "crypto": CRYPTO_INTERVALS, "forex": {"1d"}}[source]
    if interval not in allowed:
        raise DataError(f"Intervalle {interval} non supporté pour {source} (OK: {sorted(allowed)})")


def _period_range(period: str, end: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    days = PERIOD_TO_DAYS.get(period)
    if days is None:  # "max" ou inconnu -> tout l'historique
        return pd.Timestamp("1970-01-01"), end
    return end - pd.Timedelta(days=days), end


def load_data(source: str, symbol: str, period: str = "1y", interval: str = "1d",
              store: Store | None = None, cfg: dict | None = None) -> pd.DataFrame:
    """Retourne les barres OHLCV de [période] pour un actif, via le store + fournisseurs."""
    source = source.lower()
    if source not in SOURCE_LABELS:
        raise DataError(f"Source inconnue: {source}")
    symbol = symbol.strip().upper()
    if not symbol:
        raise DataError("Symbole vide.")
    _check_interval(source, interval)
    cfg = cfg or load_config()
    store = store or default_store()

    bar = interval_timedelta(interval)
    now = utcnow_naive()
    end = now.normalize() + pd.Timedelta(days=1)  # inclut la barre du jour courant
    start, _ = _period_range(period, end)

    stored = store.read(source, symbol, interval, start, end)
    meta_key = f"fetch|{source}|{symbol}|{interval}"
    head_key = f"head|{source}|{symbol}|{interval}"
    last_fetch = store.meta_get(meta_key)
    ttl = ttl_for_interval(cfg, interval)
    first_bar = store.first_ts(source, symbol, interval)
    last_bar = store.last_ts(source, symbol, interval)

    # la queue est "fraîche" si on a téléchargé récemment et qu'elle couvre jusqu'à aujourd'hui
    tail_fresh = (last_fetch is not None
                  and (now - pd.Timestamp(last_fetch)).total_seconds() < ttl
                  and last_bar is not None
                  and last_bar >= now.normalize() - bar)
    # la tête couvre la plage demandée (tolérance de 2 barres pour les week-ends),
    # ou a déjà été sondée récemment sans rien trouver avant la première barre connue
    head_meta = store.meta_get(head_key) or {}
    probed_at = pd.Timestamp(head_meta["probed_at"]) if head_meta.get("probed_at") else None
    probe_start = pd.Timestamp(head_meta["probe_start"]) if head_meta.get("probe_start") else None
    head_probed = (probed_at is not None
                   and (now - probed_at).total_seconds() < 24 * 3600
                   and probe_start is not None and probe_start <= start + 2 * bar
                   and head_meta.get("first_available") == (first_bar.isoformat() if first_bar else "none"))
    head_covered = head_probed or (first_bar is not None and first_bar <= start + 2 * bar)
    if not stored.empty and head_covered and tail_fresh:
        return stored

    # plages manquantes à télécharger : tête (historique ancien) et/ou queue (récent)
    ranges: list[tuple[pd.Timestamp, pd.Timestamp, bool]] = []  # (début, fin, est_ce_la_tête)
    if first_bar is None:
        ranges.append((start, end, True))
    else:
        if first_bar > start + 2 * bar and not head_probed:
            ranges.append((start, first_bar - bar, True))
        if not tail_fresh:
            tail_from = max(start, last_bar + bar) if last_bar is not None else start
            ranges.append((tail_from, end, False))

    errors: list[str] = []
    for rstart, rend, is_head in ranges:
        responded = False  # au moins un fournisseur a répondu (même vide) sans erreur
        for provider in providers_for(source, cfg):
            if not provider.supports(source, interval):
                continue
            try:
                fetched = provider.fetch(source, symbol, rstart, rend, interval, cfg)
                responded = True
                if fetched is not None and not fetched.empty:
                    store.upsert(fetched, source, symbol, interval)
                    store.meta_set(meta_key, utcnow_naive().isoformat())
                    if is_head:
                        new_first = store.first_ts(source, symbol, interval)
                        store.meta_set(head_key, {
                            "probed_at": utcnow_naive().isoformat(),
                            "probe_start": pd.Timestamp(rstart).isoformat(),
                            "first_available": new_first.isoformat() if new_first else "none"})
                    break
            except Exception as exc:
                errors.append(f"{provider.name}: {exc}")
        else:
            # on ne mémorise « rien avant first_bar » que si un fournisseur a VRAIMENT
            # répondu vide ; si tous ont échoué (réseau...), on retentera plus tard
            if is_head and responded:
                store.meta_set(head_key, {"probed_at": utcnow_naive().isoformat(),
                                          "probe_start": pd.Timestamp(rstart).isoformat(),
                                          "first_available": first_bar.isoformat() if first_bar else "none"})
    # si tout a échoué mais que le store contient des données -> on sert le store (mode hors-ligne)

    out = store.read(source, symbol, interval, start, end)
    if out.empty:
        raise DataError(f"Téléchargement impossible pour {symbol} ({source}, {interval}, {period}): "
                        + "; ".join(errors or ["aucune donnée"]))
    return normalize(out)


def data_range(source: str, symbol: str, interval: str = "1d",
               store: Store | None = None) -> tuple[pd.Timestamp | None, pd.Timestamp | None, int]:
    """Plage réellement stockée (pour l'affichage) : (début, fin, nb barres)."""
    store = store or default_store()
    return (store.first_ts(source, symbol.upper(), interval),
            store.last_ts(source, symbol.upper(), interval),
            store.count(source, symbol.upper(), interval))


# --- wrappers de compatibilité -------------------------------------------
def fetch_stock(symbol: str, period: str = "1y", interval: str = "1d", **kw) -> pd.DataFrame:
    return load_data("stock", symbol, period=period, interval=interval, **kw)


def fetch_crypto(symbol: str, interval: str = "1d", period: str = "1y", **kw) -> pd.DataFrame:
    return load_data("crypto", symbol, period=period, interval=interval, **kw)


def fetch_forex(pair: str, period: str = "1y", **kw) -> pd.DataFrame:
    return load_data("forex", pair, period=period, interval="1d", **kw)
