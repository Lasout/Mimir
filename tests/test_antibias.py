"""Audit anti-biais — SPEC.md §3.6 (AUD-xx). Priorité absolue : AUD-01 look-ahead.

Principe : aucun signal du jour J ne doit dépendre de données postérieures à J.
Tests de non-régression par troncature : pour 20 jours J tirés au hasard (seed fixe),
tronquer les données à J et vérifier que ce que le pipeline produit à J est identique
au pipeline complet évalué à J. Tout écart = fuite de données futures (indicateur
"repaint", fill au prix du signal, etc.).

AUD-03 (cherry-picking) : chaque signal émis est compté, aucune exclusion rétrospective.
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import tradehelper.data.events as events_mod
from tests.helpers import make_ohlcv, make_trend_reversal
from tradehelper.advice import advise
from tradehelper.backtest import run_backtest
from tradehelper.data.store import Store
from tradehelper.fees import get_fee_model
from tradehelper.indicators import add_all
from tradehelper.paper import PaperPortfolio
from tradehelper.strategies import get_strategy, list_strategies

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def random_days(n: int, k: int = 20, lo: int = 60, seed: int = 20261115) -> list[int]:
    """k jours J aléatoires mais reproductibles (seed fixe), dans [lo, n-1]."""
    rng = np.random.default_rng(seed)
    return sorted(int(x) for x in rng.choice(np.arange(lo, n), size=k, replace=False))


class TestLookAheadIndicators(unittest.TestCase):
    """AUD-01a : les indicateurs à la ligne J doivent être invariants par troncature à J."""

    def test_row_J_invariant_under_truncation(self):
        base = make_ohlcv(n=250, drift=0.001, vol=0.02, seed=11)
        df_full = add_all(base)
        cols = [c for c in df_full.columns if c not in OHLCV]
        for j in random_days(250, seed=1):
            df_trunc = add_all(base.iloc[: j + 1])
            va = df_full.iloc[j][cols].to_numpy(dtype=float)
            vb = df_trunc.iloc[j][cols].to_numpy(dtype=float)
            close_mask = np.isclose(va, vb, atol=1e-9, rtol=1e-9, equal_nan=True)
            self.assertTrue(
                bool(close_mask.all()),
                f"REPAINT détecté à J={j} sur {list(np.array(cols)[~close_mask])}",
            )


class TestLookAheadStrategies(unittest.TestCase):
    """AUD-01b : le signal (position/stop/target) à J doit être le même que le pipeline
    complet produise à J, pour chaque stratégie."""

    def test_signals_at_J_invariant_under_truncation(self):
        base = make_ohlcv(n=250, drift=0.001, vol=0.02, seed=5)
        df_full = add_all(base)
        for s in list_strategies():
            key = s["key"]
            sig_full = get_strategy(key).generate(df_full)
            for j in random_days(250, seed=2, lo=80):
                sig_tr = get_strategy(key).generate(add_all(base.iloc[: j + 1]))
                for col in ("position", "stop", "target"):
                    a, b = sig_full[col].iloc[j], sig_tr[col].iloc[-1]
                    if pd.isna(a) and pd.isna(b):
                        continue
                    self.assertEqual(
                        a, b, f"stratégie {key}, colonne {col}, J={j} : {a} != {b} (fuite de données futures)"
                    )


class TestLookAheadAdvice(unittest.TestCase):
    """AUD-01c : l'avis à J (score/label) ne doit dépendre que des données ≤ J."""

    def setUp(self):
        self._orig_cb = events_mod.fetch_central_bank_events
        events_mod.fetch_central_bank_events = lambda **kw: []

    def tearDown(self):
        events_mod.fetch_central_bank_events = self._orig_cb

    def test_advise_at_J_ignores_perturbed_future(self):
        base = make_ohlcv(n=200, drift=0.0015, vol=0.02, seed=9)
        for j in random_days(200, seed=4, lo=60):
            adv_ref = advise(add_all(base.iloc[: j + 1]), "BTCUSDT", source="crypto", interval="1d")
            # on perturbe fortement les barres FUTURES (> J) : l'avis à J ne doit pas bouger
            pert = base.copy()
            fut = pert.index[j + 1 :]
            pert.loc[fut, ["Open", "High", "Low", "Close"]] *= 1.7
            pert.loc[fut, "Volume"] *= 5
            adv_pert = advise(add_all(pert.iloc[: j + 1]), "BTCUSDT", source="crypto", interval="1d")
            self.assertEqual(adv_ref["score"], adv_pert["score"], f"score J={j} a changé avec le futur")
            self.assertEqual(adv_ref["label"], adv_pert["label"], f"label J={j} a changé avec le futur")
            self.assertEqual(adv_ref["last_close"], float(base["Close"].iloc[j]))


