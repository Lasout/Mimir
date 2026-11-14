"""Backtest de portefeuille multi-actifs.

Chaque actif est tradé avec la même stratégie (signaux long-only) ; à chaque barre,
le capital est réparti à parts égales entre les actifs en position (exécuté à l'open
de la barre suivante), avec frais par exécution et slippage. Fournit aussi la
matrice de corrélation des rendements quotidiens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..fees import FeeModel
from ..strategies import get_strategy
from .engine import BacktestResult
from .metrics import compute_metrics


@dataclass
class PortfolioResult:
    equity: pd.Series
    positions: pd.DataFrame          # parts par actif (0..1)
    trades: pd.DataFrame
    metrics: dict
    correlation: pd.DataFrame
    params: dict = field(default_factory=dict)


def run_portfolio(dfs: dict[str, pd.DataFrame], strategy_key: str = "ma_cross",
                  params: dict | None = None, initial_capital: float = 10_000.0,
                  fee: FeeModel | None = None, commission: float = 0.001,
                  slippage: float = 0.0005, max_positions: int | None = None,
                  max_corr: float | None = None, periods_per_year: int = 252) -> PortfolioResult:
    from ..fees import FeeModel as _FM
    fee = fee if fee is not None else _FM(name="commission %", pct=commission)
    symbols = list(dfs.keys())
    if not symbols:
        raise ValueError("Aucun actif fourni.")
    # signaux par actif
    sigs = {}
    for sym, df in dfs.items():
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        sigs[sym] = get_strategy(strategy_key, **(params or {})).generate(df)["position"]
    # index commun (intersection) et prix
    idx = None
    for sym, df in dfs.items():
        dfi = df.dropna(subset=["Open", "High", "Low", "Close"])
        idx = dfi.index if idx is None else idx.intersection(dfi.index)
    idx = idx.sort_values()
    n = len(idx)
    if n < 2:
        raise ValueError("Pas assez de barres communes.")
    open_px = {s: dfs[s].reindex(idx)["Open"].to_numpy() for s in symbols}
    close_px = {s: dfs[s].reindex(idx)["Close"].to_numpy() for s in symbols}
    sig_at = {s: sigs[s].reindex(idx).fillna(0).to_numpy() for s in symbols}
    # corrélation des rendements (calculée une fois ; simplification documentée dans le docstring)
    corr = pd.DataFrame({s: pd.Series(close_px[s], index=idx).pct_change() for s in symbols}).corr()

    cash = float(initial_capital)
    shares = {s: 0.0 for s in symbols}
    pending: dict[str, float] = {s: 0.0 for s in symbols}  # parts cibles, exécutées à l'open suivant
    trades: list[dict] = []
    equity_curve = np.empty(n)
    weights_hist = np.zeros((n, len(symbols)))
    total_fees = 0.0

    for i in range(n):
        # exécution des poids cibles à l'open de la barre i
        if i > 0:
            equity_now = cash + sum(shares[s] * open_px[s][i] for s in symbols)
            for s in symbols:
                target_val = pending[s] * equity_now
                cur_val = shares[s] * open_px[s][i]
                delta_val = target_val - cur_val
                if abs(delta_val) < 1e-9:
                    continue
                px = open_px[s][i] * (1 + slippage if delta_val > 0 else 1 - slippage)
                delta_shares = delta_val / px
                cost = fee.cost(abs(delta_val))
                cash -= delta_val + cost
                total_fees += cost
                if shares[s] == 0 and delta_shares != 0:
                    trades.append({"symbol": s, "entry_time": idx[i], "entry_price": px,
                                   "exit_time": np.nan, "exit_price": np.nan, "side": "long",
                                   "pnl": 0.0, "ret": 0.0, "bars": 0, "reason": "Entrée portefeuille"})
                    entry_map = trades[-1]
                    entry_map["_idx"] = len(trades) - 1
                elif shares[s] != 0 and (shares[s] + delta_shares) == 0:
                    # clôture complète
                    tr = next((t for t in reversed(trades) if t["symbol"] == s and pd.isna(t["exit_time"])), None)
                    if tr is not None:
                        tr["exit_time"] = idx[i]
                        tr["exit_price"] = px
                        tr["pnl"] = (px - tr["entry_price"]) * abs(shares[s]) - cost
                        tr["ret"] = px / tr["entry_price"] - 1.0
                        tr["bars"] = i - idx.get_loc(tr["entry_time"])
                        tr["reason"] = "Sortie portefeuille"
                shares[s] += delta_shares
        # nouveaux poids cibles à partir des signaux de la barre i (exécutés à i+1)
        active = [s for s in symbols if sig_at[s][i] > 0]
        if max_positions and len(active) > max_positions:
            active = active[:max_positions]
        if max_corr is not None and len(active) > 1:
            # limite de corrélation : on écarte les actifs trop corrélés aux déjà retenus
            selected: list[str] = []
            for s in active:
                if not selected:
                    selected.append(s)
                elif corr.loc[s, selected].mean() <= max_corr:
                    selected.append(s)
            active = selected
        w = 1.0 / len(active) if active else 0.0
        for s in symbols:
            pending[s] = w if s in active else 0.0
        # equity à la clôture
        eq = cash + sum(shares[s] * close_px[s][i] for s in symbols)
        equity_curve[i] = eq
        weights_hist[i] = [abs(shares[s] * close_px[s][i]) / eq if eq > 0 else 0.0 for s in symbols]

    # clôture en fin de période
    for s in symbols:
        if shares[s] != 0:
            px = close_px[s][-1] * (1 - slippage)
            cost = fee.cost(abs(shares[s]) * px)
            cash += shares[s] * px - cost
            total_fees += cost
            tr = next((t for t in reversed(trades) if t["symbol"] == s and pd.isna(t["exit_time"])), None)
            if tr is not None:
                tr["exit_time"] = idx[-1]
                tr["exit_price"] = px
                tr["pnl"] = (px - tr["entry_price"]) * abs(shares[s]) - cost
                tr["ret"] = px / tr["entry_price"] - 1.0
                tr["bars"] = n - 1 - idx.get_loc(tr["entry_time"])
                tr["reason"] = "Fin de la période"
            shares[s] = 0.0
    equity_curve[-1] = cash

    equity = pd.Series(equity_curve, index=idx, name="equity")
    positions = pd.DataFrame(weights_hist, index=idx, columns=symbols)
    trades_df = pd.DataFrame([{k: v for k, v in t.items() if k != "_idx"} for t in trades])
    metrics = compute_metrics(equity, trades_df, (positions.sum(axis=1) > 0).astype(int),
                              periods_per_year=periods_per_year)
    metrics["total_fees"] = total_fees
    return PortfolioResult(equity=equity, positions=positions, trades=trades_df,
                           metrics=metrics, correlation=corr,
                           params={"strategy": strategy_key, "params": params or {},
                                   "fee": fee.describe(), "initial_capital": initial_capital,
                                   "max_positions": max_positions, "max_corr": max_corr})
