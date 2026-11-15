"""Tests de la logique incrémentale de load_data (fournisseurs factices, store temporaire)."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import tradehelper.data.sources as src
from tradehelper.config import DEFAULTS
from tradehelper.data.providers import DataError
from tradehelper.data.store import Store


def make_df(start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    idx = pd.bdate_range(start, end)
    n = len(idx)
    close = pd.Series(100.0, index=idx) + range(n)
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1000.0}, index=idx)


class FakeProvider:
    def __init__(self, name="fake", fail=False):
        self.name = name
        self.fail = fail
        self.calls = []

    def supports(self, source, interval):
        return True

    def fetch(self, source, symbol, start, end, interval, cfg, session=None):
        self.calls.append((pd.Timestamp(start), pd.Timestamp(end)))
        if self.fail:
            raise DataError(f"{self.name} en panne")
        return make_df(pd.Timestamp(start), pd.Timestamp(end))


class TestLoadDataIncremental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.cfg = dict(DEFAULTS, ttl_seconds={"default": 10_000, "1d": 10_000})
        self._orig_providers_for = src.providers_for

    def tearDown(self):
        src.providers_for = self._orig_providers_for
        self.store.close()
        self.tmp.cleanup()

    def _patch(self, providers):
        src.providers_for = lambda source, cfg: providers

    def test_first_call_fetches_full_range(self):
        prov = FakeProvider()
        self._patch([prov])
        df = src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        self.assertFalse(df.empty)
        self.assertEqual(len(prov.calls), 1)
        start, end = prov.calls[0]
        self.assertLess(start, end)
        self.assertGreaterEqual((end - start).days, 29)  # ~1 mois demandé
        self.assertLessEqual((end - start).days, 32)

    def test_second_call_within_ttl_serves_store(self):
        prov = FakeProvider()
        self._patch([prov])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        df2 = src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        self.assertEqual(len(prov.calls), 1)  # pas de nouvel appel
        self.assertFalse(df2.empty)

    def test_after_ttl_fetches_only_missing_tail(self):
        prov = FakeProvider()
        self._patch([prov])
        cfg = dict(self.cfg, ttl_seconds={"default": 0, "1d": 0})  # TTL nul -> toujours frais à retélécharger
        src.load_data("stock", "FAKE", period="3mo", interval="1d", store=self.store, cfg=self.cfg)
        src.load_data("stock", "FAKE", period="3mo", interval="1d", store=self.store, cfg=cfg)
        self.assertEqual(len(prov.calls), 2)
        first_start = prov.calls[0][0]
        tail_start = prov.calls[1][0]
        # 2e appel : seulement la fin manquante (dernier ts stocké + 1 barre)
        last = self.store.last_ts("stock", "FAKE", "1d")
        self.assertEqual(tail_start, last + pd.Timedelta(days=1))
        self.assertGreater(tail_start, first_start)

    def test_fallback_to_second_provider(self):
        p1 = FakeProvider("p1", fail=True)
        p2 = FakeProvider("p2")
        self._patch([p1, p2])
        df = src.load_data("crypto", "BTCUSDT", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        self.assertFalse(df.empty)
        self.assertEqual(len(p1.calls), 1)
        self.assertEqual(len(p2.calls), 1)

    def test_all_providers_fail_empty_store_raises(self):
        self._patch([FakeProvider("p1", fail=True), FakeProvider("p2", fail=True)])
        with self.assertRaises(DataError):
            src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)

    def test_fetch_fails_but_store_has_data_serves_store(self):
        prov_ok = FakeProvider("ok")
        self._patch([prov_ok])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        # puis le fournisseur tombe en panne et le TTL expire -> on sert le store
        self._patch([FakeProvider("down", fail=True)])
        cfg = dict(self.cfg, ttl_seconds={"default": 0, "1d": 0})
        df = src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=cfg)
        self.assertFalse(df.empty)

    def test_max_period_fetches_from_1970(self):
        prov = FakeProvider()
        self._patch([prov])
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        self.assertEqual(prov.calls[0][0], pd.Timestamp("1970-01-01"))

    def test_head_probe_is_cached(self):
        # le fournisseur ne renvoie des données qu'à partir de 2020 : la 2e sonde de tête est évitée
        class Since2020(FakeProvider):
            def fetch(self, source, symbol, start, end, interval, cfg, session=None):
                self.calls.append((pd.Timestamp(start), pd.Timestamp(end)))
                start = max(pd.Timestamp(start), pd.Timestamp("2020-01-01"))
                if start >= pd.Timestamp(end):
                    from tradehelper.data.providers import DataError as DE
                    raise DE("rien avant 2020")
                return make_df(start, pd.Timestamp(end))
        prov = Since2020("since2020")
        self._patch([prov])
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        n_calls = len(prov.calls)
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        # la tête [1970, 2020] n'est sondée qu'une fois (cache 24 h) ; la queue est fraîche (TTL long)
        heads = [c for c in prov.calls if c[0] == pd.Timestamp("1970-01-01")]
        self.assertEqual(len(heads), 1)
        self.assertGreaterEqual(len(prov.calls), n_calls)

    def test_head_refetched_when_probe_does_not_cover_max(self):
        prov = FakeProvider()
        self._patch([prov])
        src.load_data("stock", "FAKE", period="1y", interval="1d", store=self.store, cfg=self.cfg)
        n = len(prov.calls)
        # un appel "max" doit re-sonder la tête depuis 1970 (la sonde 1y ne couvre pas)
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        heads = [c for c in prov.calls[n:] if c[0] == pd.Timestamp("1970-01-01")]
        self.assertEqual(len(heads), 1)
        # et le 2e appel "max" ne re-sonde plus (cache 24 h)
        n = len(prov.calls)
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=self.cfg)
        heads2 = [c for c in prov.calls[n:] if c[0] == pd.Timestamp("1970-01-01")]
        self.assertEqual(len(heads2), 0)

    def test_head_probe_not_recorded_when_all_providers_fail(self):
        # régression : si tous les fournisseurs échouent (panne réseau), on ne doit PAS
        # mémoriser « rien avant first_bar » — sinon l'historique ne serait jamais re-téléchargé
        prov_ok = FakeProvider("ok")
        self._patch([prov_ok])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        head_before = self.store.meta_get("head|stock|FAKE|1d")
        self._patch([FakeProvider("down", fail=True), FakeProvider("down2", fail=True)])
        cfg = dict(self.cfg, ttl_seconds={"default": 0, "1d": 0})
        df = src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=cfg)
        self.assertFalse(df.empty)  # servi depuis le store (mode hors-ligne)
        # la panne totale ne doit pas écraser la sonde de tête par une sonde depuis 1970
        head_after = self.store.meta_get("head|stock|FAKE|1d")
        self.assertEqual(head_after, head_before)
        # fournisseur réparé : la tête est bien re-téléchargée depuis 1970
        prov2 = FakeProvider("ok2")
        self._patch([prov2])
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=cfg)
        heads = [c for c in prov2.calls if c[0] == pd.Timestamp("1970-01-01")]
        self.assertEqual(len(heads), 1)

    def test_head_probe_recorded_when_provider_answers_empty(self):
        class Tail(FakeProvider):
            def fetch(self, source, symbol, start, end, interval, cfg, session=None):
                self.calls.append((pd.Timestamp(start), pd.Timestamp(end)))
                return make_df(pd.Timestamp(end) - pd.Timedelta(days=30), pd.Timestamp(end))

        class Empty(FakeProvider):
            def fetch(self, source, symbol, start, end, interval, cfg, session=None):
                self.calls.append((pd.Timestamp(start), pd.Timestamp(end)))
                return pd.DataFrame()  # a répondu : aucune donnée avant first_bar

        self._patch([Tail("tail")])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        self._patch([Empty("empty")])
        cfg = dict(self.cfg, ttl_seconds={"default": 0, "1d": 0})
        src.load_data("stock", "FAKE", period="max", interval="1d", store=self.store, cfg=cfg)
        self.assertIsNotNone(self.store.meta_get("head|stock|FAKE|1d"))

    def test_adjust_prices_toggle_invalidates_store(self):
        prov = FakeProvider("ok")
        self._patch([prov])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store,
                      cfg=dict(self.cfg, adjust_prices=False))
        self.assertEqual(self.store.meta_get("prices_adjusted"), False)
        self.assertGreater(self.store.count("stock", "FAKE", "1d"), 0)
        n1 = len(prov.calls)
        # passage en ajusté : le store est vidé puis re-téléchargé
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store,
                      cfg=dict(self.cfg, adjust_prices=True))
        self.assertEqual(self.store.meta_get("prices_adjusted"), True)
        self.assertGreater(len(prov.calls), n1)
        # appel suivant en ajusté : servi par le store (TTL long), pas de re-fetch
        n2 = len(prov.calls)
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store,
                      cfg=dict(self.cfg, adjust_prices=True))
        self.assertEqual(len(prov.calls), n2)

    def test_legacy_store_sans_meta_consideree_brute(self):
        prov = FakeProvider("ok")
        self._patch([prov])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store,
                      cfg=dict(self.cfg, adjust_prices=False))
        self.store.meta_set("prices_adjusted", None)  # simule une vieille base sans méta
        n1 = len(prov.calls)
        # mode ajusté demandé : la base legacy (brute) est invalidée et re-téléchargée
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store,
                      cfg=dict(self.cfg, adjust_prices=True))
        self.assertGreater(len(prov.calls), n1)
        self.assertEqual(self.store.meta_get("prices_adjusted"), True)

    def test_bad_interval_raises(self):
        self._patch([FakeProvider()])
        with self.assertRaises(DataError):
            src.load_data("forex", "EURUSD", period="1y", interval="1h", store=self.store, cfg=self.cfg)

    def test_data_range(self):
        self._patch([FakeProvider()])
        src.load_data("stock", "FAKE", period="1mo", interval="1d", store=self.store, cfg=self.cfg)
        first, last, n = src.data_range("stock", "FAKE", "1d", store=self.store)
        self.assertIsNotNone(first)
        self.assertIsNotNone(last)
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()