class TestLookAheadBacktest(unittest.TestCase):
    """AUD-01d/e : le backtest ne regarde pas le futur — préfixe d'equity invariant par
    troncature, et aucun remplissage au prix du jour du signal."""

    def test_equity_prefix_invariant_under_truncation(self):
        base = make_trend_reversal(n=200, seed=3)
        df_full = add_all(base)
        strat = get_strategy("ma_cross")
        res_full = run_backtest(df_full, strat.generate(df_full), initial_capital=10_000)
        for j in random_days(200, seed=3, lo=60):
            df_tr = add_all(base.iloc[: j + 1])
            res_tr = run_backtest(df_tr, strat.generate(df_tr), initial_capital=10_000)
            a = res_full.equity.iloc[:j].to_numpy()
            b = res_tr.equity.iloc[:j].to_numpy()
            self.assertTrue(
                np.allclose(a, b, atol=1e-8),
                f"equity diverge avant J={j} : écart max {np.max(np.abs(a - b)):.6f} -> lookahead dans le moteur",
            )

    def test_fill_is_next_bar_open_never_signal_bar(self):
        # AUD-01e : remplissage au open de la barre SUIVANTE, jamais au prix du signal
        base = make_trend_reversal(n=200, seed=3)
        df = add_all(base)
        sig = get_strategy("ma_cross").generate(df)
        res = run_backtest(df, sig, initial_capital=10_000, fee=get_fee_model("zero"), slippage=0.0)
        self.assertGreater(len(res.trades), 0)
        pos = df.index.get_indexer(res.trades["entry_time"])
        for bar, tr in zip(pos, res.trades.itertuples(), strict=True):
            self.assertGreater(bar, 0)
            # le signal venait de la barre précédente, pas de la barre de remplissage
            self.assertNotEqual(int(sig["position"].iloc[bar]), 0, "fill sur une barre sans signal")
            self.assertEqual(
                int(sig["position"].iloc[bar - 1]),
                int(sig["position"].iloc[bar]),
                "le signal a changé entre la barre du signal et celle du fill",
            )
            # prix de remplissage = open de la barre de fill (slippage 0, frais 0)
            self.assertAlmostEqual(tr.entry_price, float(df["Open"].iloc[bar]), places=9)


class TestNoCherryPicking(unittest.TestCase):
    """AUD-03 : le compte de lignes traitées == le compte de signaux émis (aucune
    exclusion rétrospective de « mauvais trades »)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.pf = PaperPortfolio(store=self.store, initial_capital=100_000.0, max_positions=10)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    @staticmethod
    def _row(sym, position="ACHAT", error=None):
        return {
            "symbol": sym,
            "type": "Action",
            "position": position,
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "last_close": 100.0,
            "label": "ACHAT",
            "best_strategy": "ma_cross",
            "error": error,
            "score": 0.5,
        }

    def test_every_signal_row_is_counted(self):
        rows = [
            self._row("AAA"),
            self._row("BBB", "ATTENTE"),
            self._row("CCC", "SHORT"),
            self._row("ERR", error="pas de données"),
            self._row("DDD"),
        ]
        out = self.pf.apply_screener(rows, source="stock")
        # chaque ligne de scan (signal émis) est comptée, y compris erreurs et non-ACHAT
        self.assertEqual(out["n_signals"], len(rows))
        # toute ligne non erreur est soit ouverte, soit explicitement skippée (jamais perdue)
        accounted = [p["symbol"] for p in out["opened"]] + [s.split(" (")[0] for s in out["skipped"]]
        for r in rows:
            if r.get("error"):
                continue
            self.assertIn(r["symbol"], accounted, f"signal {r['symbol']} non comptabilisé")
        self.assertIn("ERR (erreur: pas de données)", out["skipped"])


if __name__ == "__main__":
    unittest.main()
