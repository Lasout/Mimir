"""Fournisseurs de données (providers) avec chaîne de fallback configurable.

Chaque provider sait répondre pour certaines (source, intervalle) et sait télécharger
une plage [start, end]. La pagination Binance remonte l'historique par paquets de
1000 klines (jusqu'à `max_bars_per_fetch` barres).
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
import requests

BINANCE_BARS_PER_CALL = 1000


class DataError(Exception):
    pass


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """Uniformise : index DatetimeIndex trié (naive UTC), colonnes OHLCV."""
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


def yahoo_symbol(source: str, symbol: str) -> str:
    """Convertit un symbole tradeHelper en symbole Yahoo (fallback cross-classes)."""
    symbol = symbol.upper()
    if source == "crypto":
        for quote in ("USDT", "USDC", "BUSD", "USD"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return f"{symbol[:-len(quote)]}-{quote if quote != 'USDT' else 'USD'}"
        return f"{symbol}-USD"
    if source == "forex":
        return f"{symbol}=X" if len(symbol) == 6 else symbol
    return symbol


class YahooProvider:
    name = "yahoo"

    def supports(self, source: str, interval: str) -> bool:
        return source in ("stock", "crypto", "forex")

    def fetch(self, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
              interval: str, cfg: dict) -> pd.DataFrame:
        import yfinance as yf
        ysym = yahoo_symbol(source, symbol)
        kwargs = {"interval": interval, "auto_adjust": bool(cfg.get("adjust_prices", False))}
        # Yahoo limite l'historique intraday : au-delà d'un certain âge, on demande "max"
        if start.year <= 1995:
            kwargs["period"] = "max"
        else:
            kwargs["start"] = start
            kwargs["end"] = end + pd.Timedelta(days=1)
        df = yf.Ticker(ysym).history(**kwargs)
        if df is None or df.empty:
            raise DataError(f"Yahoo: aucune donnée pour {ysym}")
        return normalize(df)


class BinanceProvider:
    name = "binance"
    INTERVAL_SECONDS = {"1d": 86400, "12h": 43200, "8h": 28800, "4h": 14400, "2h": 7200,
                        "1h": 3600, "30m": 1800, "15m": 900, "5m": 300}

    def supports(self, source: str, interval: str) -> bool:
        return source == "crypto" and interval in self.INTERVAL_SECONDS

    def fetch(self, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
              interval: str, cfg: dict, session=None) -> pd.DataFrame:
        seconds = self.INTERVAL_SECONDS[interval]
        max_bars = int(cfg.get("max_bars_per_fetch", 100_000))
        timeout = cfg.get("request_timeout", 20)
        if session is None:
            session = requests.Session()
            session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tradeHelper/0.1"})
        start_ms = int(pd.Timestamp(start).timestamp() * 1000)
        end_ms = int(pd.Timestamp(end).timestamp() * 1000)
        rows: list = []
        cursor = start_ms
        pages = 0
        while cursor < end_ms and len(rows) < max_bars and pages < 500:
            limit = min(BINANCE_BARS_PER_CALL, max_bars - len(rows))
            resp = session.get("https://api.binance.com/api/v3/klines",
                               params={"symbol": symbol.upper(), "interval": interval,
                                       "startTime": cursor, "endTime": end_ms,
                                       "limit": limit}, timeout=timeout)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            rows.extend(batch)
            # avance d'une barre complète après la dernière kline reçue (startTime est inclusif)
            cursor = batch[-1][0] + seconds * 1000
            pages += 1
            time.sleep(0.05)  # politesse API
        rows = rows[:max_bars]
        if not rows:
            raise DataError(f"Binance: aucune donnée pour {symbol}")
        df = pd.DataFrame(rows, columns=[
            "open_time", "Open", "High", "Low", "Close", "Volume", "close_time",
            "qav", "n", "tbb", "tbq", "ignore"])
        df.index = pd.to_datetime(df["open_time"], unit="ms")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            df[col] = pd.to_numeric(df[col])
        return normalize(df)


class FrankfurterProvider:
    name = "frankfurter"

    def supports(self, source: str, interval: str) -> bool:
        return source == "forex" and interval == "1d"

    def fetch(self, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
              interval: str, cfg: dict) -> pd.DataFrame:
        pair = symbol.upper()
        if len(pair) != 6:
            raise DataError(f"Paire forex invalide: {pair}")
        base, quote = pair[:3], pair[3:]
        session = requests.Session()
        session.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) tradeHelper/0.1"})
        url = f"https://api.frankfurter.dev/v1/{pd.Timestamp(start).date()}..{pd.Timestamp(end).date()}"
        resp = session.get(url, params={"base": base, "symbols": quote},
                           timeout=cfg.get("request_timeout", 20))
        resp.raise_for_status()
        rates = resp.json().get("rates", {})
        if not rates:
            raise DataError(f"Frankfurter: aucune donnée pour {pair}")
        idx = pd.to_datetime(sorted(rates.keys()))
        close = pd.Series([rates[str(d.date())][quote] for d in idx], index=idx, dtype=float)
        # le Frankfurter ne fournit que la clôture : barre synthétique O=H=L=C
        return normalize(pd.DataFrame({"Open": close, "High": close, "Low": close,
                                       "Close": close, "Volume": np.nan}))


class AlphaVantageProvider:
    """Optionnel : nécessite une clé gratuite (env ALPHAVANTAGE_API_KEY)."""

    name = "alphavantage"
    DAILY_INTERVALS = {"1d", "1wk", "1mo"}

    def __init__(self, api_key: str | None):
        self.api_key = api_key

    def supports(self, source: str, interval: str) -> bool:
        return bool(self.api_key) and source == "stock" and interval in self.DAILY_INTERVALS

    def fetch(self, source: str, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
              interval: str, cfg: dict) -> pd.DataFrame:
        if not self.api_key:
            raise DataError("Alpha Vantage: pas de clé API")
        session = requests.Session()
        resp = session.get("https://www.alphavantage.co/query",
                           params={"function": "TIME_SERIES_DAILY", "symbol": symbol.upper(),
                                   "outputsize": "full", "apikey": self.api_key},
                           timeout=cfg.get("request_timeout", 20))
        resp.raise_for_status()
        payload = resp.json().get("Time Series (Daily)")
        if not payload:
            raise DataError(f"Alpha Vantage: pas de série pour {symbol}")
        idx = pd.to_datetime(sorted(payload.keys()))
        df = pd.DataFrame({
            "Open": [float(payload[d]["1. open"]) for d in idx],
            "High": [float(payload[d]["2. high"]) for d in idx],
            "Low": [float(payload[d]["3. low"]) for d in idx],
            "Close": [float(payload[d]["4. close"]) for d in idx],
            "Volume": [float(payload[d]["5. volume"]) for d in idx],
        }, index=idx)
        # agrégation hebdo/mensuelle si demandée
        if interval == "1wk":
            df = df.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min",
                                            "Close": "last", "Volume": "sum"}).dropna()
        elif interval == "1mo":
            df = df.resample("ME").agg({"Open": "first", "High": "max", "Low": "min",
                                        "Close": "last", "Volume": "sum"}).dropna()
        return normalize(df)


def providers_for(source: str, cfg: dict) -> list:
    """Retourne la liste ordonnée des providers actifs pour une source."""
    names = cfg.get("providers", {}).get(source, ["yahoo"])
    out = []
    for name in names:
        if name == "yahoo":
            out.append(YahooProvider())
        elif name == "binance":
            out.append(BinanceProvider())
        elif name == "frankfurter":
            out.append(FrankfurterProvider())
        elif name == "alphavantage":
            key = cfg.get("alphavantage_api_key")
            if key:
                out.append(AlphaVantageProvider(key))
    if not out:
        out.append(YahooProvider())
    return out
