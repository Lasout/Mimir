import unittest

import pandas as pd

from tests.helpers import make_ohlcv, make_trend_reversal
from tradehelper.backtest import grid_search, monte_carlo_trades, run_portfolio, walk_forward
from tradehelper.fees import FeeModel


class TestGridSearch(unittest.TestCase):
    def test_grid_returns_sorted_df(self):
        df = make_trend_reversal(n=300)
        out = grid_search(
            df,
            "ma_cross",
            {"fast": [5, 10, 20], "slow": [30, 50], "trend_filter": [False]},
            fee=FeeModel("zero"),
            slippage=0.0,
            min_trades=1,
        )
        self.assertFalse(out.empty)
        self.assertEqual(len(out), 6)
        for col in ("fast", "slow", "sharpe", "total_return", "n_trades", "score", "neighbor_sharpe"):
            self.assertIn(col, out.columns)
        self.assertTrue(out["score"].is_monotonic_decreasing)

    def test_empty_grid(self):
        df = make_ohlcv(n=50)
        out = grid_search(df, "ma_cross", {}, fee=FeeModel("zero"))
        self.assertIn("score", out.columns) or out.empty


class TestWalkForward(unittest.TestCase):
    def test_fixed_params(self):
        df = make_trend_reversal(n=400)
        res = walk_forward(
            df,
            "ma_cross",
            params={"fast": 10, "slow": 30, "trend_filter": False},
            n_splits=4,
            fee=FeeModel("zero"),
            slippage=0.0,
        )
        self.assertGreater(len(res["splits"]), 0)
        self.assertGreater(len(res["oos_equity"]), 0)
        for k in ("total_return", "sharpe", "max_drawdown", "n_trades"):
            self.assertIn(k, res["oos_metrics"])
        self.assertIn("in_sample_metrics", res)
        for s in res["splits"]:
            self.assertIn("params", s)
            self.assertIn("test_return", s)

    def test_with_param_grid(self):
        df = make_trend_reversal(n=400)
        res = walk_forward(
            df,
            "ma_cross",
            param_grid={"fast": [5, 10], "slow": [30, 50], "trend_filter": [False]},
            n_splits=3,
            fee=FeeModel("zero"),
            slippage=0.0,
            min_trades=1,
        )
        self.assertGreater(len(res["splits"]), 0)
        for s in res["splits"]:
            self.assertIn(s["params"]["fast"], (5, 10))


class TestMonteCarlo(unittest.TestCase):
    def test_stats(self):
        trades = pd.DataFrame({"ret": [0.05, -0.02, 0.10, -0.05, 0.03, 0.08, -0.01, 0.04]})
        mc = monte_carlo_trades(trades, initial_capital=10_000, n_sims=500, seed=1)
        self.assertEqual(mc["n_sims"], 500)
        self.assertEqual(mc["n_trades"], 8)
        self.assertGreaterEqual(mc["prob_profit"], 0.0)
        self.assertLessEqual(mc["prob_profit"], 1.0)
        self.assertLessEqual(mc["p5_final"], mc["p50_final"])
        self.assertLessEqual(mc["p50_final"], mc["p95_final"])
        self.assertGreaterEqual(mc["p95_maxdd"], mc["p5_maxdd"])

    def test_deterministic_with_seed(self):
        trades = pd.DataFrame({"ret": [0.05, -0.02, 0.10]})
        a = monte_carlo_trades(trades, n_sims=100, seed=7)
        b = monte_carlo_trades(trades, n_sims=100, seed=7)
        self.assertEqual(a["p50_final"], b["p50_final"])

    def test_empty_trades(self):
        mc = monte_carlo_trades(pd.DataFrame(columns=["ret"]))
        self.assertIn("error", mc)


class TestPortfolio(unittest.TestCase):
    def test_run_portfolio(self):
        dfs = {
            s: make_ohlcv(n=200, drift=0.002 if s != "DOWN" else -0.001, vol=0.01, seed=i)
            for i, s in enumerate(["AAA", "BBB", "DOWN"])
        }
        res = run_portfolio(
            dfs, "ma_cross", params={"fast": 10, "slow": 30, "trend_filter": False}, fee=FeeModel("zero"), slippage=0.0
        )
        self.assertEqual(len(res.equity), 200)
        self.assertEqual(res.positions.shape, (200, 3))
        self.assertEqual(res.correlation.shape, (3, 3))
        for k in ("total_return", "sharpe", "max_drawdown", "n_trades", "total_fees"):
            self.assertIn(k, res.metrics)
        self.assertFalse(res.trades.empty)
        self.assertIn("symbol", res.trades.columns)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            run_portfolio({})


if __name__ == "__main__":
    unittest.main()
