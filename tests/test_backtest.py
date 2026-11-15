import unittest

import numpy as np
import pandas as pd

from tests.helpers import make_ohlcv
from tradehelper.backtest import run_backtest


def long_signals(df, start=0):
    sig = pd.DataFrame({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""}, index=df.index)
    sig.loc[sig.index[start:], "position"] = 1
    sig.loc[sig.index[start], "reason"] = "Entrée test"
    return sig


class TestBacktest(unittest.TestCase):
    def test_long_uptrend_profit(self):
        df = make_ohlcv(n=120, drift=0.004, vol=0.005, seed=1)  # forte hausse
        res = run_backtest(df, long_signals(df), initial_capital=10_000, commission=0.0, slippage=0.0)
        self.assertGreater(res.metrics["total_return"], 0.0)
        self.assertGreaterEqual(res.metrics["n_trades"], 1)

    def test_no_lookahead_entry_next_bar(self):
        df = make_ohlcv(n=60)
        sig = long_signals(df, start=10)
        res = run_backtest(df, sig, commission=0.0, slippage=0.0)
        self.assertEqual(len(res.trades), 1)
        # le signal de la barre 10 est exécuté à l'open de la barre 11
        self.assertEqual(res.trades.iloc[0]["entry_time"], df.index[11])

    def test_commission_reduces_return(self):
        df = make_ohlcv(n=120, drift=0.004, vol=0.005, seed=1)
        sig = long_signals(df)
        r_free = run_backtest(df, sig, commission=0.0, slippage=0.0)
        r_comm = run_backtest(df, sig, commission=0.01, slippage=0.0)
        self.assertGreater(r_free.metrics["total_return"], r_comm.metrics["total_return"])

    def test_flat_market_no_trades(self):
        df = make_ohlcv(n=60, drift=0.0, vol=0.0, seed=3)
        sig = pd.DataFrame({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""}, index=df.index)
        res = run_backtest(df, sig)
        self.assertEqual(res.metrics["n_trades"], 0)
        self.assertAlmostEqual(res.metrics["total_return"], 0.0)

    def test_metrics_keys(self):
        df = make_ohlcv(n=100)
        res = run_backtest(df, long_signals(df, start=5))
        for key in (
            "total_return",
            "cagr",
            "sharpe",
            "sortino",
            "max_drawdown",
            "win_rate",
            "profit_factor",
            "n_trades",
            "exposure",
        ):
            self.assertIn(key, res.metrics)
        self.assertLessEqual(res.metrics["max_drawdown"], 0.0)
        self.assertGreaterEqual(res.equity.iloc[0], 0.0)

    def test_stop_loss_triggers(self):
        df = make_ohlcv(n=80, drift=0.003, vol=0.01, seed=5)
        sig = long_signals(df, start=5)
        # stop très proche du cours -> sortie quasi immédiate
        sig["stop"] = df["Close"] * 0.995
        sig.loc[sig["position"] == 0, "stop"] = np.nan
        res = run_backtest(df, sig, commission=0.0, slippage=0.0)
        self.assertGreaterEqual(res.metrics["n_trades"], 1)
        self.assertTrue((res.trades["reason"] == "Stop-loss").any())

    def test_short_disabled_by_default(self):
        df = make_ohlcv(n=60)
        sig = pd.DataFrame({"position": -1, "stop": np.nan, "target": np.nan, "reason": ""}, index=df.index)
        res = run_backtest(df, sig, allow_short=False)
        self.assertEqual(res.metrics["n_trades"], 0)


if __name__ == "__main__":
    unittest.main()
