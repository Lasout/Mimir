"""Tests hors-ligne du fondamental avancé (analysis/advanced.py) : fetchers injectés."""

import sys
import tempfile
import types
import unittest
from pathlib import Path

import pandas as pd

from tradehelper.analysis import advanced
from tradehelper.analysis.advanced import (advanced_fundamental, eps_surprise,
                                            news_sentiment_score, parse_rss_titles,
                                            sector_relative)
from tradehelper.data.store import Store

RSS_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Yahoo Finance</title>
<item><title>Apple beats estimates, stock surges to record</title></item>
<item><title>Apple warns on weak iPhone sales, downgrade</title></item>
<item><title>Apple announces dividend increase</title></item>
</channel></rss>"""


class TestNews(unittest.TestCase):
    def test_parse_rss_titles(self):
        titles = parse_rss_titles(RSS_SAMPLE)
        self.assertEqual(len(titles), 3)
        self.assertTrue(titles[0].startswith("Apple beats"))

    def test_parse_rss_invalide(self):
        self.assertEqual(parse_rss_titles("pas du xml <<<"), [])

    def test_sentiment_bull_bear(self):
        bull = news_sentiment_score(["Apple beats estimates and surges to a record", "Upgrade to buy"])
        self.assertGreater(bull["score"], 0.5)
        self.assertEqual(bull["pos"], 2)
        bear = news_sentiment_score(["Stock plunges after profit warning", "Downgrade to sell"])
        self.assertLess(bear["score"], -0.5)
        self.assertEqual(bear["neg"], 2)
        neut = news_sentiment_score(["Apple holds annual shareholder meeting"])
        self.assertEqual(neut["score"], 0.0)
        self.assertEqual(news_sentiment_score([])["n"], 0)


class TestEpsSurprise(unittest.TestCase):
    def test_surprise_positive(self):
        df = pd.DataFrame({"Reported EPS": [1.10, 1.20], "EPS Estimate": [1.00, 1.00]})
        out = eps_surprise(df)
        self.assertTrue(out["available"])
        self.assertAlmostEqual(out["avg_surprise_pct"], 15.0)
        self.assertGreater(out["score"], 0.5)

    def test_surprise_negative(self):
        df = pd.DataFrame({"Reported EPS": [0.80], "EPS Estimate": [1.00]})
        out = eps_surprise(df)
        self.assertLess(out["score"], -0.5)
        self.assertAlmostEqual(out["avg_surprise_pct"], -20.0)

    def test_sans_donnees(self):
        self.assertFalse(eps_surprise(None)["available"])
        self.assertFalse(eps_surprise(pd.DataFrame({"Reported EPS": [None], "EPS Estimate": [None]}))["available"])


class TestSectorRelative(unittest.TestCase):
    def test_sous_valorie_vs_secteur(self):
        info = {"trailingPE": 15.0, "profitMargins": 0.30, "sector": "Technology"}
        peers = [{"trailingPE": 30.0, "profitMargins": 0.10, "sector": "Technology"},
                 {"trailingPE": 26.0, "profitMargins": 0.20, "sector": "Technology"}]
        out = sector_relative(info, peers)
        self.assertTrue(out["available"])
        self.assertAlmostEqual(out["sector_median_pe"], 28.0)
        self.assertGreater(out["score"], 0)  # moins cher que le secteur + meilleure marge
        self.assertEqual(out["n_peers"], 2)

    def test_sur_valorie(self):
        info = {"trailingPE": 60.0, "profitMargins": 0.05, "sector": "Technology"}
        peers = [{"trailingPE": 25.0, "profitMargins": 0.25, "sector": "Technology"}]
        out = sector_relative(info, peers)
        self.assertLess(out["score"], 0)

    def test_sans_pe(self):
        self.assertFalse(sector_relative({"trailingPE": None}, [])["available"])


class TestAdvancedFundamental(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _fetchers(self, compteur):
        return {
            "news": lambda sym: (compteur.__setitem__("news", compteur.get("news", 0) + 1),
                                 ["ACME beats estimates and surges", "ACME record profit"])[1],
            "eps": lambda sym: (compteur.__setitem__("eps", compteur.get("eps", 0) + 1),
                                pd.DataFrame({"Reported EPS": [1.2], "EPS Estimate": [1.0]}))[1],
            "info": lambda sym: {"trailingPE": 15.0, "profitMargins": 0.3, "sector": "Tech"},
            "peers_info": lambda peer: {"trailingPE": 30.0, "profitMargins": 0.1, "sector": "Tech"},
            "peers": ["PEER1", "PEER2"],
        }

    def test_complet_avec_fetchers(self):
        compteur = {}
        out = advanced_fundamental("ACME", source="stock", store=self.store,
                                   fetchers=self._fetchers(compteur))
        self.assertTrue(out["available"])
        self.assertEqual(len(out["components"]), 3)
        self.assertTrue(-1.0 <= out["score"] <= 1.0)
        self.assertTrue(any("Sentiment" in r for r in out["reasons"]))
        self.assertTrue(any("BPA" in r for r in out["reasons"]))
        self.assertTrue(any("secteur" in r for r in out["reasons"]))
        self.assertIn("news", out["data"])
        # 2e appel : servi par le cache, pas de nouvel appel réseau
        out2 = advanced_fundamental("ACME", source="stock", store=self.store,
                                    fetchers=self._fetchers(compteur))
        self.assertEqual(out2["score"], out["score"])
        self.assertEqual(compteur.get("news", 0), 1)
        self.assertEqual(compteur.get("eps", 0), 1)

    def test_non_stock_indisponible(self):
        out = advanced_fundamental("BTCUSDT", source="crypto", store=self.store)
        self.assertFalse(out["available"])

    def test_echec_reseau_non_bloquant(self):
        def boom(sym):
            raise RuntimeError("offline")
        out = advanced_fundamental("ACME", source="stock", store=self.store,
                                   fetchers={"news": boom, "eps": boom,
                                             "info": lambda s: (_ for _ in ()).throw(RuntimeError("x")),
                                             "peers": []})
        self.assertFalse(out["available"])  # aucun composant -> pas dispo, pas d'exception


class TestIntegrationFondamental(unittest.TestCase):
    """fundamental_analysis intègre les composantes avancées (yfinance mocké, hors-ligne)."""

    def test_integration_composantes_avancees(self):
        fake_yf = types.ModuleType("yfinance")

        class FakeTicker:
            def __init__(self, sym):
                self.info = {"trailingPE": 20.0, "profitMargins": 0.2, "revenueGrowth": 0.1,
                             "sector": "Tech", "currentPrice": 100.0}

        fake_yf.Ticker = FakeTicker
        old_yf = sys.modules.get("yfinance")
        sys.modules["yfinance"] = fake_yf
        old_adv = advanced.advanced_fundamental
        calls = []

        def fake_adv(symbol, source="stock", **kw):
            calls.append(symbol)
            return {"available": True, "score": 0.8,
                    "reasons": ["Sentiment news positif (+0.80)."],
                    "components": [(0.10, 0.8)], "data": {"news": {"score": 0.8}}}

        advanced.advanced_fundamental = fake_adv
        try:
            from tradehelper.analysis.fundamental import fundamental_analysis
            out = fundamental_analysis("ACME", source="stock")
        finally:
            advanced.advanced_fundamental = old_adv
            if old_yf is not None:
                sys.modules["yfinance"] = old_yf
            else:
                del sys.modules["yfinance"]
        self.assertTrue(out["available"])
        self.assertEqual(calls, ["ACME"])
        self.assertTrue(any("[Avancé] Sentiment" in r for r in out["reasons"]))
        self.assertIn("news", out["data"])


if __name__ == "__main__":
    unittest.main()
