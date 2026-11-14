import unittest

import numpy as np

from tradehelper.advice import advise
from tradehelper.strategies import get_strategy, list_strategies
from tests.helpers import make_ohlcv, make_trend_reversal


class TestStrategies(unittest.TestCase):
    def test_registry(self):
        keys = [s["key"] for s in list_strategies()]
        self.assertEqual(set(keys), {"ma_cross", "rsi_reversion", "donchian_breakout",
                                     "macd_momentum", "adaptive_regime", "ml_signal"})

    def test_signals_shape(self):
        df = make_ohlcv(n=200)
        for meta in list_strategies():
            sig = get_strategy(meta["key"]).generate(df)
            self.assertEqual(len(sig), len(df))
            for col in ("position", "stop", "target", "reason"):
                self.assertIn(col, sig.columns)
            self.assertTrue(set(sig["position"].unique()) <= {-1, 0, 1})

    def test_ma_cross_entries_and_exits_on_reversal(self):
        df = make_trend_reversal(n=300)
        sig = get_strategy("ma_cross", fast=10, slow=30, trend_filter=False).generate(df)
        self.assertIn(1, sig["position"].values)   # au moins une entrée
        self.assertIn(0, sig["position"].iloc[10:].values)  # et une sortie après

    def test_rsi_reversion_on_oscillation(self):
        n = 300
        close = 100 + 8 * np.sin(np.linspace(0, 6 * np.pi, n))
        import pandas as pd
        idx = pd.bdate_range("2025-01-01", periods=n)
        df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                           "Close": close, "Volume": 1e6}, index=idx)
        sig = get_strategy("rsi_reversion", oversold=35, exit_level= 60).generate(df)
        self.assertGreater((sig["position"] == 1).sum(), 0)

    def test_custom_params(self):
        s = get_strategy("ma_cross", fast=5, slow=10)
        self.assertEqual(s.params["fast"], 5)
        self.assertEqual(s.params["slow"], 10)


class TestAdvice(unittest.TestCase):
    def test_advise_crypto_no_fundamentals(self):
        df = make_ohlcv(n=120, drift=0.003)
        adv = advise(df, "BTCUSDT", source="crypto", interval="1d")
        self.assertIn(adv["label"], {"ACHAT FORT", "ACHAT", "NEUTRE", "PRUDENCE (réduire)", "VENTE / ÉVITER"})
        self.assertGreaterEqual(adv["score"], -1.0)
        self.assertLessEqual(adv["score"], 1.0)
        self.assertFalse(adv["fundamental_available"])
        self.assertTrue(len(adv["reasons"]) > 0)
        self.assertIn("Swing", adv["horizon"])

    def test_advise_intraday_horizon(self):
        df = make_ohlcv(n=120)
        adv = advise(df, "BTCUSDT", source="crypto", interval="15m")
        self.assertIn("Day trading", adv["horizon"])

    def test_uptrend_positive_score(self):
        df = make_ohlcv(n=150, drift=0.006, vol=0.008, seed=2)
        adv = advise(df, "TEST", source="crypto", interval="1d")
        self.assertGreater(adv["score"], 0.0)


if __name__ == "__main__":
    unittest.main()
