"""Grid search sur les paramètres d'une stratégie, avec garde-fou anti-overfitting."""

from __future__ import annotations

import itertools

import pandas as pd

from ..strategies import get_strategy
from .engine import run_backtest


def grid_search(df: pd.DataFrame, strategy_key: str, param_grid: dict[str, list],
                metric: str = "sharpe", min_trades: int = 3, top: int | None = None,
                **bt_kwargs) -> pd.DataFrame:
    """Teste toutes les combinaisons de `param_grid` et retourne un DataFrame trié.

    Colonnes : les paramètres + métriques (sharpe, total_return, max_drawdown,
    n_trades, win_rate, profit_factor) + `neighbor_<metric>` (moyenne du metric sur
    les configurations voisines dans la grille) : un bon score isolé au milieu de
    voisins faibles est suspect (overfitting) — `score` pénalise cet écart.
    """
    keys = list(param_grid.keys())
    combos = list(itertools.product(*[param_grid[k] for k in keys]))
    rows = []
    for combo in combos:
        params = dict(zip(keys, combo))
        try:
            sig = get_strategy(strategy_key, **params).generate(df)
            res = run_backtest(df, sig, **bt_kwargs)
            m = res.metrics
            row = dict(params)
            row.update({"sharpe": m["sharpe"], "sortino": m["sortino"],
                        "total_return": m["total_return"], "cagr": m["cagr"],
                        "max_drawdown": m["max_drawdown"], "n_trades": m["n_trades"],
                        "win_rate": m["win_rate"], "profit_factor": m["profit_factor"],
                        "total_fees": m.get("total_fees", 0.0)})
            rows.append(row)
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    valid = out[out["n_trades"] >= min_trades]
    pool = valid if not valid.empty else out
    # moyenne du metric chez les voisins (configs qui diffèrent d'un seul paramètre)
    neighbor_col = f"neighbor_{metric}"
    neigh = []
    for _, r in pool.iterrows():
        mask = pd.Series(True, index=pool.index)
        for k in keys:
            mask &= pool[k] == r[k]
        others = pool[(~mask) & (pool[keys].apply(lambda c: (c != r[keys]).sum() == 1, axis=1))]
        neigh.append(others[metric].mean() if len(others) else r[metric])
    pool = pool.copy()
    pool[neighbor_col] = neigh
    pool["score"] = pool[metric] - 0.5 * (pool[metric] - pool[neighbor_col]).clip(lower=0)
    pool = pool.sort_values("score", ascending=False)
    return pool.head(top) if top else pool
