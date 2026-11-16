"""Tests hors-ligne du moteur paper conforme (SPEC §3.5, PAP-01…PAP-08)."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tradehelper.data.store import Store
from tradehelper.paper_engine import (
    DEFAULT_SLIPPAGE_PCT,
    MIN_FEE_RT_PCT,
    Bar,
    CostModel,
    PaperEngine,
    PaperEngineError,
    make_trade_id,
)
from tradehelper.strategies import strategy_version


def clock_seq(start="2026-01-10 21:00:00"):
    """Horloge déterministe incrémentée à chaque appel (tests d'horodatage réel PAP-05)."""
    state = {"t": pd.Timestamp(start)}

    def clock():
        state["t"] += pd.Timedelta(minutes=1)
        return state["t"]

    return clock


class TestCostModel(unittest.TestCase):
    def test_fee_rt_floor_01pct(self):
        # PAP-06 : plancher non négociable de 0,1 % aller-retour
        c = CostModel(fee_pct_rt=0.0)
        self.assertEqual(c.fee_pct_rt, MIN_FEE_RT_PCT)
        self.assertAlmostEqual(c.fee_side(10_000), 10_000 * 0.001 / 2)

    def test_slippage_against_position(self):
        c = CostModel(slippage_pct=0.001)
        # long : on achète plus cher, on vend moins cher
        self.assertAlmostEqual(c.apply_entry(100.0, "long"), 100.1)
        self.assertAlmostEqual(c.apply_exit(100.0, "long"), 99.9)
        # short : inverse
        self.assertAlmostEqual(c.apply_entry(100.0, "short"), 99.9)
        self.assertAlmostEqual(c.apply_exit(100.0, "short"), 100.1)

    def test_slippage_vol_mode(self):
        c = CostModel(slippage_mode="vol", vol_mult=0.5)
        self.assertAlmostEqual(c.slippage_rate(0.04), 0.02)
        self.assertAlmostEqual(c.slippage_rate(None), DEFAULT_SLIPPAGE_PCT)


class TestRecordSignal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.eng = PaperEngine(store=self.store, initial_capital=100_000, risk_pct=0.01, clock=clock_seq())
        self.v = strategy_version("ma_cross", {"fast": 10, "slow": 30})
        self.eng.start_test("test-1", self.v)  # GOU-02 : test démarré avant tout signal

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_idempotent_trade_id(self):
        # PAP-05 : rejouer le même jour ne crée pas de doublon
        v = strategy_version("ma_cross", {"fast": 10, "slow": 30})
        t1 = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=v,
        )
        t2 = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=v,
        )
        self.assertEqual(t1["trade_id"], t2["trade_id"])
        self.assertEqual(make_trade_id(v, "AAPL", "2026-01-10"), t1["trade_id"])
        self.assertEqual(len(self.store.ptrade_list()), 1)
        self.assertEqual(t1["status"], "pending")
        self.assertAlmostEqual(t1["r_planned"], (110 - 100) / (100 - 95))  # PAP-04

    def test_new_version_locked_to_test(self):
        # AUD-04/GOU-01 : changer un paramètre -> nouvelle version, mais le test en cours
        # est verrouillé sur sa version : il faut start_test() (compteur remis à zéro)
        v1 = strategy_version("ma_cross", {"fast": 10})
        v2 = strategy_version("ma_cross", {"fast": 11})
        self.assertNotEqual(v1, v2)
        self.eng.start_test("t-v1", v1)
        t1 = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=v1,
        )
        with self.assertRaises(PaperEngineError):
            self.eng.record_signal(
                signal_date="2026-01-10",
                asset="AAPL",
                source="stock",
                direction="long",
                planned_entry=100.0,
                stop=95.0,
                target=110.0,
                strategy_version=v2,
            )
        self.assertEqual(t1["trade_id"], make_trade_id(v1, "AAPL", "2026-01-10"))

    def test_real_clock_timestamps(self):
        # PAP-05 : horodatage à l'horloge réelle (injectée ici), jamais manuel
        t = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )
        self.assertTrue(t["created_at"].startswith("2026-01-10 21:0"))

    def test_stop_incoherent_rejette(self):
        with self.assertRaises(PaperEngineError):
            self.eng.record_signal(
                signal_date="2026-01-10",
                asset="AAPL",
                source="stock",
                direction="long",
                planned_entry=100.0,
                stop=105.0,
                target=110.0,
                strategy_version="v",
            )


