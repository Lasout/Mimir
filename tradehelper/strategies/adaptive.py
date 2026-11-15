"""Stratégie à régime adaptatif : détecte tendance vs range via l'ADX.

- régime tendance (ADX > seuil) : croisement de moyennes mobiles (trend following)
- régime range (ADX <= seuil) : mean-reversion RSI
Le régime est réévalué à chaque barre (causal : ADX de la barre courante).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import adx, ema, rsi
from .base import Strategy


class AdaptiveRegime(Strategy):
    name = "adaptive_regime"
    label = "Régime adaptatif (ADX : tendance=MA cross, range=RSI)"
    description = "ADX > seuil -> suivi de tendance (EMA) ; sinon mean-reversion RSI. Stop ATR."
    default_params = {
        "adx_threshold": 25.0,
        "fast": 12,
        "slow": 26,
        "rsi_oversold": 30.0,
        "rsi_exit": 50.0,
        "atr_stop": 2.5,
    }

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = self._empty_signals(df)
        close = df["Close"]
        a = adx(df, 14)
        fast = ema(close, int(p["fast"]))
        slow = ema(close, int(p["slow"]))
        r = rsi(close, 14)
        trend = a > float(p["adx_threshold"])

        pos = np.zeros(len(df))
        reason = pd.Series("", index=df.index)
        in_pos = 0
        for i in range(1, len(df)):
            is_trend = bool(pd.notna(trend.iloc[i]) and trend.iloc[i])
            if is_trend:
                # régime tendance : on est long tant que la moyenne rapide est au-dessus de la lente
                target = 1 if fast.iloc[i] > slow.iloc[i] else 0
                if target != in_pos:
                    in_pos = target
                    if target:
                        reason.iloc[i] = f"Régime tendance (ADX {a.iloc[i]:.0f}) : EMA rapide > lente"
                    else:
                        reason.iloc[i] = f"Régime tendance (ADX {a.iloc[i]:.0f}) : EMA rapide < lente"
            else:
                # régime range : mean-reversion RSI
                if in_pos == 0 and pd.notna(r.iloc[i]) and r.iloc[i] < float(p["rsi_oversold"]):
                    in_pos = 1
                    reason.iloc[i] = (
                        f"Régime range (ADX {a.iloc[i] if pd.notna(a.iloc[i]) else 0:.0f}) : RSI {r.iloc[i]:.0f} survente"
                    )
                elif in_pos == 1 and pd.notna(r.iloc[i]) and r.iloc[i] > float(p["rsi_exit"]):
                    in_pos = 0
                    reason.iloc[i] = f"Régime range : RSI {r.iloc[i]:.0f} > sortie"
            pos[i] = in_pos
        sig["position"] = pos
        sig["reason"] = reason
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig
