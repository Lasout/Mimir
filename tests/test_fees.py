import unittest

from tradehelper.fees import PRESETS, FeeModel, get_fee_model, list_presets


class TestFeeModel(unittest.TestCase):
    def test_flat(self):
        f = FeeModel("TR actions", flat=1.0)
        self.assertEqual(f.cost(10_000), 1.0)
        self.assertEqual(f.cost(10), 1.0)

    def test_pct(self):
        f = FeeModel("x", pct=0.001)
        self.assertAlmostEqual(f.cost(10_000), 10.0)

    def test_flat_plus_pct(self):
        f = FeeModel("x", flat=1.0, pct=0.001)
        self.assertAlmostEqual(f.cost(10_000), 11.0)

    def test_min_max(self):
        f = FeeModel("TR crypto", pct=0.01, min_fee=1.0, max_fee=50.0)
        self.assertEqual(f.cost(10), 1.0)          # min
        self.assertEqual(f.cost(1_000_000), 50.0)  # max
        self.assertAlmostEqual(f.cost(10_000), 100.0 - 50.0 + 50.0 if False else min(100.0, 50.0))

    def test_describe(self):
        self.assertIn("1 €/ordre", PRESETS["trade_republic_fr_actions"].describe())
        self.assertIn("1 %", PRESETS["trade_republic_fr_crypto"].describe())


class TestPresets(unittest.TestCase):
    def test_trade_republic_default_auto(self):
        stock = get_fee_model("trade_republic_fr_auto", "stock")
        crypto = get_fee_model("trade_republic_fr_auto", "crypto")
        forex = get_fee_model("trade_republic_fr_auto", "forex")
        self.assertEqual(stock.flat, 1.0)          # 1 €/ordre actions
        self.assertEqual(crypto.pct, 0.01)          # 1 % crypto
        self.assertEqual(crypto.min_fee, 1.0)
        self.assertIsNotNone(forex)

    def test_unknown_preset_raises(self):
        with self.assertRaises(KeyError):
            get_fee_model("nope")

    def test_presets_listed(self):
        self.assertIn("trade_republic_fr_auto", list_presets())
        self.assertIn("zero", list_presets())


if __name__ == "__main__":
    unittest.main()
