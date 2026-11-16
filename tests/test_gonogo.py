"""Tests hors-ligne de la grille go/no-go capital réel — SPEC §5 (GNG-xx, P6.4)."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from tradehelper.broker import BrokerError, IBKRBroker, check_gonogo
from tradehelper.data.store import Store
from tradehelper.gonogo import evaluate_gonogo
from tradehelper.paper_engine import Bar, CostModel, PaperEngine
from tradehelper.strategies import strategy_version


def clock_seq(start="2026-03-02 21:00:00"):
    state = {"t": pd.Timestamp(start)}

    def clock():
        state["t"] += pd.Timedelta(minutes=1)
        return state["t"]

    return clock


def _ok_kwargs(**over):
    """Kwargs de base remplissant les 6 critères GNG."""
    kw = {
        "n_closed": 120,
        "expectancy_r": 0.30,
        "expectancy_ic95": 0.05,
        "win_rate": 0.55,
        "payoff": 1.8,
        "max_drawdown_pct": -0.12,
        "total_return": 0.40,
        "benchmark_return": 0.10,
        "regime_labels": ["haussier", "baissier"],
    }
    kw.update(over)
    return kw


class TestEvaluateGonogo(unittest.TestCase):
    def test_all_ok_go(self):
        r = evaluate_gonogo(**_ok_kwargs())
        self.assertTrue(r["go"])
        self.assertEqual(r["n_ok"], 6)
        self.assertEqual(r["missing"], [])
        self.assertIn("GO", r["verdict"])

    def test_gng01_boundary_100(self):
        self.assertFalse(evaluate_gonogo(**_ok_kwargs(n_closed=99))["go"])
        self.assertTrue(evaluate_gonogo(**_ok_kwargs(n_closed=100))["go"])

    def test_gng02_expectancy_strictly_above_015(self):
        # 0,15 exactement ne passe pas (critère : > +0,15 R)
        r = evaluate_gonogo(**_ok_kwargs(expectancy_r=0.15, expectancy_ic95=0.01))
        self.assertFalse(r["go"])
        self.assertIn("GNG-02", r["missing"])
        # IC contenant 0 -> no-go même avec une bonne espérance
        r = evaluate_gonogo(**_ok_kwargs(expectancy_r=0.30, expectancy_ic95=0.40))
        self.assertIn("GNG-02", r["missing"])

    def test_gng03_payoff_ou_winrate_mais_expectance_positive(self):
        # payoff >= 1,5 suffisant
        self.assertTrue(evaluate_gonogo(**_ok_kwargs(payoff=1.5, win_rate=0.30))["go"])
        # win rate >= 45 % suffit si payoff < 1,5
        self.assertTrue(evaluate_gonogo(**_ok_kwargs(payoff=1.2, win_rate=0.45))["go"])
        # aucun des deux
        r = evaluate_gonogo(**_ok_kwargs(payoff=1.2, win_rate=0.44))
        self.assertIn("GNG-03", r["missing"])
        # espérance négative -> GNG-03 non rempli même avec win rate élevé
        r = evaluate_gonogo(**_ok_kwargs(expectancy_r=-0.10, expectancy_ic95=0.05, payoff=2.0, win_rate=0.60))
        self.assertIn("GNG-03", r["missing"])

    def test_gng04_drawdown_boundary(self):
        self.assertTrue(evaluate_gonogo(**_ok_kwargs(max_drawdown_pct=-0.25))["go"])
        r = evaluate_gonogo(**_ok_kwargs(max_drawdown_pct=-0.26))
        self.assertIn("GNG-04", r["missing"])
        r = evaluate_gonogo(**_ok_kwargs(max_drawdown_pct=None))
        self.assertIn("GNG-04", r["missing"])

    def test_gng05_benchmark_strictly_better(self):
        r = evaluate_gonogo(**_ok_kwargs(total_return=0.10, benchmark_return=0.10))
        self.assertIn("GNG-05", r["missing"])
        r = evaluate_gonogo(**_ok_kwargs(benchmark_return=None))
        self.assertIn("GNG-05", r["missing"])

    def test_gng06_deux_regimes_requis(self):
        r = evaluate_gonogo(**_ok_kwargs(regime_labels=["haussier"]))
        self.assertIn("GNG-06", r["missing"])
        self.assertTrue(evaluate_gonogo(**_ok_kwargs(regime_labels=["haussier", "plat"]))["go"])

    def test_verdict_nogo_liste_les_manquants(self):
        r = evaluate_gonogo(**_ok_kwargs(n_closed=10, regime_labels=["plat"]))
        self.assertFalse(r["go"])
        self.assertIn("NO-GO", r["verdict"])
        self.assertIn("GNG-01", r["verdict"])
        self.assertIn("GNG-06", r["verdict"])
        self.assertEqual(r["n_total"], 6)


class TestCheckGonogoBroker(unittest.TestCase):
    def test_none_ou_verte_ne_leve_pas(self):
        check_gonogo(None)
        check_gonogo({"go": True, "missing": []})

    def test_rouge_leve_broker_error(self):
        with self.assertRaises(BrokerError) as cm:
            check_gonogo({"go": False, "missing": ["GNG-01", "GNG-06"]})
        self.assertIn("NO-GO", str(cm.exception))
        self.assertIn("GNG-01", str(cm.exception))

    def test_ibkr_refuse_ordre_reel_si_grille_rouge(self):
        # instance sans __init__ (évite env/ib_insync) : on teste la garde GNG de place_order
        b = object.__new__(IBKRBroker)
        b.max_order_notional = 1_000.0
        b._gonogo = lambda: {"go": False, "missing": ["GNG-02"]}
        with self.assertRaises(BrokerError) as cm:
            b.place_order("AAPL", "buy", 1, limit_price=100.0, confirm=True, dry_run=False)
        self.assertIn("go/no-go", str(cm.exception))

    def test_ibkr_dry_run_non_bloque_par_la_grille(self):
        b = object.__new__(IBKRBroker)
        b.max_order_notional = 1_000.0
        b._gonogo = lambda: {"go": False, "missing": ["GNG-01"]}
        out = b.place_order("AAPL", "buy", 1, limit_price=100.0, confirm=True, dry_run=True)
        self.assertEqual(out["status"], "dry_run")


class TestEngineGonogo(unittest.TestCase):
    """`PaperEngine.gonogo()` assemble dashboard + benchmark + régimes depuis le store."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        # 5 actifs haussiers + 5 baissiers -> 2 régimes de marché (GNG-06) ;
        # fenêtre courte (60 j) pour que le B&H reste battable par la stratégie (GNG-05)
        idx = pd.bdate_range("2026-03-02", periods=60)
        n = len(idx)
        self.assets = [f"UP{i}" for i in range(5)] + [f"DN{i}" for i in range(5)]
        for sym in self.assets:
            step = 1.02 if sym.startswith("UP") else 0.98
            closes = [100 * (step**i) for i in range(n)]
            self.store.upsert(
                pd.DataFrame(
                    {
                        "Open": closes,
                        "High": [c * 1.01 for c in closes],
                        "Low": [c * 0.99 for c in closes],
                        "Close": closes,
                        "Volume": [1e6] * n,
                    },
                    index=idx,
                ),
                "stock",
                sym,
                "1d",
            )
        self.eng = PaperEngine(
            store=self.store, initial_capital=100_000, risk_pct=0.01, cost=CostModel(), clock=clock_seq()
        )
        self.v = strategy_version("ma_cross", {"fast": 10})
        self.eng.start_test("test-gonogo", self.v)

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

    def _many_winning_trades(self, n: int):
        # 100 trades répartis sur 10 actifs x 10 blocs de 3 jours (fenêtre ~30 j)
        dates = pd.bdate_range("2026-03-02", periods=3 * (n // len(self.assets)) + 2)
        for i in range(n):
            asset = self.assets[i % len(self.assets)]
            k = i // len(self.assets)
            self._trade(
                asset,
                str(dates[3 * k].date()),
                str(dates[3 * k + 1].date()),
                str(dates[3 * k + 2].date()),
                dict(open=105.0, high=111.0, low=104.0, close=110.5),
            )

    def test_grille_verte_avec_100_trades_gagnants_2_regimes(self):
        self._many_winning_trades(100)
        r = self.eng.gonogo()
        self.assertEqual(r["n_total"], 6)
        self.assertEqual(
            r["missing"], [], f"critères manquants : {[(c['id'], c['detail']) for c in r['criteria'] if not c['ok']]}"
        )
        self.assertTrue(r["go"])
        self.assertIn("GO", r["verdict"])

    def test_grille_rouge_si_peu_de_trades(self):
        self._trade(
            "UP0",
            "2026-03-02",
            "2026-03-03",
            "2026-03-04",
            dict(open=105.0, high=111.0, low=104.0, close=110.5),
        )
        r = self.eng.gonogo()
        self.assertFalse(r["go"])
        self.assertIn("GNG-01", r["missing"])
        self.assertIn("NO-GO", r["verdict"])


if __name__ == "__main__":
    unittest.main()
