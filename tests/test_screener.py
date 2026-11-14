import unittest

import numpy as np
import pandas as pd

import tradehelper.screener as screener_mod
from tradehelper.indicators import add_all
from tradehelper.screener import format_horizon, recommend, screen_assets
from tests.helpers import make_ohlcv, make_trend_reversal


def _stable_seed(symbol: str) -> int:
    return sum(ord(c) for c in symbol) % 1000


def fake_load_data_factory():
    def fake(source, symbol, period="1y", interval="1d"):
        drift = 0.005 if symbol == "UP" else -0.004
        return make_ohlcv(n=200, drift=drift, vol=0.01, seed=_stable_seed(symbol))
    return fake


class TestHorizon(unittest.TestCase):
    def test_format_horizon(self):
        self.assertIn("h", format_horizon(6, "1h"))
        self.assertIn("j", format_horizon(10, "1d"))
        self.assertIn("mois", format_horizon(200, "1d"))


class TestRecommend(unittest.TestCase):
    def test_levels_coherent(self):
        df = add_all(make_ohlcv(n=120, drift=0.004, seed=3))
        sig = pd.DataFrame({"position": 1, "stop": np.nan, "target": np.nan, "reason": ""},
                           index=df.index)
        rec = recommend(df, sig, {"avg_bars": 8.0}, "1d")
        self.assertEqual(rec["position"], "ACHAT")
        self.assertLess(rec["stop"], rec["entry"])
        self.assertGreater(rec["target"], rec["entry"])
        self.assertIn("j", rec["horizon"])

    def test_flat_position(self):
        df = add_all(make_ohlcv(n=120))
        sig = pd.DataFrame({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""},
                           index=df.index)
        rec = recommend(df, sig, {"avg_bars": 5.0}, "1d")
        self.assertEqual(rec["position"], "ATTENTE")


class TestScreenAssets(unittest.TestCase):
    def setUp(self):
        self._orig = screener_mod.load_data
        screener_mod.load_data = fake_load_data_factory()

    def tearDown(self):
        screener_mod.load_data = self._orig

    def test_rows_structure(self):
        rows = screen_assets("crypto", ["UP", "DOWN"], period="1y", interval="1d", workers=1)
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIsNone(r["error"])
            for key in ("symbol", "score", "label", "best_strategy", "sharpe",
                        "position", "entry", "stop", "target", "horizon", "n_trades"):
                self.assertIn(key, r)
            self.assertIn(r["best_strategy"],
                          {"ma_cross", "rsi_reversion", "donchian_breakout", "macd_momentum",
                           "adaptive_regime", "ml_signal"})
            self.assertIn(r["position"], {"ACHAT", "ATTENTE", "SHORT"})
            self.assertLess(r["stop"], r["entry"])
            self.assertGreater(r["target"], r["entry"])

    def test_uptrend_recommends_buy(self):
        # ma_cross est déterministe : en forte tendance haussière, position longue à la fin
        rows = screen_assets("crypto", ["UP"], period="1y", interval="1d", strategies=["ma_cross"], workers=1)
        self.assertEqual(rows[0]["best_strategy"], "ma_cross")
        self.assertEqual(rows[0]["position"], "ACHAT")
        self.assertGreater(rows[0]["score"], 0)
        # toutes stratégies confondues : la reco reste cohérente
        rows_all = screen_assets("crypto", ["UP"], period="1y", interval="1d", workers=1)
        self.assertIn(rows_all[0]["position"], {"ACHAT", "ATTENTE", "SHORT"})

    def test_error_row(self):
        def boom(source, symbol, period="1y", interval="1d"):
            raise ValueError("pas de données")
        screener_mod.load_data = boom
        rows = screen_assets("crypto", ["XXX"], period="1y", interval="1d", workers=1)
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["error"])
        self.assertIn("Erreur", rows[0]["label"])

    def test_progress_callback(self):
        calls = []
        screen_assets("crypto", ["UP", "DOWN"], period="1y", interval="1d", workers=1,
                      on_progress=lambda i, n, s: calls.append((i, n, s)))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][1], 2)


if __name__ == "__main__":
    unittest.main()
