"""Stratégie ML : classifieur sur features techniques causales.

Features à la barre i (strictement causales : données ≤ i uniquement) :
ret_1, ret_5, RSI, MACD hist relatif, %B de Bollinger, ATR%, ratio de volume, ADX,
distance à l'EMA50. Label : la clôture de i+1 monte d'au moins `threshold`.

Le modèle (HistGradientBoosting) est réentraîné tous les `retrain_every` barres sur
une fenêtre glissante de `train_window` barres (pas de lookahead). Une validation
croisée temporelle (TimeSeriesSplit) est calculée une fois et exposée dans
`val_accuracy` (diagnostic, pas un gate).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ..indicators import adx, atr, bollinger_bands, ema, macd, rsi, sma
from .base import Strategy


def _features(df: pd.DataFrame) -> pd.DataFrame:
    close = df["Close"]
    vol = df["Volume"].fillna(0.0)
    m = macd(close)
    bb = bollinger_bands(close)
    feats = pd.DataFrame(
        {
            "ret_1": close.pct_change(1),
            "ret_5": close.pct_change(5),
            "rsi": rsi(close, 14) / 100.0,
            "macd_rel": m["hist"] / close,
            "pct_b": bb["pct_b"],
            "atr_pct": atr(df, 14) / close,
            "vol_ratio": vol / sma(vol, 20).replace(0, np.nan),
            "adx": adx(df, 14) / 100.0,
            "dist_ema50": close / ema(close, 50) - 1.0,
        }
    )
    return feats.fillna(0.0)


def _labels(df: pd.DataFrame, threshold: float) -> pd.Series:
    fwd = df["Close"].shift(-1) / df["Close"] - 1.0
    return (fwd > threshold).astype(float).where(fwd.notna())


class MLSignal(Strategy):
    name = "ml_signal"
    label = "Signal ML (HistGB, fenêtre glissante, CV temporelle)"
    description = "Classifieur sklearn sur features techniques causales ; réentraîné périodiquement."
    default_params = {
        "retrain_every": 20,
        "train_window": 500,
        "min_train": 60,
        "threshold": 0.0,
        "margin": 0.02,
        "n_splits": 3,
        "atr_stop": 2.5,
        "device": "auto",
    }  # auto | cpu | cuda (cuda -> XGBoost device="cuda")

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.val_accuracy: float | None = None

    def _make_model(self, device: str) -> Any:
        if device == "cuda":
            try:
                from xgboost import XGBClassifier

                return XGBClassifier(
                    tree_method="hist",
                    device="cuda",
                    n_estimators=200,
                    max_depth=6,
                    learning_rate=0.1,
                    random_state=0,
                    n_jobs=-1,
                )
            except ImportError:
                pass  # pas de xgboost -> repli sklearn CPU
        from sklearn.ensemble import HistGradientBoostingClassifier

        return HistGradientBoostingClassifier(max_iter=100, learning_rate=0.1, random_state=0)

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            from sklearn.model_selection import TimeSeriesSplit
        except ImportError as exc:
            raise RuntimeError("scikit-learn requis pour ml_signal (pip install scikit-learn)") from exc

        p = self.params
        device = p.get("device", "auto")
        if device == "auto":
            from ..compute import detect_backend

            device = detect_backend()
        sig = self._empty_signals(df)
        feats = _features(df)
        labels = _labels(df, float(p["threshold"]))
        X = feats.to_numpy()
        y = labels.to_numpy()
        n = len(df)
        min_train = int(p["min_train"])
        retrain = max(1, int(p["retrain_every"]))
        window = int(p["train_window"])
        margin = float(p["margin"])

        # CV temporelle (diagnostic) sur l'échantillon
        valid = ~np.isnan(y)
        if valid.sum() > 5 * int(p["n_splits"]):
            accs = []
            tscv = TimeSeriesSplit(n_splits=int(p["n_splits"]))
            for tr, te in tscv.split(X[valid]):
                clf = self._make_model(device)
                clf.fit(X[valid][tr], y[valid][tr])
                accs.append(float(clf.score(X[valid][te], y[valid][te])))
            self.val_accuracy = float(np.mean(accs)) if accs else None

        model = None
        pos = np.zeros(n)
        for i in range(min_train, n):
            # Réentraînement à la barre i : labels y[k] pour k < i uniquement.
            # y[k] = f(close[k+1]/close[k]) a besoin de close[k+1] <= close[i] : causal.
            # (Ne JAMAIS inclure y[i] : il dépend de close[i+1], donc du futur.)
            if model is None or (i - min_train) % retrain == 0:
                lo = max(0, i - window)
                mask = ~np.isnan(y[lo:i])
                if mask.sum() >= 30:
                    model = self._make_model(device)
                    model.fit(X[lo:i][mask], y[lo:i][mask])
            # Prédiction à la barre i : ne dépend que de X[i] (features <= i) — jamais de y[i].
            if model is not None:
                prob = model.predict_proba(X[i : i + 1])[0, 1]
                pos[i] = 1.0 if prob > 0.5 + margin else 0.0
            else:
                pos[i] = pos[i - 1] if i > 0 else 0.0
        sig["position"] = pos
        reasons = pd.Series("", index=df.index)
        changes = pd.Series(pos, index=df.index).diff().fillna(0)
        reasons[changes == 1] = f"ML: proba achat > {0.5 + margin:.2f}"
        reasons[changes == -1] = "ML: proba achat repasse sous le seuil"
        sig["reason"] = reasons
        if p["atr_stop"]:
            stop = self._atr_trailing_stop(df, float(p["atr_stop"]), +1)
            sig["stop"] = stop.where(pd.Series(pos, index=df.index) == 1)
        return sig
