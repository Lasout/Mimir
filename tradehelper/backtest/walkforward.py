"""Walk-forward : validation hors échantillon (out-of-sample).

Découpe la série en blocs ; pour chaque bloc de test, entraîne sur la fenêtre
précédente (rolling ou ancrée), éventuellement en optimisant les paramètres sur
l'échantillon d'entraînement (grid search), puis évalue sur le bloc de test.
L'equity OOS est chaînée (capital composé d'un bloc à l'autre).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..strategies import get_strategy
from .engine import run_backtest
from .metrics import compute_metrics
from .optimize import grid_search


def walk_forward(
    df: pd.DataFrame,
    strategy_key: str,
    params: dict | None = None,
    param_grid: dict[str, list] | None = None,
    n_splits: int = 5,
    train_multiple: int = 2,
    anchored: bool = False,
    initial_capital: float = 10_000.0,
    metric: str = "sharpe",
    min_trades: int = 1,
    **bt_kwargs: Any,
) -> dict:
    """Retourne : oos_equity, oos_metrics, splits (détail par bloc), in_sample_metrics."""
    df = df.dropna(subset=["Close"])
    n = len(df)
    test_len = max(20, n // (n_splits + 1))
    splits: list[dict] = []
    oos_equity_parts: list[pd.Series] = []
    oos_trades: list[pd.DataFrame] = []
    capital = float(initial_capital)

    i = test_len
    while i + test_len <= n and len(splits) < n_splits:
        test_df = df.iloc[i : i + test_len]
        train_df = df.iloc[:i] if anchored else df.iloc[max(0, i - train_multiple * test_len) : i]
        if len(train_df) < 30:
            i += test_len
            continue
        # choix des paramètres sur le train
        if param_grid:
            best = grid_search(
                train_df, strategy_key, param_grid, metric=metric, min_trades=min_trades, top=1, **bt_kwargs
            )
            if best.empty:
                chosen = dict(params or {})
            else:
                row = best.iloc[0]
                chosen = {k: row[k] for k in param_grid}
            train_res = run_backtest(train_df, get_strategy(strategy_key, **chosen).generate(train_df), **bt_kwargs)
            train_metric = train_res.metrics.get(metric, 0.0)
        else:
            chosen = dict(params or {})
            train_res = run_backtest(train_df, get_strategy(strategy_key, **chosen).generate(train_df), **bt_kwargs)
            train_metric = train_res.metrics.get(metric, 0.0)
        # évaluation sur le bloc de test
        test_res = run_backtest(
            test_df, get_strategy(strategy_key, **chosen).generate(test_df), initial_capital=capital, **bt_kwargs
        )
        oos_equity_parts.append(test_res.equity)
        if not test_res.trades.empty:
            oos_trades.append(test_res.trades)
        splits.append(
            {
                "test_start": test_df.index[0],
                "test_end": test_df.index[-1],
                "params": chosen,
                f"train_{metric}": train_metric,
                f"test_{metric}": test_res.metrics.get(metric, 0.0),
                "test_return": test_res.metrics["total_return"],
                "test_trades": test_res.metrics["n_trades"],
            }
        )
        capital = float(test_res.equity.iloc[-1])
        i += test_len

    oos_equity = pd.concat(oos_equity_parts) if oos_equity_parts else pd.Series(dtype=float)
    oos_trades_df = (
        pd.concat(oos_trades)
        if oos_trades
        else pd.DataFrame(
            columns=["entry_time", "entry_price", "exit_time", "exit_price", "side", "pnl", "ret", "bars", "reason"]
        )
    )
    ppy = bt_kwargs.get("periods_per_year", 252)
    pos = (
        pd.concat([pd.Series(1, index=p.index) for p in oos_equity_parts])
        if oos_equity_parts
        else pd.Series(dtype=float)
    )
    oos_metrics = compute_metrics(oos_equity, oos_trades_df, pos, periods_per_year=ppy) if len(oos_equity) > 1 else {}
    # in-sample de référence (mêmes paramètres, toute la série)
    final_params = splits[-1]["params"] if splits else dict(params or {})
    in_sample = run_backtest(
        df, get_strategy(strategy_key, **final_params).generate(df), initial_capital=initial_capital, **bt_kwargs
    )
    return {
        "oos_equity": oos_equity,
        "oos_metrics": oos_metrics,
        "splits": splits,
        "in_sample_metrics": in_sample.metrics,
        "params": final_params,
    }
