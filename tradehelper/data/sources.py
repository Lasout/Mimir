"""Sources de données ouvertes (gratuites, sans clé API).

- Actions / ETF / indices : Yahoo Finance via yfinance
- Crypto : API publique Binance (klines)
- Forex : taux de change quotidiens de la BCE via frankfurter.dev

Toutes les séries sont mises en cache en CSV pour fonctionner hors-ligne
et limiter les appels réseau.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

CACHE_DIR = Path.home() / ".cache" / "tradehelper"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

SOURCE_LABELS = {
    "stock": "Action / ETF (Yahoo Finance)",
    "crypto": "Cryptomonnaie (Binance)",
    "forex": "Forex (taux BCE)",
}

STOCK_INTERVALS = {"1d", "1wk", "1mo", "1h", "4h", "30m", "15m", "5m"}
CRYPTO_INTERVALS = {"1d", "12h", "8h", "4h", "2h", "1h", "30m", "15m", "5m"}
PERIOD_TO_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 365, "2y": 730, "5y": 1826, "max": 3650}

BINANCE_BARS_PER_CALL = 1000

_session = requests.Session()
_session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tradeHelper/0.1"})


class DataError(Exception):
    """Erreur de récupération de données."""


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Uniformise : index DatetimeIndex trié (naive), colonnes OHLCV."""
    df = df.copy()
    df.columns = [str(c).strip().capitalize() if isinstance(c, str) else c for c in df.columns]
    for col in ("Open", "High", "Low", "Close"):
        if col not in df.columns:
            raise DataError(f"Colonne manquante: {col}")
    if "Volume" not in df.columns:
        df["Volume"] = np.nan
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_convert("UTC").tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.dropna(subset=["Close"])


def _cache_path(source: str, symbol: str, interval: str, period: str) -> Path:
    key = f"{source}|{symbol.upper()}|{interval}|{period}"
    return CACHE_DIR / (hashlib.md5(key.encode()).hexdigest() + ".csv")


def _cache_ttl(interval: str) -> float:
    """Durée de validité du cache en secondes."""
    if interval in ("1d", "1wk", "1mo"):
        return 6 * 3600  # journalier : 6 h
    return 300  # intraday : 5 min


def _read_cache(path: Path, interval: str) -> pd.DataFrame | None:
    if not path.exists():
        return None
    if time.time() - path.stat().st_mtime > _cache_ttl(interval):
        return None
    try:
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return _normalize(df)
    except Exception:
        return None


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    try:
        df.to_csv(path)
    except Exception:
        pass


def fetch_stock(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Historique OHLCV d'une action/ETF via Yahoo Finance."""
    import yfinance as yf

    if interval not in STOCK_INTERVALS:
        raise DataError(f"Intervalle non supporté pour les actions: {interval}")
    path = _cache_path("stock", symbol, interval, period)
    cached = _read_cache(path, interval)
    if cached is not None:
        return cached
    try:
        df = yf.Ticker(symbol.upper()).history(period=period, interval=interval, auto_adjust=False)
    except Exception as exc:  # réseau, rate-limit...
        df = None
        if path.exists():
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            df = _normalize(df)
        else:
            raise DataError(f"Téléchargement impossible pour {symbol}: {exc}") from exc
    if df is None or df.empty:
        raise DataError(f"Aucune donnée pour {symbol} (période={period}, intervalle={interval})")
    df = _normalize(df)
    _write_cache(path, df)
    return df


def fetch_crypto(symbol: str = "BTCUSDT", interval: str = "1d", period: str = "1y") -> pd.DataFrame:
    """Klines Binance (API publique). symbol ex: BTCUSDT, ETHUSDT."""
    if interval not in CRYPTO_INTERVALS:
        raise DataError(f"Intervalle non supporté pour la crypto: {interval}")
    path = _cache_path("crypto", symbol, interval, period)
    cached = _read_cache(path, interval)
    if cached is not None:
        return cached
    days = PERIOD_TO_DAYS.get(period, 365)
    seconds = {"1d": 86400, "12h": 43200, "8h": 28800, "4h": 14400, "2h": 7200,
               "1h": 3600, "30m": 1800, "15m": 900, "5m": 300}[interval]
    limit = min(BINANCE_BARS_PER_CALL, max(30, int(days * 86400 / seconds)))
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - limit * seconds * 1000
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol.upper(), "interval": interval, "startTime": start_ms,
              "endTime": end_ms, "limit": BINANCE_BARS_PER_CALL}
    try:
        resp = _session.get(url, params=params, timeout=20)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as exc:
        if path.exists():
            return _normalize(pd.read_csv(path, index_col=0, parse_dates=True))
        raise DataError(f"Binance inaccessible pour {symbol}: {exc}") from exc
    if not rows:
        raise DataError(f"Aucune donnée Binance pour {symbol}")
    df = pd.DataFrame(rows, columns=[
        "open_time", "Open", "High", "Low", "Close", "Volume", "close_time",
        "qav", "n", "tbb", "tbq", "ignore"])
    df.index = pd.to_datetime(df["open_time"], unit="ms")
    for col in ("Open", "High", "Low", "Close", "Volume"):
        df[col] = pd.to_numeric(df[col])
    df = _normalize(df)
    _write_cache(path, df)
    return df


def fetch_forex(pair: str = "EURUSD", period: str = "1y") -> pd.DataFrame:
    """Taux de change quotidien (BCE). pair ex: EURUSD, EURJPY.

    Le Frankfurter ne fournit que le cours de clôture : on construit une
    barre journalière synthétique (O=H=L=C) suffisante pour l'analyse daily.
    """
    pair = pair.upper()
    if len(pair) != 6:
        raise DataError(f"Paire forex invalide: {pair} (ex: EURUSD)")
    base, quote = pair[:3], pair[3:]
    path = _cache_path("forex", pair, "1d", period)
    cached = _read_cache(path, "1d")
    if cached is not None:
        return cached
    days = PERIOD_TO_DAYS.get(period, 365)
    end = pd.Timestamp.utcnow().normalize()
    start = end - pd.Timedelta(days=days + 10)
    url = f"https://api.frankfurter.dev/v1/{start.date()}..{end.date()}"
    try:
        resp = _session.get(url, params={"base": base, "symbols": quote}, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:
        if path.exists():
            return _normalize(pd.read_csv(path, index_col=0, parse_dates=True))
        raise DataError(f"Frankfurter inaccessible pour {pair}: {exc}") from exc
    rates = payload.get("rates", {})
    if not rates:
        raise DataError(f"Aucune donnée forex pour {pair}")
    idx = pd.to_datetime(sorted(rates.keys()))
    close = pd.Series([rates[str(d.date())][quote] for d in idx], index=idx, dtype=float)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": np.nan})
    df = _normalize(df)
    _write_cache(path, df)
    return df


def load_data(source: str, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    """Point d'entrée unique. source ∈ {stock, crypto, forex}."""
    source = source.lower()
    if source == "stock":
        return fetch_stock(symbol, period=period, interval=interval)
    if source == "crypto":
        return fetch_crypto(symbol, interval=interval, period=period)
    if source == "forex":
        if interval not in ("1d",):
            raise DataError("Le forex n'est disponible qu'en journalier (données BCE).")
        return fetch_forex(pair=symbol, period=period)
    raise DataError(f"Source inconnue: {source}")