class TestFillAndFollow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.eng = PaperEngine(
            store=self.store,
            initial_capital=100_000,
            risk_pct=0.01,
            cost=CostModel(fee_pct_rt=0.001, slippage_pct=0.0005),
            clock=clock_seq(),
        )
        self.v = strategy_version("ma_cross", {"fast": 10, "slow": 30})
        self.eng.start_test("test-1", self.v)
        self.t = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_fill_at_next_open_not_signal_price(self):
        # PAP-01 : fill au open de J+1 + slippage, jamais au prix du signal (100.0)
        bar_j1 = Bar(date="2026-01-11", open=102.0, high=103.0, low=101.0, close=102.5)
        out = self.eng.fill_pending("AAPL", "stock", bar_j1)
        self.assertEqual(out["status"], "open")
        self.assertEqual(out["fill_date"], "2026-01-11")
        self.assertAlmostEqual(out["fill_price"], 102.0 * (1 + 0.0005))  # slippage contre
        self.assertNotEqual(out["fill_price"], 100.0)  # jamais le prix du signal
        # sizing : risk 1 % * 100 000 / (100-95) = 1000 / 5 = 200 -> qty=200
        self.assertAlmostEqual(out["qty"], 200.0)
        # frais d'entrée = 0,05 % du notionnel (moitié du AR 0,1 %)
        self.assertAlmostEqual(out["fees"], 200 * out["fill_price"] * 0.0005)
        # idempotent : rejouer le même jour ne double-remplit pas (PAP-05)
        cash_before = self.eng.cash
        out2 = self.eng.fill_pending("AAPL", "stock", bar_j1)
        self.assertIsNone(out2)  # plus rien en attente
        self.assertEqual(len(self.store.ptrade_list(status="open")), 1)
        self.assertAlmostEqual(self.eng.cash, cash_before)

    def test_fill_same_day_forbidden(self):
        # PAP-01 : remplir le jour du signal est invalide par construction
        bar_j = Bar(date="2026-01-10", open=100.5, high=101.0, low=100.0, close=100.8)
        with self.assertRaises(PaperEngineError):
            self.eng.fill_pending("AAPL", "stock", bar_j)

    def test_stop_priority_same_day(self):
        # PAP-03 : stop et cible touchés le même jour -> stop prioritaire
        self.eng.fill_pending("AAPL", "stock", Bar(date="2026-01-11", open=102.0, high=103.0, low=101.0, close=102.5))
        bar = Bar(date="2026-01-12", open=104.0, high=111.0, low=94.0, close=95.0)
        closed = self.eng.on_bar("AAPL", "stock", bar)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["exit_reason"], "stop")
        self.assertAlmostEqual(closed[0]["exit_price"], 95.0 * (1 - 0.0005))

    def test_target_exit_and_r_realized_net(self):
        # PAP-04 : r_realized net de frais et slippage
        self.eng.fill_pending("AAPL", "stock", Bar(date="2026-01-11", open=100.0, high=101.0, low=99.5, close=100.5))
        t = self.store.ptrade_list(status="open")[0]
        entry = t["fill_price"]
        qty = t["qty"]
        bar = Bar(date="2026-01-12", open=105.0, high=111.0, low=104.0, close=110.5)
        closed = self.eng.on_bar("AAPL", "stock", bar)
        self.assertEqual(closed[0]["exit_reason"], "target")
        exit_price = closed[0]["exit_price"]  # 110 * (1 - 0.0005)
        self.assertAlmostEqual(exit_price, 110.0 * (1 - 0.0005))
        fee_rt = 0.001  # AR
        expected_pnl = (exit_price - entry) * qty - qty * entry * fee_rt / 2 - qty * exit_price * fee_rt / 2
        self.assertAlmostEqual(closed[0]["pnl"], expected_pnl, places=6)
        risk_money = (100.0 - 95.0) * qty
        self.assertAlmostEqual(closed[0]["r_realized"], expected_pnl / risk_money, places=9)
        self.assertGreater(closed[0]["r_realized"], closed[0]["r_planned"] - 1.0)  # proche du R prévu, net

    def test_no_follow_on_fill_day(self):
        self.eng.fill_pending("AAPL", "stock", Bar(date="2026-01-11", open=100.0, high=120.0, low=80.0, close=100.5))
        # le jour du fill, pas de clôture même si stop/cible touchés
        closed = self.eng.on_bar("AAPL", "stock", Bar(date="2026-01-11", open=100.0, high=120.0, low=80.0, close=100.5))
        self.assertEqual(closed, [])

    def test_short_direction(self):
        self.eng.record_signal(
            signal_date="2026-01-10",
            asset="MSFT",
            source="stock",
            direction="short",
            planned_entry=100.0,
            stop=105.0,
            target=90.0,
            strategy_version=self.v,
        )
        out = self.eng.fill_pending("MSFT", "stock", Bar(date="2026-01-11", open=99.0, high=99.5, low=98.0, close=98.5))
        self.assertAlmostEqual(out["fill_price"], 99.0 * (1 - 0.0005))  # slippage contre (short)
        self.assertAlmostEqual(out["r_planned"], (100 - 90) / (105 - 100))
        # stop short : high >= 105 -> sortie au stop (slippage contre : plus cher)
        closed = self.eng.on_bar(
            "MSFT", "stock", Bar(date="2026-01-12", open=104.0, high=106.0, low=103.0, close=105.5)
        )
        self.assertEqual(closed[0]["exit_reason"], "stop")
        self.assertAlmostEqual(closed[0]["exit_price"], 105.0 * (1 + 0.0005))


class TestDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        # quelques barres pour le benchmark buy-and-hold
        idx = pd.bdate_range("2026-01-05", periods=20)
        n = len(idx)
        close = [100 * (1.01**i) for i in range(n)]  # +1 %/jour : B&H gagnant
        df = pd.DataFrame(
            {
                "Open": close,
                "High": [c * 1.01 for c in close],
                "Low": [c * 0.99 for c in close],
                "Close": close,
                "Volume": [1e6] * n,
            },
            index=idx,
        )
        self.store.upsert(df, "stock", "AAPL", "1d")
        self.eng = PaperEngine(
            store=self.store, initial_capital=100_000, risk_pct=0.01, cost=CostModel(), clock=clock_seq()
        )
        self.v = strategy_version("ma_cross", {"fast": 10})
        self.eng.start_test("test-1", self.v)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _trade(self, asset, signal_date, fill_date, exit_date, bar_kwargs):
        self.eng.record_signal(
            signal_date=signal_date,
            asset=asset,
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )
        self.eng.fill_pending(asset, "stock", Bar(date=fill_date, open=100.0, high=101.0, low=99.0, close=100.5))
        return self.eng.on_bar(asset, "stock", Bar(date=exit_date, **bar_kwargs))

    def test_dashboard_closed_only_and_metrics(self):
        # PAP-08 : métriques UNIQUEMENT sur trades clôturés
        # trade gagnant (target) et perdant (stop)
        self._trade(
            "AAPL", "2026-01-10", "2026-01-11", "2026-01-12", dict(open=105.0, high=111.0, low=104.0, close=110.5)
        )
        self._trade("AAPL", "2026-01-12", "2026-01-13", "2026-01-14", dict(open=99.0, high=100.0, low=94.0, close=95.0))
        # un trade encore ouvert ne doit pas compter
        self.eng.record_signal(
            signal_date="2026-01-14",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )
        self.eng.fill_pending("AAPL", "stock", Bar(date="2026-01-15", open=100.0, high=101.0, low=99.0, close=100.2))
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 2)
        self.assertEqual(d["n_open"], 1)
        rs = [t["r_realized"] for t in self.store.ptrade_list(status="closed")]
        self.assertAlmostEqual(d["expectancy_r"], sum(rs) / 2)
        self.assertAlmostEqual(d["win_rate"], 0.5)
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        self.assertAlmostEqual(d["payoff"], (sum(wins) / len(wins)) / (-sum(losses) / len(losses)))
        self.assertAlmostEqual(d["breakeven_win_rate"], 1.0 / (1.0 + d["payoff"]))
        self.assertLess(d["max_drawdown_r"], 0)  # il y a eu une perte
        self.assertIsNotNone(d["expectancy_ic95"])
        # benchmark buy-and-hold lu dans le store sur la fenêtre des trades
        bh = self.eng.benchmark_buy_hold()
        self.assertIsNotNone(bh)
        self.assertEqual(bh["window"], ["2026-01-11", "2026-01-14"])
        self.assertGreater(bh["mean_return"], 0)  # AAPL monte de 1 %/jour dans le fixture

    def test_dashboard_empty(self):
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 0)
        self.assertIsNone(d["expectancy_r"])
        self.assertIsNone(d["expectancy_ic95"])
        self.assertIsNone(d["max_drawdown_r"])

    def _many_trades(self, n: int, pattern: str = "all_win"):
        dates = pd.bdate_range("2026-03-02", periods=3 * n + 2)
        for i in range(n):
            # gain +2R (target 110 depuis fill 100, risque 5) ou perte -1R (stop 95)
            is_win = i % 3 == 0 if pattern == "third_win" else True
            bar = (
                dict(open=105.0, high=111.0, low=104.0, close=110.5)
                if is_win
                else dict(open=99.0, high=100.0, low=94.0, close=95.0)
            )
            self._trade(
                "AAPL", str(dates[3 * i].date()), str(dates[3 * i + 1].date()), str(dates[3 * i + 2].date()), bar
            )

    def test_aud06_insufficient_sample(self):
        # AUD-06 : moins de 100 trades clôturés -> aucune conclusion statistique
        self._trade(
            "AAPL", "2026-01-10", "2026-01-11", "2026-01-12", dict(open=105.0, high=111.0, low=104.0, close=110.5)
        )
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 1)
        self.assertFalse(d["min_trades_ok"])
        self.assertFalse(d["significance_ok"])
        self.assertIn("insuffisant", d["conclusion"])

    def test_aud06_significant_when_ic_excludes_zero(self):
        self._many_trades(100)  # 100 trades identiques gagnants : IC = 0
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 100)
        self.assertTrue(d["min_trades_ok"])
        self.assertTrue(d["significance_ok"])
        self.assertIn("significativement", d["conclusion"])

    def test_aud06_not_significant_when_ic_contains_zero(self):
        # 1/3 de gagnants (+2R) / 2/3 de perdants (-1R) : expectancy ~ 0, IC > 0
        self._many_trades(100, pattern="third_win")
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 100)
        self.assertTrue(d["min_trades_ok"])
        self.assertLess(abs(d["expectancy_r"]), 0.1)  # ~0 (frais inclus)
        self.assertFalse(d["significance_ok"])
        self.assertIn("on ne sait rien", d["conclusion"])

    def test_reset(self):
        self._trade(
            "AAPL", "2026-01-10", "2026-01-11", "2026-01-12", dict(open=105.0, high=111.0, low=104.0, close=110.5)
        )
        self.eng.reset()
        self.assertEqual(self.store.ptrade_list(), [])
        self.assertAlmostEqual(self.eng.cash, 100_000)


class TestGovernance(unittest.TestCase):
    """SPEC §3.7 (GOU-01…04) : version figée, test figé, pas d'annulation, carnet d'idées,
    revue tous les 100 trades."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.eng = PaperEngine(store=self.store, initial_capital=100_000, risk_pct=0.01, clock=clock_seq())
        self.v = strategy_version("ma_cross", {"fast": 10})

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _closed_trade(self, tid, test_id, r, pnl, exit_date="2026-01-15"):
        self.store.ptrade_add(
            {
                "trade_id": tid,
                "strategy_version": self.v,
                "signal_date": "2026-01-10",
                "asset": "AAPL",
                "source": "stock",
                "direction": "long",
                "planned_entry": 100.0,
                "stop": 95.0,
                "target": 110.0,
                "risk_pct": 0.01,
                "r_planned": 2.0,
                "status": "closed",
                "fill_price": 100.5,
                "fill_date": "2026-01-11",
                "exit_date": exit_date,
                "exit_price": 105.0,
                "exit_reason": "target",
                "qty": 200.0,
                "fees": 10.0,
                "slippage_cost": 1.0,
                "pnl": pnl,
                "r_realized": r,
                "created_at": "2026-01-10 21:00:00",
                "test_id": test_id,
            }
        )

    def test_no_test_no_signal(self):
        # GOU-02 : aucun signal sans test démarré (config figée)
        with self.assertRaises(PaperEngineError):
            self.eng.record_signal(
                signal_date="2026-01-10",
                asset="AAPL",
                source="stock",
                direction="long",
                planned_entry=100.0,
                stop=95.0,
                target=110.0,
                strategy_version=self.v,
            )

    def test_start_test_freezes_config_and_resets(self):
        # GOU-02/AUD-04 : start_test fige la config en meta et remet le compteur à zéro
        cfg1 = self.eng.start_test("test-A", self.v, risk_pct=0.02, initial_capital=50_000)
        self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )
        self.assertEqual(len(self.store.ptrade_list()), 1)
        v2 = strategy_version("ma_cross", {"fast": 11})
        cfg2 = self.eng.start_test("test-B", v2)  # nouvelle version = nouveau test
        self.assertNotEqual(cfg1["test_id"], cfg2["test_id"])
        self.assertEqual(self.store.ptrade_list(), [])  # compteur remis à zéro
        self.assertAlmostEqual(self.eng.cash, 100_000)  # capital du nouveau test (défaut)
        self.assertEqual(self.eng.active_test()["strategy_version"], v2)
        # les signaux du nouveau test portent le nouveau test_id
        t = self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=v2,
        )
        self.assertEqual(t["test_id"], cfg2["test_id"])

    def test_cancel_signal_forbidden(self):
        # GOU-03 : aucune annulation rétrospective
        self.eng.start_test("t", self.v)
        self.eng.record_signal(
            signal_date="2026-01-10",
            asset="AAPL",
            source="stock",
            direction="long",
            planned_entry=100.0,
            stop=95.0,
            target=110.0,
            strategy_version=self.v,
        )
        with self.assertRaises(PaperEngineError):
            self.eng.cancel_signal("whatever")

    def test_dashboard_scoped_to_current_test(self):
        # GOU-02 : le dashboard ne compte que les trades du test en cours
        cfg = self.eng.start_test("t", self.v)
        self._closed_trade("old1", "autre-test", 1.0, 500.0)
        self._closed_trade("new1", cfg["test_id"], 0.5, 250.0)
        self._closed_trade("new2", cfg["test_id"], -0.5, -250.0)
        d = self.eng.dashboard()
        self.assertEqual(d["n_closed"], 2)  # pas l'ancien test
        self.assertAlmostEqual(d["expectancy_r"], 0.0)
        self.assertEqual(d["test"]["test_id"], cfg["test_id"])

    def test_review_every_100_trades(self):
        # GOU-04 : revue logguée à chaque palier de 100 trades clôturés
        cfg = self.eng.start_test("t", self.v)
        for i in range(100):
            self._closed_trade(f"t{i}", cfg["test_id"], 0.2, 40.0, exit_date=f"2026-01-{(i % 27) + 1:02d}")
        self.assertTrue(self.eng.check_review())  # palier 100 franchi -> revue logguée
        self.assertFalse(self.eng.check_review())  # pas de double revue
        runs = [r for r in self.store.list_runs(50) if r["kind"] == "paper_review"]
        self.assertEqual(len(runs), 1)
        # avant 100 trades : pas de revue
        self.eng.start_test("t2", self.v)
        self.assertFalse(self.eng.check_review())

    def test_ideas_notebook_separate(self):
        # GOU-01 : carnet d'idées séparé de la version en cours
        self.eng.start_test("t", self.v)
        self.eng.add_idea("tester un stop à 1,5x ATR (PAS appliqué au test en cours)")
        self.eng.add_idea("filtrer les actifs peu volatils", strategy_version="rsi_reversion@aaaaaaaa")
        ideas = self.eng.list_ideas()
        self.assertEqual(len(ideas), 2)
        self.assertTrue(all(e["kind"] == "idée" for e in ideas))
        d = self.eng.dashboard()
        self.assertEqual(d["n_ideas"], 2)


if __name__ == "__main__":
    unittest.main()
