"""Classe de base des stratégies de trading."""

from __future__ import annotations

from typing import Any

import pandas as pd


class Strategy:
    """Une stratégie transforme un DataFrame OHLCV en signaux.

    generate(df) doit retourner un DataFrame aligné sur df avec :
      - position : position cible {-1, 0, +1} (1 = long, -1 = short, 0 = flat)
      - stop     : stop-loss (prix) ou NaN
      - target   : take-profit (prix) ou NaN
      - reason   : explication du signal (texte, renseigné aux changements)
    """

    name = "strategy"
    label = "Stratégie"
    description = ""
    default_params: dict = {}

    def __init__(self, **params: Any) -> None:
        self.params: dict[str, Any] = {**self.default_params, **params}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    # --- utilitaires -----------------------------------------------------
    @staticmethod
    def _empty_signals(df: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({
            "position": 0,
            "stop": float("nan"),
            "target": float("nan"),
            "reason": "",
        }, index=df.index)

    @staticmethod
    def _atr_trailing_stop(df: pd.DataFrame, mult: float, direction: int) -> pd.Series:
        """Stop suiveur de type chandelier : close -/+ mult * ATR."""
        from ..indicators import atr
        a = atr(df, 14) * mult
        if direction > 0:
            return df["Close"] - a
        return df["Close"] + a
