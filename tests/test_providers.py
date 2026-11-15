import unittest

import pandas as pd

from tradehelper.config import DEFAULTS
from tradehelper.data.providers import BinanceProvider, DataError, normalize, yahoo_symbol


class TestNormalize(unittest.TestCase):
    def test_tz_aware_to_naive_and_columns(self):
        idx = pd.date_range("2026-01-01", periods=5, tz="America/New_York")
        df = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}, index=idx)
        out = normalize(df)
        self.assertIsNone(out.index.tz)
        self.assertEqual(list(out.columns), ["Open", "High", "Low", "Close", "Volume"])

    def test_missing_close_raises(self):
        df = pd.DataFrame({"Open": [1.0], "High": [2.0], "Low": [0.5]}, index=pd.date_range("2026-01-01", periods=1))
        with self.assertRaises(DataError):
            normalize(df)


class TestYahooSymbol(unittest.TestCase):
    def test_mappings(self):
        self.assertEqual(yahoo_symbol("stock", "aapl"), "AAPL")
        self.assertEqual(yahoo_symbol("crypto", "BTCUSDT"), "BTC-USD")
        self.assertEqual(yahoo_symbol("crypto", "ETHUSDT"), "ETH-USD")
        self.assertEqual(yahoo_symbol("forex", "EURUSD"), "EURUSD=X")


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Session factice qui pagine comme Binance (1000 klines par appel)."""

    def __init__(self, start_ms, total_bars, sec):
        self.start_ms = start_ms
        self.total_bars = total_bars
        self.sec = sec
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((params["startTime"], params["endTime"], params["limit"]))
        start_idx = (params["startTime"] - self.start_ms) // (self.sec * 1000)
        end_idx = min(self.total_bars, start_idx + params["limit"])
        if start_idx >= self.total_bars:
            return FakeResponse([])
        rows = []
        for i in range(start_idx, end_idx):
            t = self.start_ms + i * self.sec * 1000
            rows.append([t, "100", "101", "99", "100.5", "10", t + self.sec * 1000 - 1, "0", 0, "0", "0", "0"])
        return FakeResponse(rows)


class TestBinancePagination(unittest.TestCase):
    def test_paginates_multiple_pages(self):
        start = pd.Timestamp("2026-01-01")
        end = start + pd.Timedelta(days=2500)  # ~2500 barres 1d -> 3 pages
        sec = 86400
        start_ms = int(start.timestamp() * 1000)
        session = FakeSession(start_ms, total_bars=2500, sec=sec)
        prov = BinanceProvider()
        df = prov.fetch("crypto", "BTCUSDT", start, end, "1d", DEFAULTS, session=session)
        self.assertEqual(len(df), 2500)
        self.assertEqual(len(session.calls), 3)  # 1000 + 1000 + 500
        self.assertEqual(session.calls[0][2], 1000)
        # le curseur avance d'exactement une barre après la dernière kline reçue
        self.assertEqual(session.calls[1][0], session.calls[0][0] + 1000 * sec * 1000)
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertAlmostEqual(float(df["Close"].iloc[0]), 100.5)

    def test_respects_max_bars(self):
        cfg = dict(DEFAULTS, max_bars_per_fetch=1500)
        start = pd.Timestamp("2026-01-01")
        end = start + pd.Timedelta(days=5000)
        sec = 86400
        session = FakeSession(int(start.timestamp() * 1000), total_bars=5000, sec=sec)
        df = BinanceProvider().fetch("crypto", "BTCUSDT", start, end, "1d", cfg, session=session)
        self.assertEqual(len(df), 1500)

    def test_supports(self):
        prov = BinanceProvider()
        self.assertTrue(prov.supports("crypto", "1d"))
        self.assertFalse(prov.supports("stock", "1d"))
        self.assertFalse(prov.supports("crypto", "3d"))

    def test_empty_raises(self):
        start = pd.Timestamp("2026-01-01")
        session = FakeSession(int(start.timestamp() * 1000), total_bars=0, sec=86400)
        with self.assertRaises(DataError):
            BinanceProvider().fetch(
                "crypto", "XXXUSDT", start, start + pd.Timedelta(days=5), "1d", DEFAULTS, session=session
            )


if __name__ == "__main__":
    unittest.main()
