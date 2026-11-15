"""Tests d'intégration avec données RÉELLES (réseau) — marqués « network ».

Exclus de la suite par défaut et du CI rapide : ils ne tournent que si
    TRADEHELPER_NETWORK_TESTS=1 python3 -m unittest discover -s tests
ou `TRADEHELPER_NETWORK_TESTS=1 python3 -m unittest tests.test_integration_network -v`.
Chaque test utilise un store SQLite temporaire (aucune pollution de la base locale).
"""

import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

NETWORK = os.environ.get("TRADEHELPER_NETWORK_TESTS") == "1"


@unittest.skipUnless(NETWORK, "tests réseau : lancer avec TRADEHELPER_NETWORK_TESTS=1")
class TestIntegrationNetwork(unittest.TestCase):
    def setUp(self):
        from tradehelper.data.store import Store

        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "net.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _check_ohlcv(self, df: pd.DataFrame, min_bars: int = 10) -> None:
        self.assertIsInstance(df, pd.DataFrame)
        self.assertGreaterEqual(len(df), min_bars, f"trop peu de barres: {len(df)}")
        for col in ("Open", "High", "Low", "Close", "Volume"):
            self.assertIn(col, df.columns)
        self.assertTrue(df.index.is_monotonic_increasing, "index non trié")
        self.assertFalse(bool(df["Close"].isna().any()), "Close avec NaN")
        self.assertTrue(bool((df["High"] >= df["Low"]).all()), "High < Low")
        self.assertTrue(bool((df["Close"] > 0).all()), "Close <= 0")

    def test_stock_yahoo_1mo_daily(self):
        from tradehelper.data.sources import load_data

        df = load_data("stock", "AAPL", period="1mo", interval="1d", store=self.store)
        self._check_ohlcv(df, min_bars=10)

    def test_stock_intraday_1h(self):
        # Yahoo limite l'intraday (1h = 730 j max) : on vérifie que la source répond
        from tradehelper.data.sources import load_data

        df = load_data("stock", "MSFT", period="1mo", interval="1h", store=self.store)
        self._check_ohlcv(df, min_bars=50)

    def test_crypto_binance_3mo_daily(self):
        from tradehelper.data.sources import load_data

        df = load_data("crypto", "BTCUSDT", period="3mo", interval="1d", store=self.store)
        self._check_ohlcv(df, min_bars=30)

    def test_forex_frankfurter_1mo(self):
        from tradehelper.data.sources import load_data

        df = load_data("forex", "EURUSD", period="1mo", interval="1d", store=self.store)
        self._check_ohlcv(df, min_bars=10)

    def test_store_sert_du_cache_au_2e_appel(self):
        from tradehelper.data.sources import load_data

        df1 = load_data("stock", "AAPL", period="1mo", interval="1d", store=self.store)
        n1 = self.store.count_source("stock")
        self.assertGreater(n1, 0)
        df2 = load_data("stock", "AAPL", period="1mo", interval="1d", store=self.store)
        self.assertEqual(len(df1), len(df2))
        self.assertEqual(n1, self.store.count_source("stock"))  # aucun nouvel appel réseau

    def test_advise_action_reelle(self):
        from tradehelper.advice import advise
        from tradehelper.data.sources import load_data
        from tradehelper.indicators import add_all

        df = add_all(load_data("stock", "AAPL", period="6mo", interval="1d", store=self.store))
        adv = advise(df, "AAPL", source="stock", interval="1d")
        for key in ("score", "label", "reasons", "last_close", "stop_suggestion", "risk", "horizon"):
            self.assertIn(key, adv)
        self.assertGreaterEqual(adv["score"], -1.0)
        self.assertLessEqual(adv["score"], 1.0)
        self.assertGreater(adv["last_close"], 0)

    def test_screener_2_actifs_reels(self):
        import tradehelper.screener as screener_mod

        rows = screener_mod.screen_assets(
            "stock",
            ["AAPL", "MSFT"],
            period="6mo",
            interval="1d",
            strategies=["ma_cross", "rsi_reversion"],
            workers=1,
        )
        self.assertEqual(len(rows), 2)
        for r in rows:
            self.assertIsNone(r["error"], f"{r['symbol']}: {r['error']}")
            self.assertIn(r["position"], ("ACHAT", "ATTENTE", "SHORT"))
            self.assertIn("horizon", r)


if __name__ == "__main__":
    unittest.main()
