"""Métriques de performance d'un backtest."""

from __future__ import annotations

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    dd = (equity - peak) / peak
    return float(dd.min())


def compute_metrics(equity: pd.Series, trades: pd.DataFrame, positions: pd.Series,
                    periods_per_year: float = 252, rf: float = 0.0) -> dict:
    equity = equity.dropna()
    if len(equity) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0,
                "n_trades": 0, "win_rate": 0.0}
    rets = equity.pct_change().dropna()
    days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    years = max(days / 365.0, 1e-9)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1.0)
    cagr = float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0) if years > 0 else total_return
    ann_vol = float(rets.std(ddof=1) * np.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    excess = rets.mean() * periods_per_year - rf
    sharpe = float(excess / ann_vol) if ann_vol > 0 else 0.0
    downside = rets[rets < 0]
    downside_vol = float(downside.std(ddof=1) * np.sqrt(periods_per_year)) if len(downside) > 1 else 0.0
    sortino = float(excess / downside_vol) if downside_vol > 0 else 0.0
    mdd = max_drawdown(equity)
    calmar = float(cagr / abs(mdd)) if mdd < 0 else 0.0

    n = len(trades)
    if n > 0:
        wins = trades[trades["ret"] > 0]
        win_rate = float(len(wins) / n)
        gross_profit = float(wins["pnl"].sum())
        gross_loss = float(-trades.loc[trades["ret"] <= 0, "pnl"].sum())
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        expectancy = float(trades["pnl"].mean())
        avg_ret = float(trades["ret"].mean())
        best = float(trades["ret"].max())
        worst = float(trades["ret"].min())
        avg_bars = float(trades["bars"].mean()) if "bars" in trades else 0.0
    else:
        win_rate = profit_factor = expectancy = avg_ret = best = worst = avg_bars = 0.0
        profit_factor = 0.0

    exposure = float((positions != 0).mean()) if len(positions) else 0.0

    return {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": mdd,
        "calmar": calmar,
        "n_trades": int(n),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy": expectancy,
        "avg_trade_ret": avg_ret,
        "best_trade": best,
        "worst_trade": worst,
        "avg_bars": avg_bars,
        "exposure": exposure,
        "years": years,
    }


def periods_per_year(interval: str, source: str = "stock") -> float:
    """Nombre de barres par an selon l'intervalle (approx.)."""
    if source == "crypto":
        return {"1d": 365, "12h": 730, "8h": 1095, "4h": 2190, "2h": 4380,
                "1h": 8760, "30m": 17520, "15m": 35040, "5m": 105120}.get(interval, 365)
    if source == "forex":
        return 252
    return {"1d": 252, "1wk": 52, "1mo": 12, "1h": 252 * 6.5, "4h": 252 * 6.5 / 4,
            "30m": 252 * 6.5 * 2, "15m": 252 * 6.5 * 4, "5m": 252 * 6.5 * 12}.get(interval, 252)
