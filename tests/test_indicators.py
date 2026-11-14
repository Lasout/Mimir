import unittest

import numpy as np
import pandas as pd

from tradehelper.indicators import (adx, atr, bollinger_bands, ema, macd, obv,
                                    rsi, rolling_volatility, sma)
from tests.helpers import make_ohlcv


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = sma(s, 3)
        self.assertTrue(np.isnan(out.iloc[1]))
        self.assertAlmostEqual(out.iloc[4], 4.0)

    def test_ema_converges(self):
        s = pd.Series(np.full(100, 10.0))
        out = ema(s, 10)
        self.assertAlmostEqual(out.iloc[-1], 10.0, places=6)

    def test_rsi_all_up_is_100(self):
        s = pd.Series(np.arange(1.0, 50.0))
        out = rsi(s, 14)
        self.assertAlmostEqual(out.iloc[-1], 100.0)

    def test_rsi_all_down_is_0(self):
        s = pd.Series(np.arange(50.0, 1.0, -1.0))
        out = rsi(s, 14)
        self.assertAlmostEqual(out.iloc[-1], 0.0, places=3)

    def test_macd_columns(self):
        df = make_ohlcv()
        m = macd(df["Close"])
        for col in ("macd", "signal", "hist"):
            self.assertIn(col, m.columns)
        self.assertAlmostEqual(float(m["hist"].iloc[-1]), float(m["macd"].iloc[-1] - m["signal"].iloc[-1]))

    def test_bollinger_bands_ordering(self):
        df = make_ohlcv()
        bb = bollinger_bands(df["Close"])
        tail = bb.dropna().iloc[-1]
        self.assertGreater(tail["upper"], tail["middle"])
        self.assertGreater(tail["middle"], tail["lower"])

    def test_atr_positive(self):
        df = make_ohlcv()
        a = atr(df, 14).dropna()
        self.assertTrue((a > 0).all())

    def test_adx_range(self):
        df = make_ohlcv()
        a = adx(df, 14).dropna()
        self.assertTrue(((a >= 0) & (a <= 100)).all())

    def test_obv_and_vol(self):
        df = make_ohlcv()
        o = obv(df)
        self.assertEqual(len(o), len(df))
        v = rolling_volatility(df["Close"], 20)
        self.assertTrue((v.dropna() > 0).all())


if __name__ == "__main__":
    unittest.main()
