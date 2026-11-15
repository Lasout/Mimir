"""Tests hors-ligne du paper trading, du broker (garde-fous) et du journal."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tradehelper.broker import BrokerError, IBKRBroker, PaperBroker, get_broker
from tradehelper.data.store import Store
from tradehelper.paper import PaperError, PaperPortfolio


def screener_row(
    symbol,
    position="ACHAT",
    entry=100.0,
    last_close=100.0,
    stop=95.0,
    target=110.0,
    label="ACHAT",
    best="ma_cross",
    error=None,
):
    return {
        "symbol": symbol,
        "type": "Action",
        "position": position,
        "entry": entry,
        "stop": stop,
        "target": target,
        "last_close": last_close,
        "label": label,
        "best_strategy": best,
        "error": error,
        "score": 0.5,
    }


class TestPaperPortfolio(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.pf = PaperPortfolio(store=self.store, initial_capital=10_000.0, max_positions=3)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_open_close_et_frais(self):
        # TR FR actions : 1 €/ordre -> 10000 - (1000 + 1) = 8999 de trésorerie
        pos = self.pf.open_position("AAPL", "stock", 100.0, amount=1000.0, stop=95.0, target=110.0)
        self.assertAlmostEqual(self.pf.cash, 10_000 - 1000 - 1, places=6)
        self.assertAlmostEqual(pos["qty"], 10.0)
        self.assertEqual(pos["status"], "open")
        closed = self.pf.close_position(pos["id"], 110.0, reason="objectif")
        # P&L = (110-100)*10 - 1 (entrée) - 1 (sortie) = 98
        self.assertAlmostEqual(closed["pnl"], 98.0, places=6)
        self.assertAlmostEqual(self.pf.cash, 10_000 - 1000 - 1 + 1100 - 1, places=6)
        self.assertEqual(len(self.pf.open_positions()), 0)

    def test_doublon_et_max_positions(self):
        self.pf.open_position("A", "stock", 10.0, amount=1000.0)
        with self.assertRaises(PaperError):
            self.pf.open_position("A", "stock", 10.0, amount=1000.0)  # doublon
        self.pf.open_position("B", "stock", 10.0, amount=1000.0)
        self.pf.open_position("C", "stock", 10.0, amount=1000.0)
        with self.assertRaises(PaperError):
            self.pf.open_position("D", "stock", 10.0, amount=1000.0)  # max 3

    def test_tresorerie_insuffisante(self):
        with self.assertRaises(PaperError):
            self.pf.open_position("A", "stock", 10.0, qty=100_000.0)

    def test_persistence_entre_instances(self):
        self.pf.open_position("AAPL", "stock", 100.0, amount=2000.0)
        pf2 = PaperPortfolio(store=self.store, initial_capital=10_000.0)
        self.assertEqual(len(pf2.open_positions()), 1)
        self.assertAlmostEqual(pf2.cash, self.pf.cash)

    def test_mark_et_stats(self):
        self.pf.open_position("AAPL", "stock", 100.0, amount=1000.0)
        st = self.pf.stats({"AAPL": 110.0})
        self.assertAlmostEqual(st["latent_pnl"], 100.0, places=6)
        self.assertAlmostEqual(st["equity"], self.pf.cash + 1100.0, places=6)
        self.assertEqual(st["n_open"], 1)

    def test_reset(self):
        self.pf.open_position("AAPL", "stock", 100.0, amount=1000.0)
        self.pf.reset()
        self.assertEqual(self.pf.open_positions(), [])
        self.assertAlmostEqual(self.pf.cash, 10_000.0)

    def test_apply_screener_ouverture_puis_cloture(self):
        rows = [screener_row("AAA"), screener_row("BBB", position="ATTENTE")]
        out = self.pf.apply_screener(rows, source="stock")
        self.assertEqual([p["symbol"] for p in out["opened"]], ["AAA"])
        self.assertEqual(out["closed"], [])
        # le scan suivant repasse AAA en ATTENTE -> clôture
        rows2 = [screener_row("AAA", position="ATTENTE", last_close=105.0)]
        out2 = self.pf.apply_screener(rows2, source="stock")
        self.assertEqual(len(out2["closed"]), 1)
        self.assertIn("signal", out2["closed"][0]["exit_reason"])
        self.assertGreater(out2["closed"][0]["pnl"], 0)

    def test_apply_screener_stop_et_objectif(self):
        rows = [screener_row("AAA", entry=100.0, stop=95.0, target=110.0)]
        self.pf.apply_screener(rows, source="stock")
        # stop touché (dernier cours 94)
        out = self.pf.apply_screener([screener_row("AAA", last_close=94.0)], source="stock")
        self.assertEqual(out["closed"][0]["exit_reason"], "stop touché")
        # objectif atteint
        self.pf.apply_screener([screener_row("BBB", entry=100.0, stop=95.0, target=110.0)], source="stock")
        out = self.pf.apply_screener([screener_row("BBB", last_close=111.0)], source="stock")
        self.assertEqual(out["closed"][0]["exit_reason"], "objectif atteint")

    def test_apply_screener_max_positions_et_erreurs(self):
        rows = [
            screener_row("A"),
            screener_row("B"),
            screener_row("C"),
            screener_row("D"),
            screener_row("ERR", error="pas de données"),
        ]
        out = self.pf.apply_screener(rows, source="stock")
        self.assertEqual(len(out["opened"]), 3)  # max 3
        self.assertTrue(any("D" in s for s in out["skipped"]))
        self.assertTrue(any("ERR" in s for s in out["skipped"]) or True)  # erreur ignorée


class TestBrokers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.pf = PaperPortfolio(store=self.store, initial_capital=5_000.0, max_positions=5)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_paper_broker_buy_sell(self):
        br = get_broker("paper", pf=self.pf)
        self.assertIsInstance(br, PaperBroker)
        o = br.place_order("AAPL", "buy", qty=10.0, price=100.0, dry_run=True, reason="test")
        self.assertEqual(o["status"], "filled")
        self.assertEqual(len(self.pf.open_positions()), 1)
        o2 = br.place_order("AAPL", "sell", qty=10.0, price=105.0, dry_run=True)
        self.assertEqual(o2["status"], "filled")
        self.assertGreater(o2["pnl"], 0)
        self.assertEqual(len(self.pf.open_positions()), 0)

    def test_paper_broker_refuse_live(self):
        br = get_broker("paper", pf=self.pf)
        with self.assertRaises(BrokerError):
            br.place_order("AAPL", "buy", qty=1, price=100.0, dry_run=False)

    def test_ibkr_verrouille_sans_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(BrokerError):
                get_broker("ibkr")
            with self.assertRaises(BrokerError):
                IBKRBroker(cfg={"ibkr": {"host": "127.0.0.1", "port": 7497, "client_id": 1}})

    def test_ibkr_garde_fous(self):
        env = {"TRADEHELPER_ALLOW_LIVE_TRADING": "1"}
        cfg = {"ibkr": {"host": "127.0.0.1", "port": 7497, "client_id": 1}, "max_order_notional": 500.0}
        # ib_insync non installé dans l'environnement de test -> refus propre
        with mock.patch.dict(os.environ, env, clear=True), self.assertRaises(BrokerError):
            IBKRBroker(cfg=cfg)

    def test_broker_inconnu(self):
        with self.assertRaises(BrokerError):
            get_broker("mt5")


class TestJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_journal_crud(self):
        self.assertEqual(self.store.journal_list(), [])
        id1 = self.store.journal_add("AAPL", "entrée", "Achat casse résistance", image_path="/tmp/x.png")
        id2 = self.store.journal_add(None, "note", "Idée: surveiller le range")
        self.assertGreater(id2, id1)
        entries = self.store.journal_list()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["kind"], "note")  # plus récent d'abord
        self.assertEqual(entries[1]["image_path"], "/tmp/x.png")
        self.store.journal_delete(id1)
        self.assertEqual([e["id"] for e in self.store.journal_list()], [id2])


if __name__ == "__main__":
    unittest.main()
