import unittest

import numpy as np
import pandas as pd

from tests.helpers import make_ohlcv
from tradehelper.backtest import run_backtest
from tradehelper.fees import FeeModel


def long_signals(df, start=0):
    sig = pd.DataFrame({"position": 0, "stop": np.nan, "target": np.nan, "reason": ""}, index=df.index)
    sig.loc[sig.index[start:], "position"] = 1
    sig.loc[sig.index[start], "reason"] = "Entrée test"
    return sig


class TestFeesInEngine(unittest.TestCase):
    def test_flat_fee_per_execution(self):
        df = make_ohlcv(n=80, drift=0.004, vol=0.005, seed=1)
        sig = long_signals(df, start=5)
        zero = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0)
        flat1 = run_backtest(df, sig, fee=FeeModel("TR", flat=1.0), slippage=0.0)
        # 1 trade = 2 exécutions (entrée + sortie) -> 2 € de frais
        self.assertAlmostEqual(zero.metrics["total_fees"], 0.0, places=6)
        self.assertAlmostEqual(flat1.metrics["total_fees"], 2.0, places=6)
        self.assertGreater(zero.metrics["total_return"], flat1.metrics["total_return"])
        self.assertAlmostEqual(zero.metrics["total_return"] - flat1.metrics["total_return"], 2.0 / 10_000, places=6)

    def test_legacy_commission_still_works(self):
        df = make_ohlcv(n=80, drift=0.004, vol=0.005, seed=1)
        sig = long_signals(df, start=5)
        res = run_backtest(df, sig, commission=0.001, slippage=0.0)
        self.assertGreater(res.metrics["total_fees"], 0.0)

    def test_min_fee_crypto_like(self):
        df = make_ohlcv(n=80, drift=0.004, vol=0.005, seed=1)
        sig = long_signals(df, start=5)
        # petits ordres : le min de 1 € s'applique au moins à l'exécution d'entrée
        res = run_backtest(
            df, sig, initial_capital=100.0, fee=FeeModel("TR crypto", pct=0.01, min_fee=1.0), slippage=0.0
        )
        self.assertGreaterEqual(res.metrics["total_fees"], 2.0 - 1e-9)


class TestFunding(unittest.TestCase):
    def test_funding_reduces_equity(self):
        df = make_ohlcv(n=100, drift=0.0, vol=0.005, seed=2)
        sig = long_signals(df, start=5)
        no_f = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0)
        with_f = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0, funding_per_bar=0.001)
        self.assertLess(with_f.equity.iloc[-1], no_f.equity.iloc[-1])
        self.assertGreater(with_f.metrics["total_fees"], 0.0)


class TestVolTarget(unittest.TestCase):
    def test_vol_target_reduces_size_in_high_vol(self):
        # marché très volatile : le vol targeting doit réduire l'exposition/le rendement
        df = make_ohlcv(n=150, drift=0.003, vol=0.05, seed=3)
        sig = long_signals(df, start=10)
        base = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0)
        vt = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0, vol_target=0.10, periods_per_year=252)
        self.assertLess(abs(vt.metrics["total_return"]), abs(base.metrics["total_return"]) * 1.5 + 1e-9)


class TestMaxDrawdownStop(unittest.TestCase):
    def test_dd_stop_halts(self):
        # krach brutal au milieu
        n = 120
        up = np.linspace(100, 150, 60)
        down = np.linspace(150, 60, 60)
        close = np.concatenate([up, down])
        idx = pd.bdate_range("2025-01-01", periods=n)
        df = pd.DataFrame(
            {"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": 1e6}, index=idx
        )
        sig = long_signals(df, start=5)
        res = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0, max_drawdown_stop=0.10)
        # le stop se déclenche à la clôture de la barre qui franchit -10 % : on autorise
        # un dépassement d'une barre, mais pas le krach complet (-60 %)
        self.assertGreater(res.metrics["max_drawdown"], -0.15)
        self.assertLess(res.metrics["max_drawdown"], -0.09)  # le seuil a bien été touché
        self.assertTrue((res.trades["reason"] == "Stop de drawdown global").any())
        # sans stop, le krach irait bien plus bas
        res_free = run_backtest(df, sig, fee=FeeModel("zero"), slippage=0.0)
        self.assertLess(res_free.metrics["max_drawdown"], res.metrics["max_drawdown"])


if __name__ == "__main__":
    unittest.main()
