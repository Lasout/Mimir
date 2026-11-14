import unittest

from tradehelper.data.universe import UNIVERSE, universe_for


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


if __name__ == "__main__":
    unittest.main()
