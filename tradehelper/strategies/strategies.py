"""Stratégies prêtes à l'emploi, adaptées au swing / day trading."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import atr, donchian, ema, macd, rsi, sma
from .base import Strategy


class MovingAverageCross(Strategy):
    """Croisement de moyennes mobiles (swing).

    Long quand la moyenne rapide croise au-dessus de la lente,
    flat au croisement inverse. Filtre de tendance optionnel (EMA longue).
    """

    name = "ma_cross"
    label = "Croisement de moyennes mobiles (swing)"
    description = "EMA rapide vs EMA lente, filtre de tendance optionnel, stop ATR."
    default_params = {"fast": 12, "slow": 26, "trend_filter": True, "trend_ema": 200,
                      "atr_stop": 3.0}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = self._empty_signals(df)
        fast = ema(df["Close"], int(p["fast"]))
        slow = ema(df["Close"], int(p["slow"]))
        trend = ema(df["Close"], int(p["trend_ema"])) if p["trend_filter"] else None

        cross_up = (fast.shift(1) <= slow.shift(1)) & (fast > slow)
        cross_dn = (fast.shift(1) >= slow.shift(1)) & (fast < slow)

        pos = np.zeros(len(df))
        reason = pd.Series("", index=df.index)
        in_pos = 0
        for i in range(1, len(df)):
            if in_pos == 0 and cross_up.iloc[i]:
                if trend is None or df["Close"].iloc[i] > trend.iloc[i]:
                    in_pos = 1
                    reason.iloc[i] = f"Croisement haussier EMA{p['fast']}/EMA{p['slow']}"
                else:
                    reason.iloc[i] = "Croisement ignoré (sous la tendance longue)"
            elif in_pos == 1 and cross_dn.iloc[i]:
                in_pos = 0
                reason.iloc[i] = f"Croisement baissier EMA{p['fast']}/EMA{p['slow']}"
            pos[i] = in_pos
        sig["position"] = pos
        sig["reason"] = reason
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig


class RSIMeanReversion(Strategy):
    """Mean-reversion sur RSI (day/swing).

    Achat quand le RSI passe sous le seuil de survente, sortie quand il
    repasse au-dessus du seuil de sortie.
    """

    name = "rsi_reversion"
    label = "Mean-reversion RSI (day/swing)"
    description = "Long quand RSI < survente, sortie au-dessus du seuil de sortie. Stop ATR."
    default_params = {"period": 14, "oversold": 30.0, "exit_level": 50.0, "atr_stop": 2.5}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = self._empty_signals(df)
        r = rsi(df["Close"], int(p["period"]))
        pos = np.zeros(len(df))
        reason = pd.Series("", index=df.index)
        in_pos = 0
        for i in range(1, len(df)):
            if in_pos == 0 and r.iloc[i] < float(p["oversold"]):
                in_pos = 1
                reason.iloc[i] = f"RSI {r.iloc[i]:.1f} < {p['oversold']} (survente)"
            elif in_pos == 1 and r.iloc[i] > float(p["exit_level"]):
                in_pos = 0
                reason.iloc[i] = f"RSI {r.iloc[i]:.1f} > {p['exit_level']} (sortie)"
            pos[i] = in_pos
        sig["position"] = pos
        sig["reason"] = reason
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig


class DonchianBreakout(Strategy):
    """Breakout sur canal de Donchian avec confirmation de volume (swing).

    Long quand la clôture dépasse le plus haut des N dernières séances
    avec un volume supérieur à la moyenne ; sortie sur le plus bas.
    """

    name = "donchian_breakout"
    label = "Breakout canal de Donchian + volume (swing)"
    description = "Clôture > plus haut N séances avec volume confirmé, stop ATR."
    default_params = {"window": 20, "vol_mult": 1.2, "atr_stop": 2.5}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = self._empty_signals(df)
        n = int(p["window"])
        don = donchian(df, n)
        vol_ma = sma(df["Volume"].fillna(0.0), n)
        vol_ok = df["Volume"].fillna(0.0) > float(p["vol_mult"]) * vol_ma
        above = df["Close"] > don["upper"].shift(1)
        below = df["Close"] < don["lower"].shift(1)
        pos = np.zeros(len(df))
        reason = pd.Series("", index=df.index)
        in_pos = 0
        for i in range(1, len(df)):
            if in_pos == 0 and above.iloc[i] and vol_ok.iloc[i]:
                in_pos = 1
                reason.iloc[i] = f"Clôture > plus haut {n}j avec volume x{p['vol_mult']}"
            elif in_pos == 1 and below.iloc[i]:
                in_pos = 0
                reason.iloc[i] = f"Clôture < plus bas {n}j"
            pos[i] = in_pos
        sig["position"] = pos
        sig["reason"] = reason
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig


class MACDMomentum(Strategy):
    """Momentum MACD (day/swing).

    Long quand l'histogramme MACD repasse au-dessus de zéro,
    flat quand il repasse dessous.
    """

    name = "macd_momentum"
    label = "Momentum MACD (day/swing)"
    description = "Histogramme MACD > 0 -> long, < 0 -> flat. Stop ATR."
    default_params = {"fast": 12, "slow": 26, "signal": 9, "atr_stop": 2.5}

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = self._empty_signals(df)
        m = macd(df["Close"], int(p["fast"]), int(p["slow"]), int(p["signal"]))
        hist = m["hist"]
        cross_up = (hist.shift(1) <= 0) & (hist > 0)
        cross_dn = (hist.shift(1) >= 0) & (hist < 0)
        pos = np.zeros(len(df))
        reason = pd.Series("", index=df.index)
        in_pos = 0
        for i in range(1, len(df)):
            if in_pos == 0 and cross_up.iloc[i]:
                in_pos = 1
                reason.iloc[i] = "Histogramme MACD repasse > 0"
            elif in_pos == 1 and cross_dn.iloc[i]:
                in_pos = 0
                reason.iloc[i] = "Histogramme MACD repasse < 0"
            pos[i] = in_pos
        sig["position"] = pos
        sig["reason"] = reason
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig
