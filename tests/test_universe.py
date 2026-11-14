import unittest

from tradehelper.data.universe import ETF_LIST, UNIVERSE, asset_type, default_symbols, universe_for


class TestUniverse(unittest.TestCase):
    def test_all_sources_have_assets(self):
        for src in ("stock", "crypto", "forex"):
            self.assertGreater(len(universe_for(src)), 5)

    def test_crypto_pairs_are_usdt(self):
        self.assertTrue(all(s.endswith("USDT") for s in universe_for("crypto")))

    def test_forex_pairs_are_6_chars(self):
        self.assertTrue(all(len(s) == 6 for s in universe_for("forex")))

    def test_unknown_source_empty(self):
        self.assertEqual(universe_for("inconnu"), [])

    def test_keys_match_sources(self):
        self.assertEqual(set(UNIVERSE), {"stock", "crypto", "forex"})

    def test_etf_list_subset_of_stock_universe(self):
        self.assertTrue(set(ETF_LIST) <= set(UNIVERSE["stock"]))
        self.assertGreaterEqual(len(ETF_LIST), 10)

    def test_asset_type(self):
        self.assertEqual(asset_type("stock", "SPY"), "ETF")
        self.assertEqual(asset_type("stock", "vwce.de"), "ETF")
        self.assertEqual(asset_type("stock", "AAPL"), "Action")
        self.assertEqual(asset_type("crypto", "BTCUSDT"), "Crypto")
        self.assertEqual(asset_type("forex", "EURUSD"), "Forex")

    def test_default_symbols_stock_mixes_etf(self):
        # régression : les ETF doivent redescendre dans les listes par défaut screener/portefeuille
        for n in (6, 10, 12):
            syms = default_symbols("stock", n)
            self.assertEqual(len(syms), n)
            self.assertTrue(any(s in ETF_LIST for s in syms), f"pas d'ETF dans default_symbols(stock,{n})")
            self.assertTrue(any(s not in ETF_LIST for s in syms))

    def test_default_symbols_other_sources(self):
        self.assertEqual(default_symbols("crypto", 5), universe_for("crypto")[:5])
        self.assertEqual(default_symbols("forex", 5), universe_for("forex")[:5])
        self.assertEqual(default_symbols("stock", 10_000), universe_for("stock"))


if __name__ == "__main__":
    unittest.main()
