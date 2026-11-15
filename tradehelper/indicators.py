"""Indicateurs techniques usuels, implémentés sur pandas (vectorisés)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss > 0, 100.0)  # que des hausses -> RSI 100
    return out


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """Retourne un DataFrame avec macd, signal, histogram."""
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({"macd": line, "signal": sig, "hist": line - sig})


def bollinger_bands(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    return pd.DataFrame(
        {
            "middle": mid,
            "upper": mid + num_std * std,
            "lower": mid - num_std * std,
            "pct_b": (close - (mid - num_std * std)) / ((mid + num_std * std) - (mid - num_std * std)),
        }
    )


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder."""
    tr = true_range(df)
    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    tr = true_range(df)
    atr_ = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr_
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean() / atr_
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def donchian(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "upper": df["High"].rolling(window=window, min_periods=window).max(),
            "lower": df["Low"].rolling(window=window, min_periods=window).min(),
            "middle": (
                df["High"].rolling(window=window, min_periods=window).max()
                + df["Low"].rolling(window=window, min_periods=window).min()
            )
            / 2.0,
        }
    )


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0.0)
    return (df["Volume"].fillna(0.0) * direction).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP cumulé depuis le début de la série (utile en intraday)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3.0
    vol = df["Volume"].fillna(0.0)
    cum_pv = (typical * vol).cumsum()
    cum_v = vol.cumsum().replace(0.0, np.nan)
    return cum_pv / cum_v


def rolling_volatility(close: pd.Series, window: int = 20, annualization: int = 252) -> pd.Series:
    """Volatilité annualisée des rendements log."""
    log_ret = np.log(close / close.shift(1))
    return log_ret.rolling(window=window, min_periods=window).std(ddof=1) * np.sqrt(annualization)


def rolling_max(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).max()


def rolling_min(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).min()


def zscore(series: pd.Series, window: int = 20) -> pd.Series:
    mean = sma(series, window)
    std = series.rolling(window=window, min_periods=window).std(ddof=0).replace(0.0, np.nan)
    return (series - mean) / std


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les indicateurs usuels au DataFrame OHLCV (colonnes préfixées)."""
    out = df.copy()
    close = out["Close"]
    out["SMA20"] = sma(close, 20)
    out["SMA50"] = sma(close, 50)
    out["EMA12"] = ema(close, 12)
    out["EMA26"] = ema(close, 26)
    out["EMA50"] = ema(close, 50)
    out["EMA200"] = ema(close, 200)
    out["RSI14"] = rsi(close, 14)
    macd_df = macd(close)
    out["MACD"] = macd_df["macd"]
    out["MACD_signal"] = macd_df["signal"]
    out["MACD_hist"] = macd_df["hist"]
    bb = bollinger_bands(close)
    out["BB_upper"] = bb["upper"]
    out["BB_middle"] = bb["middle"]
    out["BB_lower"] = bb["lower"]
    out["BB_pctb"] = bb["pct_b"]
    out["ATR14"] = atr(out, 14)
    out["ATR_pct"] = out["ATR14"] / close
    out["ADX14"] = adx(out, 14)
    don = donchian(out, 20)
    out["Donchian_upper"] = don["upper"]
    out["Donchian_lower"] = don["lower"]
    out["OBV"] = obv(out)
    out["VWAP"] = vwap(out)
    out["Vol20"] = rolling_volatility(close, 20)
    return out
