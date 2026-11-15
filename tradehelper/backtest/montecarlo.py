"""Monte Carlo sur la séquence des trades (bootstrap des rendements)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def monte_carlo_trades(trades: pd.DataFrame, initial_capital: float = 10_000.0,
                       n_sims: int = 1000, seed: int = 42) -> dict:
    """Rééchantillonne les rendements des trades (avec remplacement) et rejicule
    la trajectoire d'equity. Retourne les percentiles de valeur finale et de DD."""
    if trades is None or len(trades) == 0:
        return {"n_sims": 0, "error": "aucun trade"}
    rets = trades["ret"].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    finals = np.empty(n_sims)
    maxdds = np.empty(n_sims)
    for s in range(n_sims):
        sample = rng.choice(rets, size=len(rets), replace=True)
        eq = initial_capital * np.cumprod(1.0 + sample)
        finals[s] = eq[-1]
        maxdds[s] = np.min((eq - np.maximum.accumulate(eq)) / np.maximum.accumulate(eq))
    return {
        "n_sims": n_sims,
        "n_trades": len(rets),
        "prob_profit": float((finals > initial_capital).mean()),
        "mean_final": float(finals.mean()),
        "p5_final": float(np.percentile(finals, 5)),
        "p50_final": float(np.percentile(finals, 50)),
        "p95_final": float(np.percentile(finals, 95)),
        "p5_maxdd": float(np.percentile(maxdds, 5)),
        "p95_maxdd": float(np.percentile(maxdds, 95)),
        "worst_final": float(np.min(finals)),
    }
