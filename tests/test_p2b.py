import unittest

import numpy as np
import pandas as pd

from tests.helpers import make_ohlcv, make_trend_reversal
from tradehelper.backtest import run_backtest, run_portfolio
from tradehelper.data.universe import universe_for
from tradehelper.fees import FeeModel
from tradehelper.screener import top_pick
from tradehelper.strategies import get_strategy


class TestETFsInUniverse(unittest.TestCase):
    def test_etfs_present(self):
        stocks = universe_for("stock")
        for etf in ("SPY", "QQQ", "VWCE.DE", "IWDA.AS", "CW8.PA"):
            self.assertIn(etf, stocks)
        self.assertGreater(len(stocks), 50)


class TestAdaptiveRegime(unittest.TestCase):
    def test_signals_on_trend_then_range(self):
        df = make_trend_reversal(n=300)
        sig = get_strategy("adaptive_regime").generate(df)
        self.assertEqual(len(sig), len(df))
        self.assertIn(1, sig["position"].values)
        self.assertTrue(set(sig["position"].unique()) <= {-1, 0, 1})


class TestMLSignal(unittest.TestCase):
    def test_generate_no_lookahead_shape(self):
        df = make_trend_reversal(n=160)
        strat = get_strategy("ml_signal", min_train=40, retrain_every=20, train_window=120)
        sig = strat.generate(df)
        self.assertEqual(len(sig), len(df))
        self.assertTrue(set(sig["position"].unique()) <= {-1, 0, 1})
        self.assertIsNotNone(strat.val_accuracy)
        self.assertGreaterEqual(strat.val_accuracy, 0.0)
        self.assertLessEqual(strat.val_accuracy, 1.0)
        # les premières barres (avant min_train) sont à plat
        self.assertTrue((sig["position"].iloc[:40] == 0).all())


class TestKellySizing(unittest.TestCase):
    def test_kelly_runs(self):
        df = make_ohlcv(n=150, drift=0.004, vol=0.01, seed=4)
        sig = pd.DataFrame({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""}, index=df.index)
        # signaux oscillants pour générer plusieurs trades
        sig.loc[sig.index[10::15], "position"] = [1, 0] * 5
        sig["position"] = (
            sig["position"].cummax().clip(0, 1).where(sig["position"].diff().fillna(0) != 0, sig["position"])
        )
        res = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0, sizing="kelly", kelly_fraction=0.5)
        self.assertIn("total_return", res.metrics)
        self.assertEqual(res.params["sizing"], "kelly")


class TestPortfolioMaxCorr(unittest.TestCase):
    def test_correlated_assets_not_both_held(self):
        base = make_ohlcv(n=150, drift=0.002, vol=0.01, seed=11)
        dfs = {"AAA": base.copy(), "BBB": base.copy(), "CCC": make_ohlcv(n=150, drift=-0.001, vol=0.012, seed=99)}
        res = run_portfolio(
            dfs,
            "ma_cross",
            params={"fast": 5, "slow": 20, "trend_filter": False},
            fee=FeeModel("zero"),
            slippage=0.0,
            max_corr=0.9,
        )
        # AAA et BBB sont identiques (corr = 1) : jamais détenus ensemble
        both = ((res.positions["AAA"] > 0.4) & (res.positions["BBB"] > 0.4)).sum()
        self.assertEqual(both, 0)

    def test_without_max_corr_both_can_be_held(self):
        base = make_ohlcv(n=150, drift=0.002, vol=0.01, seed=11)
        dfs = {"AAA": base.copy(), "BBB": base.copy()}
        res = run_portfolio(
            dfs, "ma_cross", params={"fast": 5, "slow": 20, "trend_filter": False}, fee=FeeModel("zero"), slippage=0.0
        )
        both = ((res.positions["AAA"] > 0.4) & (res.positions["BBB"] > 0.4)).sum()
        self.assertGreater(both, 0)


class TestTopPick(unittest.TestCase):
    def _row(self, sym, position, score, sharpe, error=None):
        return {
            "symbol": sym,
            "position": position,
            "score": score,
            "sharpe": sharpe,
            "label": "ACHAT",
            "strategy_label": "X",
            "n_trades": 5,
            "win_rate": 0.6,
            "entry": 100,
            "stop": 95,
            "target": 110,
            "horizon": "≈ 10 j",
            "error": error,
        }

    def test_picks_best_achat(self):
        rows = [
            self._row("A", "ACHAT", 0.5, 1.0),
            self._row("B", "ACHAT", 0.8, 0.5),
            self._row("C", "ATTENTE", 0.9, 2.0),
            self._row("D", "ACHAT", 0.1, 2.0, error="x"),
        ]
        pick = top_pick(rows)
        self.assertIsNotNone(pick)
        self.assertIn(pick["symbol"], ("A", "B"))  # D exclu (erreur), C exclu (pas ACHAT)
        self.assertIn("why", pick)

    def test_none_when_no_achat(self):
        rows = [self._row("A", "ATTENTE", 0.9, 1.0)]
        self.assertIsNone(top_pick(rows))

    def test_empty(self):
        self.assertIsNone(top_pick([]))


if __name__ == "__main__":
    unittest.main()
