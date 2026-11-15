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

    def test_watchlist(self):
        self.assertEqual(self.store.watch_list(), [])
        self.store.watch_add("stock", "aapl")
        self.store.watch_add("stock", "SPY")
        self.store.watch_add("crypto", "BTCUSDT")
        self.store.watch_add("stock", "AAPL")  # doublon -> remplacé, pas dupliqué
        items = self.store.watch_list()
        self.assertEqual([(s, y) for s, y, _ in items],
                         [("crypto", "BTCUSDT"), ("stock", "AAPL"), ("stock", "SPY")])
        self.assertTrue(all(added for _, _, added in items))
        self.store.watch_remove("stock", "SPY")
        self.assertEqual([y for _, y, _ in self.store.watch_list()], ["BTCUSDT", "AAPL"])
        # persistance : réouverture de la base
        self.store.close()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.assertEqual([y for _, y, _ in self.store.watch_list()], ["BTCUSDT", "AAPL"])

    def test_parquet_cache_roundtrip(self):
        import importlib.util
        if importlib.util.find_spec("pyarrow") is None:
            self.skipTest("pyarrow non installé")
        df = make_ohlcv(n=120)
        self.store.upsert(df, "stock", "FAKE", "1d")
        pq = self.store._pq_path("stock", "FAKE", "1d")
        self.assertTrue(pq.exists(), "snapshot parquet non écrit")
        out = self.store.read("stock", "FAKE", "1d")
        self.assertEqual(len(out), 120)
        self.assertAlmostEqual(float(out["Close"].iloc[-1]), float(df["Close"].iloc[-1]))
        # filtre start/end aussi côté parquet
        mid = df.index[60]
        out2 = self.store.read("stock", "FAKE", "1d", start=mid)
        self.assertEqual(len(out2), 60)
        # repli SQLite si le parquet est supprimé
        pq.unlink()
        out3 = self.store.read("stock", "FAKE", "1d")
        self.assertEqual(len(out3), 120)

    def test_parquet_snapshot_is_full_union(self):
        # régression : un upsert incrémental ne doit PAS écraser le snapshot parquet
        # avec seulement la plage neuve (sinon read() ne renverrait que l'incrément)
        import importlib.util
        if importlib.util.find_spec("pyarrow") is None:
            self.skipTest("pyarrow non installé")
        df1 = make_ohlcv(n=100)
        self.store.upsert(df1, "stock", "FAKE", "1d")
        df2 = make_ohlcv(n=120).iloc[100:]  # 20 barres suivantes, mêmes dates de base
        self.store.upsert(df2, "stock", "FAKE", "1d")
        out = self.store.read("stock", "FAKE", "1d")
        self.assertEqual(len(out), 120)
        pq = self.store._read_parquet("stock", "FAKE", "1d")
        self.assertIsNotNone(pq)
        self.assertEqual(len(pq), 120)  # snapshot = union complète

    def test_parquet_stale_falls_back_to_sqlite(self):
        # garde-fou : snapshot parquet tronqué -> repli SQLite (comptage)
        import importlib.util
        if importlib.util.find_spec("pyarrow") is None:
            self.skipTest("pyarrow non installé")
        df = make_ohlcv(n=100)
        self.store.upsert(df, "stock", "FAKE", "1d")
        pq = self.store._pq_path("stock", "FAKE", "1d")
        pd.DataFrame({"ts": [df.index[-1]], "open": [1.0], "high": [1.0], "low": [1.0],
                      "close": [1.0], "volume": [1.0]}).to_parquet(pq, index=False)
        out = self.store.read("stock", "FAKE", "1d")
        self.assertEqual(len(out), 100)

    def test_runs_history(self):
        self.assertEqual(self.store.list_runs(), [])
        id1 = self.store.log_run("backtest", "aapl", "ma_cross : 12 trades", {"metrics": {"sharpe": 1.2}})
        id2 = self.store.log_run("screener", "multi", "10/10 actifs", {"n": 10})
        self.assertGreater(id2, id1)
        runs = self.store.list_runs()
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[0]["kind"], "screener")  # plus récent d'abord
        self.assertEqual(runs[1]["symbol"], "AAPL")     # uppercasé
        self.assertEqual(runs[1]["payload"], {"metrics": {"sharpe": 1.2}})
        self.assertTrue(runs[0]["created_at"])
        self.assertEqual(self.store.clear_runs(), 2)
        self.assertEqual(self.store.list_runs(), [])
        # persistance après réouverture
        self.store.log_run("portefeuille", "multi", "3 actifs", {"m": 1})
        self.store.close()
        self.store = Store(Path(self.tmp.name) / "test.db")
        self.assertEqual(len(self.store.list_runs()), 1)


if __name__ == "__main__":
    unittest.main()
