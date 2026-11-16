"""AUD-05 : détection de régime de marché (haussier / plat / baissier)."""

import unittest

import pandas as pd

import tradehelper.screener as screener_mod
from tests.helpers import make_ohlcv
from tradehelper.regime import detect_regime, regime_coverage


def _from_closes(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    idx = pd.bdate_range("2025-01-01", periods=n)
    c = pd.Series(closes, index=idx)
    return pd.DataFrame(
        {
            "Open": c.shift(1).fillna(c.iloc[0]),
            "High": c * 1.005,
            "Low": c * 0.995,
            "Close": c,
            "Volume": pd.Series(2_000_000.0, index=idx),
        }
    )


def _trend(n: int, start: float, step: float) -> pd.DataFrame:
    return _from_closes([start + i * step for i in range(n)])


class TestDetectRegime(unittest.TestCase):
    def test_haussier(self):
        r = detect_regime(_trend(80, 100.0, 1.0))  # +1 par barre, forte pente
        self.assertEqual(r["label"], "haussier")
        self.assertGreater(r["close_vs_sma"], 0)
        self.assertGreater(r["sma_slope"], 0)

    def test_baissier(self):
        r = detect_regime(_trend(80, 200.0, -1.0))
        self.assertEqual(r["label"], "baissier")
        self.assertLess(r["close_vs_sma"], 0)
        self.assertLess(r["sma_slope"], 0)

    def test_plat(self):
        closes = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(80)]
        self.assertEqual(detect_regime(_from_closes(closes))["label"], "plat")

    def test_peu_de_bars(self):
        # pas assez d'historique -> neutre plutôt qu'un faux régime
        self.assertEqual(detect_regime(_trend(10, 100.0, 1.0))["label"], "plat")

    def test_make_ohlcv_haussier(self):
        r = detect_regime(make_ohlcv(n=120, drift=0.01, vol=0.005, seed=5))
        self.assertEqual(r["label"], "haussier")


class TestRegimeCoverage(unittest.TestCase):
    def test_deux_regimes_ok(self):
        cov = regime_coverage(["haussier", "baissier", "haussier", "plat"])
        self.assertTrue(cov["ok"])
        self.assertEqual(cov["n_regimes"], 3)
        self.assertEqual(cov["counts"]["haussier"], 2)

    def test_un_seul_regime_pas_ok(self):
        cov = regime_coverage(["haussier"] * 10)
        self.assertFalse(cov["ok"])
        self.assertEqual(cov["n_regimes"], 1)

    def test_vide_pas_ok(self):
        cov = regime_coverage([])
        self.assertFalse(cov["ok"])
        self.assertEqual(cov["n_regimes"], 0)


class TestScreenerRegime(unittest.TestCase):
    """La ligne du screener porte le régime de la période testée (AUD-05)."""

    def setUp(self):
        self._orig = screener_mod.load_data

    def tearDown(self):
        screener_mod.load_data = self._orig  # type: ignore[attr-defined]

    def test_row_succes_contient_regime(self):
        df = make_ohlcv(n=200, drift=0.006, vol=0.01, seed=9)  # tendance haussière nette
        screener_mod.load_data = lambda *a, **k: df  # type: ignore[attr-defined]
        rows = screener_mod.screen_assets("crypto", ["FAKE"], strategies=["ma_cross"], workers=1)
        self.assertIsNone(rows[0]["error"])
        self.assertEqual(rows[0]["regime"], "haussier")

    def test_row_erreur_contient_regime_vide(self):
        def _boom(*a, **k):
            raise ValueError("boom")

        screener_mod.load_data = _boom  # type: ignore[attr-defined]
        rows = screener_mod.screen_assets("crypto", ["FAKE"], strategies=["ma_cross"], workers=1)
        self.assertIn("regime", rows[0])
        self.assertEqual(rows[0]["regime"], "")


if __name__ == "__main__":
    unittest.main()
