"""Tests hors-ligne de gui/export.py (PNG via Figure Agg, CSV, TXT)."""

import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from matplotlib.figure import Figure

from tradehelper.gui.export import (SCREENER_COLS, export_figure_png, export_screener_csv,
                                    export_text_report, export_trades_csv)
from tests.helpers import make_ohlcv


class TestExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_screener_csv(self):
        rows = [
            {"symbol": "SPY", "type": "ETF", "score": 0.42, "label": "Achat", "position": "ACHAT",
             "sharpe": 1.3, "total_return": 0.21, "n_trades": 12, "error": None, "entry": 500.0},
            {"symbol": "AAPL", "type": "Action", "score": -0.1, "label": "Neutre", "error": None},
        ]
        path = export_screener_csv(rows, self.dir / "scr.csv")
        with open(path, encoding="utf-8") as f:
            out = list(csv.DictReader(f))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["symbol"], "SPY")
        self.assertEqual(out[0]["type"], "ETF")
        self.assertEqual(out[1]["symbol"], "AAPL")
        # colonnes dans l'ordre d'affichage prévu
        self.assertEqual(out[0].keys() and list(out[0].keys())[:2], SCREENER_COLS[:2])

    def test_export_screener_csv_lignes_heterogenes(self):
        rows = [{"symbol": "X", "error": "boom"}, {"zzz": 1}]
        path = export_screener_csv(rows, self.dir / "scr2.csv")
        with open(path, encoding="utf-8") as f:
            out = list(csv.DictReader(f))
        self.assertEqual(len(out), 2)
        self.assertIn("symbol", out[0])
        self.assertIn("zzz", out[1])

    def test_export_trades_csv(self):
        df = make_ohlcv(n=30)
        trades = pd.DataFrame({
            "entry_time": [df.index[5], df.index[15]],
            "exit_time": [df.index[10], df.index[20]],
            "side": ["long", "long"], "pnl": [12.5, -3.0], "ret": [0.01, -0.002],
            "bars": [5, 5], "reason": ["signal", "stop"],
        })
        path = export_trades_csv(trades, self.dir / "trades.csv")
        out = pd.read_csv(path)
        self.assertEqual(len(out), 2)
        self.assertIn("entry_time", out.columns)
        self.assertRegex(str(out["entry_time"].iloc[0]), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
        self.assertAlmostEqual(float(out["pnl"].iloc[0]), 12.5)

    def test_export_text_report(self):
        path = export_text_report("Rapport — rendement +12.3 %\n", self.dir / "rapport.txt")
        self.assertEqual(path.read_text(encoding="utf-8"), "Rapport — rendement +12.3 %\n")

    def test_export_figure_png(self):
        fig = Figure(figsize=(4, 3), dpi=80)
        ax = fig.add_subplot(111)
        ax.plot([1, 2, 3], [1, 4, 2])
        path = export_figure_png(fig, self.dir / "chart.png")
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG"))
        self.assertGreater(len(data), 1000)


if __name__ == "__main__":
    unittest.main()
