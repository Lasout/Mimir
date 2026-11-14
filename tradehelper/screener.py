"""Screener multi-stratégies.

Pour chaque actif : on exécute toutes les stratégies en backtest, on choisit
la meilleure (Sharpe, avec repli sur le rendement si peu de trades), puis on
recommande une prise de position avec horizon (durée moyenne des trades),
prix d'entrée, stop-loss et objectif de cours.
"""

from __future__ import annotations

import pandas as pd

from .advice import advise
from .backtest import periods_per_year, run_backtest
from .data.sources import load_data
from .indicators import add_all
from .strategies import get_strategy, list_strategies

BAR_HOURS = {"1d": 24.0, "1wk": 168.0, "1mo": 720.0, "12h": 12.0, "8h": 8.0,
             "4h": 4.0, "2h": 2.0, "1h": 1.0, "30m": 0.5, "15m": 0.25, "5m": 5.0 / 60.0}


def bar_hours(interval: str) -> float:
    return BAR_HOURS.get(interval, 24.0)


def format_horizon(avg_bars: float, interval: str) -> str:
    """Convertit une durée moyenne de trade (en barres) en horizon lisible."""
    hours = max(avg_bars, 1.0) * bar_hours(interval)
    if hours < 48:
        return f"≈ {max(1, round(hours))} h"
    days = hours / 24.0
    if days < 60:
        return f"≈ {max(1, round(days))} j"
    return f"≈ {max(1, round(days / 30.0))} mois"


def recommend(df: pd.DataFrame, signals: pd.DataFrame, metrics: dict,
              interval: str, stop_mult: float = 2.0, reward_risk: float = 2.5) -> dict:
    """Recommandation à partir du dernier signal de la stratégie choisie.

    - position : ACHAT si la stratégie est en position longue à la dernière barre
    - entrée ≈ dernier cours (exécution au prochain open)
    - stop = entrée - stop_mult * ATR14 ; objectif = entrée + reward_risk * risque
    - horizon = durée moyenne des trades du backtest
    """
    pos = int(signals["position"].iloc[-1])
    entry = float(df["Close"].iloc[-1])
    atr = float(df["ATR14"].iloc[-1]) if "ATR14" in df.columns and pd.notna(df["ATR14"].iloc[-1]) else entry * 0.02
    if not atr > 0:
        atr = entry * 0.02
    stop = entry - stop_mult * atr
    risk = entry - stop
    target = entry + reward_risk * risk
    horizon = format_horizon(metrics.get("avg_bars") or 5.0, interval)
    if pos > 0:
        position = "ACHAT"
    elif pos < 0:
        position = "SHORT"
    else:
        position = "ATTENTE"
    return {"position": position, "entry": entry, "stop": stop, "target": target,
            "horizon": horizon, "atr": atr}


def _pick_best(results: list[tuple]) -> tuple:
    """results : (key, label, BacktestResult, signals)."""
    valid = [r for r in results if r[2].metrics["n_trades"] >= 2]
    pool = valid or results
    return max(pool, key=lambda r: (r[2].metrics["sharpe"], r[2].metrics["total_return"]))


def screen_assets(source: str, symbols: list[str], period: str = "1y", interval: str = "1d",
                  capital: float = 10_000.0, commission: float = 0.001, slippage: float = 0.0005,
                  strategies: list[str] | None = None, on_progress=None) -> list[dict]:
    """Analyse chaque symbole avec toutes les stratégies. Retourne une liste de dicts."""
    keys = strategies or [s["key"] for s in list_strategies()]
    ppy = periods_per_year(interval, source)
    rows: list[dict] = []
    for i, sym in enumerate(symbols):
        if on_progress is not None:
            on_progress(i, len(symbols), sym)
        try:
            df = add_all(load_data(source, sym, period=period, interval=interval))
            adv = advise(df, sym, source=source, interval=interval)
            results = []
            for key in keys:
                strat = get_strategy(key)
                sig = strat.generate(df)
                res = run_backtest(df, sig, initial_capital=capital, commission=commission,
                                   slippage=slippage, periods_per_year=ppy)
                results.append((key, strat.label, res, sig))
            bkey, blabel, bres, bsig = _pick_best(results)
            rec = recommend(df, bsig, bres.metrics, interval)
            m = bres.metrics
            rows.append({
                "symbol": sym,
                "last_close": round(adv["last_close"], 4),
                "score": adv["score"],
                "label": adv["label"],
                "rsi": adv["rsi"],
                "adx": adv["adx"],
                "atr_pct": adv["atr_pct"],
                "risk": adv["risk"],
                "best_strategy": bkey,
                "strategy_label": blabel,
                "sharpe": round(m["sharpe"], 2),
                "total_return": round(m["total_return"], 4),
                "n_trades": m["n_trades"],
                "win_rate": round(m["win_rate"], 3),
                "position": rec["position"],
                "entry": round(rec["entry"], 4),
                "stop": round(rec["stop"], 4),
                "target": round(rec["target"], 4),
                "horizon": rec["horizon"],
                "error": None,
            })
        except Exception as exc:
            rows.append({"symbol": sym, "error": str(exc), "score": "", "label": f"Erreur: {exc}"[:40],
                         "position": "", "entry": "", "stop": "", "target": "", "horizon": "",
                         "best_strategy": "", "sharpe": "", "total_return": "", "n_trades": "",
                         "win_rate": "", "last_close": "", "rsi": "", "adx": "", "atr_pct": "",
                         "risk": "", "strategy_label": ""})
    return rows
