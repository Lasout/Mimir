import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tradehelper.data.store import Store
from tests.helpers import make_ohlcv


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_upsert_read_roundtrip(self):
        df = make_ohlcv(n=100)
        n = self.store.upsert(df, "stock", "FAKE", "1d")
        self.assertEqual(n, 100)
        out = self.store.read("stock", "FAKE", "1d")
        self.assertEqual(len(out), 100)
        self.assertEqual(list(out.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertAlmostEqual(float(out["Close"].iloc[-1]), float(df["Close"].iloc[-1]))
        self.assertTrue(out.index.is_monotonic_increasing)

    def test_read_range(self):
        df = make_ohlcv(n=100)
        self.store.upsert(df, "stock", "FAKE", "1d")
        mid = df.index[50]
        out = self.store.read("stock", "FAKE", "1d", start=mid, end=df.index[-1])
        self.assertEqual(len(out), 50)
        self.assertEqual(out.index[0], mid.normalize())

    def test_incremental_upsert_and_replace(self):
        df = make_ohlcv(n=50)
        self.store.upsert(df, "crypto", "BTCUSDT", "1d")
        # chevauchement + nouvelles barres : les conflits sont remplacés
        df2 = make_ohlcv(n=60, seed=9)
        df2.index = df.index[-10:].append(pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=50))
        self.store.upsert(df2, "crypto", "BTCUSDT", "1d")
        self.assertEqual(self.store.count("crypto", "BTCUSDT", "1d"), 100)
        self.assertEqual(self.store.first_ts("crypto", "BTCUSDT", "1d"), df.index[0])
        self.assertEqual(self.store.last_ts("crypto", "BTCUSDT", "1d"), df2.index[-1])

    def test_isolation_symbol_interval(self):
        df = make_ohlcv(n=30)
        self.store.upsert(df, "stock", "AAA", "1d")
        self.store.upsert(df, "stock", "AAA", "1h")
        self.store.upsert(df, "stock", "BBB", "1d")
        self.assertEqual(self.store.count("stock", "AAA", "1d"), 30)
        self.assertEqual(self.store.count("stock", "AAA", "1h"), 30)
        self.assertEqual(self.store.count("stock", "BBB", "1d"), 30)
        self.assertEqual(self.store.count("crypto", "AAA", "1d"), 0)

    def test_meta(self):
        self.assertIsNone(self.store.meta_get("k"))
        self.store.meta_set("k", {"a": 1, "when": "2026-01-01T00:00:00"})
        self.assertEqual(self.store.meta_get("k"), {"a": 1, "when": "2026-01-01T00:00:00"})

    def test_read_empty(self):
        out = self.store.read("stock", "NOPE", "1d")
        self.assertTrue(out.empty)
        self.assertIsNone(self.store.last_ts("stock", "NOPE", "1d"))

    def test_symbol_uppercased(self):
        df = make_ohlcv(n=10)
        self.store.upsert(df, "stock", "aapl", "1d")
        self.assertEqual(self.store.count("stock", "AAPL", "1d"), 10)


if __name__ == "__main__":
    unittest.main()
