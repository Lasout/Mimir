"""Tests hors-ligne de gui/theme.py (partie pure : palettes, rcParams matplotlib, police).

La partie ttk (apply_ttk) nécessite un display X : couverte par le test GUI de fumée,
pas par la suite unitaire (qui doit rester exécutable sans écran).
"""

import unittest

import matplotlib
from matplotlib.figure import Figure

from tradehelper.gui.theme import (FONT_SCALE_MAX, FONT_SCALE_MIN, PALETTES, THEMES,
                                   apply_mpl_theme, font_size, mpl_rc)


class TestPalettes(unittest.TestCase):
    def test_themes_disponibles(self):
        self.assertEqual(set(THEMES), {"clair", "sombre"})
        self.assertEqual(set(PALETTES), set(THEMES))

    def test_palette_complet(self):
        keys = {"bg", "fg", "field", "accent", "accent_fg", "select_bg", "heading",
                "disabled", "border"}
        for name, p in PALETTES.items():
            self.assertTrue(keys <= set(p), f"clés manquantes dans {name}: {keys - set(p)}")
            for k, v in p.items():
                self.assertRegex(v, r"^#[0-9a-fA-F]{6}$", f"{name}.{k} pas une couleur hex")

    def test_sombre_vraiment_sombre(self):
        def lum(hexcol):
            r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
            return 0.299 * r + 0.587 * g + 0.114 * b
        self.assertLess(lum(PALETTES["sombre"]["bg"]), 80)
        self.assertGreater(lum(PALETTES["clair"]["bg"]), 200)


class TestMplRc(unittest.TestCase):
    def setUp(self):
        self._saved = dict(matplotlib.rcParams)

    def tearDown(self):
        matplotlib.rcParams.update(self._saved)

    def test_mpl_rc_sombre(self):
        rc = mpl_rc("sombre")
        self.assertEqual(rc["figure.facecolor"], "#1e1e24")
        self.assertEqual(rc["text.color"], rc["xtick.color"])
        self.assertIn("axes.facecolor", rc)

    def test_mpl_rc_inconnu_repli_clair(self):
        rc = mpl_rc("nimportequoi")
        self.assertEqual(rc["figure.facecolor"], "#ffffff")

    def test_apply_mpl_theme(self):
        apply_mpl_theme("sombre")
        self.assertEqual(matplotlib.rcParams["figure.facecolor"], "#1e1e24")
        apply_mpl_theme("clair")
        self.assertEqual(matplotlib.rcParams["figure.facecolor"], "#ffffff")

    def test_figure_suivit_le_theme(self):
        apply_mpl_theme("sombre")
        fig = Figure()
        ax = fig.add_subplot(111)
        self.assertEqual(ax.get_facecolor(), matplotlib.colors.to_rgba("#26262e"))


class TestFontSize(unittest.TestCase):
    def test_font_size(self):
        self.assertEqual(font_size(1.0), 10)
        self.assertEqual(font_size(1.2), 12)
        self.assertEqual(font_size(0.5), 7)  # plancher
        self.assertGreaterEqual(font_size(FONT_SCALE_MIN), 7)
        self.assertLessEqual(font_size(FONT_SCALE_MAX), 30)


if __name__ == "__main__":
    unittest.main()
