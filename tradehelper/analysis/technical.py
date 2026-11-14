"""Score d'analyse technique à partir des indicateurs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators import adx, atr, ema, macd, obv, rsi, sma


def _clip(x: float) -> float:
    return float(np.clip(x, -1.0, 1.0))


def technical_analysis(df: pd.DataFrame) -> dict:
    """Retourne un dict : sous-scores, score global [-1, 1], raisons (français)."""
    df = df.dropna(subset=["Close"]).copy()
    close = df["Close"]
    n = len(df)
    reasons: list[str] = []
    scores: dict[str, float] = {}

    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    last = float(close.iloc[-1])

    # --- Tendance (poids 0.45) ---
    trend_parts = []
    if not np.isnan(ema50.iloc[-1]):
        trend_parts.append(_clip((last / ema50.iloc[-1] - 1.0) * 10.0))
        if n > 60:
            slope = (ema50.iloc[-1] / ema50.iloc[-10] - 1.0) if not np.isnan(ema50.iloc[-10]) else 0.0
            trend_parts.append(_clip(slope * 20.0))
            if not np.isnan(ema200.iloc[-1]):
                trend_parts.append(0.6 if last > ema200.iloc[-1] else -0.6)
        trend = float(np.mean(trend_parts))
    else:
        trend = 0.0
    scores["trend"] = trend
    if trend > 0.3:
        reasons.append(f"Tendance haussière : cours {last:.2f} au-dessus de la moyenne mobile (EMA50 {ema50.iloc[-1]:.2f}).")
    elif trend < -0.3:
        reasons.append(f"Tendance baissière : cours {last:.2f} sous la moyenne mobile (EMA50 {ema50.iloc[-1]:.2f}).")
    else:
        reasons.append("Tendance latérale (proche des moyennes mobiles).")

    # --- Momentum (poids 0.35) ---
    r = rsi(close, 14)
    rsi_last = float(r.iloc[-1]) if not np.isnan(r.iloc[-1]) else 50.0
    m = macd(close)
    hist_last = float(m["hist"].iloc[-1])
    hist_slope = float(m["hist"].iloc[-1] - m["hist"].iloc[-3]) if n > 3 else 0.0
    rsi_score = _clip((50.0 - rsi_last) / 25.0)          # RSI < 50 -> positif
    macd_score = _clip(hist_last / (abs(float(close.iloc[-1])) * 0.01)) + _clip(hist_slope * 50.0)
    macd_score = _clip(macd_score / 2.0)
    momentum = float(np.clip(0.5 * rsi_score + 0.5 * macd_score, -1, 1))
    scores["momentum"] = momentum
    if rsi_last < 30:
        reasons.append(f"RSI à {rsi_last:.1f} : zone de survente (potentiel rebond).")
    elif rsi_last > 70:
        reasons.append(f"RSI à {rsi_last:.1f} : zone de surachat (risque de repli).")
    else:
        reasons.append(f"RSI à {rsi_last:.1f} : momentum neutre.")
    if hist_last > 0 and hist_slope > 0:
        reasons.append("MACD au-dessus de sa ligne de signal et en hausse : momentum acheteur.")
    elif hist_last < 0 and hist_slope < 0:
        reasons.append("MACD sous sa ligne de signal et en baisse : momentum vendeur.")

    # --- Force de la tendance ADX (poids 0.10) ---
    a = adx(df, 14)
    adx_last = float(a.iloc[-1]) if not np.isnan(a.iloc[-1]) else 20.0
    adx_score = _clip((adx_last - 25.0) / 25.0) * (1.0 if trend > 0 else -1.0)
    scores["adx"] = adx_score
    if adx_last > 25:
        reasons.append(f"ADX à {adx_last:.1f} > 25 : la tendance en cours est forte.")

    # --- Volatilité / prix relatif dans les bandes (poids 0.10) ---
    window = 20
    mid = sma(close, window)
    std = close.rolling(window=window, min_periods=window).std(ddof=0)
    upper, lower = mid + 2 * std, mid - 2 * std
    denom = (upper.iloc[-1] - lower.iloc[-1])
    pct_b = float((close.iloc[-1] - lower.iloc[-1]) / denom) if denom and not np.isnan(denom) and denom > 0 else 0.5
    vol_score = _clip(2.0 * pct_b - 1.0)  # proche bande basse -> acheteur
    atr14 = atr(df, 14)
    atr_pct = float(atr14.iloc[-1] / last) if not np.isnan(atr14.iloc[-1]) else 0.0
    scores["volatility"] = vol_score
    if pct_b > 0.9:
        reasons.append("Cours proche de la bande de Bollinger haute : extension haussière.")
    elif pct_b < 0.1:
        reasons.append("Cours proche de la bande de Bollinger basse : extension baissière.")
    reasons.append(f"Volatilité (ATR14) : {atr_pct * 100:.2f} % du cours.")

    # --- Volume (confirmation) ---
    vol = df["Volume"].fillna(0.0)
    if vol.iloc[-20:].sum() > 0 and n > 20:
        vol_ratio = float(vol.iloc[-5:].mean() / vol.iloc[-20:].mean()) if vol.iloc[-20:].mean() > 0 else 1.0
        obv_slope = float(obv(df).iloc[-1] - obv(df).iloc[-10]) if n > 10 else 0.0
        vol_score_2 = _clip((vol_ratio - 1.0) * 2.0) + _clip(np.sign(obv_slope) * 0.3)
        scores["volume"] = _clip(vol_score_2 / 2.0)
        if vol_ratio > 1.3:
            reasons.append(f"Volume récent supérieur à la moyenne (x{vol_ratio:.2f}) : mouvement confirmé.")
    else:
        scores["volume"] = 0.0

    weights = {"trend": 0.40, "momentum": 0.35, "adx": 0.10, "volatility": 0.075, "volume": 0.075}
    score = float(sum(weights[k] * scores.get(k, 0.0) for k in weights))
    score = float(np.clip(score, -1.0, 1.0))

    return {
        "score": score,
        "sub_scores": scores,
        "reasons": reasons,
        "last_close": last,
        "rsi": rsi_last,
        "adx": adx_last,
        "atr_pct": atr_pct,
        "pct_b": pct_b,
        "ema50": float(ema50.iloc[-1]) if not np.isnan(ema50.iloc[-1]) else None,
        "ema200": float(ema200.iloc[-1]) if not np.isnan(ema200.iloc[-1]) else None,
    }
