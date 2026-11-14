import unittest

import numpy as np

import tradehelper.compute as compute
from tradehelper.compute import add_all_dispatch, detect_backend
from tradehelper.indicators import add_all
from tests.helpers import make_ohlcv


class TestDetectBackend(unittest.TestCase):
    def setUp(self):
        self._orig_gpu = compute.gpu_info

    def tearDown(self):
        compute.gpu_info = self._orig_gpu

    def test_auto_without_gpu_is_cpu(self):
        compute.gpu_info = lambda: None
        self.assertEqual(detect_backend({"compute_backend": "auto"}), "cpu")

    def test_auto_with_gpu_is_cuda(self):
        compute.gpu_info = lambda: {"backend": "cuda", "device": "Fake RTX", "devices": 1, "vram_gb": 16}
        self.assertEqual(detect_backend({"compute_backend": "auto"}), "cuda")

    def test_explicit_cpu(self):
        compute.gpu_info = lambda: {"backend": "cuda", "device": "Fake RTX", "devices": 1, "vram_gb": 16}
        self.assertEqual(detect_backend({"compute_backend": "cpu"}), "cpu")

    def test_explicit_cuda_without_gpu_raises(self):
        compute.gpu_info = lambda: None
        with self.assertRaises(RuntimeError):
            detect_backend({"compute_backend": "cuda"})

    def test_env_override(self):
        import os
        compute.gpu_info = lambda: None
        os.environ["TRADEHELPER_BACKEND"] = "cpu"
        try:
            self.assertEqual(detect_backend({}), "cpu")
        finally:
            del os.environ["TRADEHELPER_BACKEND"]


class TestAddAllDispatch(unittest.TestCase):
    def test_cpu_matches_pandas(self):
        df = make_ohlcv(n=120)
        a = add_all(df)
        b = add_all_dispatch(df, backend="cpu")
        for col in ("SMA20", "EMA50", "RSI14", "MACD", "BB_upper", "ATR14", "ADX14", "Vol20"):
            self.assertTrue(np.allclose(a[col].to_numpy(dtype=float), b[col].to_numpy(dtype=float),
                                        equal_nan=True), col)

    def test_cuda_without_cupy_falls_back_to_cpu(self):
        # dans le bac à sable (pas de cupy), backend="cuda" doit retomber sur CPU sans erreur
        df = make_ohlcv(n=120)
        a = add_all(df)
        try:
            import cupy  # noqa
            self.skipTest("cupy présent : le test de repli GPU n'a pas de sens ici")
        except ImportError:
            pass
        b = add_all_dispatch(df, backend="cuda")
        self.assertTrue(np.allclose(a["RSI14"].to_numpy(dtype=float), b["RSI14"].to_numpy(dtype=float),
                                    equal_nan=True))

    def test_auto_dispatch_works(self):
        df = make_ohlcv(n=80)
        out = add_all_dispatch(df, backend="auto")
        self.assertIn("RSI14", out.columns)


@unittest.skipUnless(compute.gpu_info() is not None, "pas de GPU CUDA dans cet environnement")
class TestGpuEquivalence(unittest.TestCase):
    """Comparaison GPU vs CPU — exécutée uniquement sur une machine avec carte NVIDIA."""

    def test_gpu_matches_cpu(self):
        df = make_ohlcv(n=300, seed=5)
        cpu = add_all(df)
        gpu = compute.add_all_gpu(df)
        for col in ("SMA20", "EMA12", "EMA26", "RSI14", "MACD", "MACD_signal", "BB_upper",
                    "ATR14", "ADX14", "Donchian_upper", "Vol20"):
            self.assertTrue(np.allclose(cpu[col].to_numpy(dtype=float), gpu[col].to_numpy(dtype=float),
                                        atol=1e-8, equal_nan=True), col)


class TestMLDeviceParam(unittest.TestCase):
    def test_ml_accepts_device(self):
        from tradehelper.strategies import get_strategy
        s = get_strategy("ml_signal", device="cpu")
        self.assertEqual(s.params["device"], "cpu")


class TestScreenerParallel(unittest.TestCase):
    def test_pool_workers(self):
        import multiprocessing as mp

        import tradehelper.screener as sm
        from tests.helpers import make_ohlcv

        def fake_load(source, symbol, period="1y", interval="1d", **kw):
            return make_ohlcv(n=150, drift=0.002, vol=0.01, seed=sum(ord(c) for c in symbol) % 500)

        orig_load = sm.load_data
        sm.load_data = fake_load
        old_method = mp.get_start_method(allow_none=True)
        try:
            mp.set_start_method("fork", force=True)  # le fork hérite du monkeypatch
            rows = sm.screen_assets("crypto", ["AAA", "BBB", "CCC"], period="1y",
                                    interval="1d", workers=2)
        finally:
            mp.set_start_method(old_method or "fork", force=True)
            sm.load_data = orig_load
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["symbol"] for r in rows], ["AAA", "BBB", "CCC"])  # ordre conservé
        for r in rows:
            self.assertIsNone(r["error"])
            self.assertIn("position", r)


if __name__ == "__main__":
    unittest.main()
