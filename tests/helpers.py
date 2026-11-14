"""Données synthétiques pour les tests (aucun accès réseau)."""

import numpy as np
import pandas as pd


def make_ohlcv(n: int = 250, start: float = 100.0, drift: float = 0.0008, vol: float = 0.015,
               seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    close = start * np.exp(np.cumsum(rets))
    open_ = close * (1 + rng.normal(0, vol / 4, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 3, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 3, n)))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": volume}, index=idx)


def make_trend_reversal(n: int = 300, seed: int = 7) -> pd.DataFrame:
    """Hausse puis baisse nette : doit déclencher des signaux d'entrée et de sortie."""
    half = n // 2
    up = np.linspace(100, 160, half)
    down = np.linspace(160, 110, n - half)
    close = np.concatenate([up, down]) * (1 + np.random.default_rng(seed).normal(0, 0.002, n))
    idx = pd.bdate_range("2025-01-01", periods=n)
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    volume = np.full(n, 2_000_000.0)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                         "Volume": volume}, index=idx)
