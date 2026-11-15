"""Tests hors-ligne de gui/i18n.py : parité fr/en, repli, get_lang."""

import os
import unittest
from unittest import mock

from tradehelper.gui.i18n import LANGS, STRINGS, get_lang, t


class TestI18n(unittest.TestCase):
    def test_parite_fr_en(self):
        self.assertEqual(
            set(STRINGS["fr"]), set(STRINGS["en"]), f"clés différentes: {set(STRINGS['fr']) ^ set(STRINGS['en'])}"
        )

    def test_langues(self):
        self.assertEqual(LANGS, ("fr", "en"))

    def test_t_defaut_fr(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(t("menu_file"), "Fichier")
            self.assertEqual(t("menu_file", "en"), "File")
            self.assertEqual(t("menu_file", "de"), "Fichier")  # langue inconnue -> fr

    def test_t_repli_cle_inconnue(self):
        self.assertEqual(t("cle_qui_nexiste_pas", "en"), "cle_qui_nexiste_pas")

    def test_get_lang_env(self):
        with mock.patch.dict(os.environ, {"TRADEHELPER_LANG": "en"}):
            self.assertEqual(get_lang(), "en")
        with mock.patch.dict(os.environ, {"TRADEHELPER_LANG": "xx"}):
            self.assertEqual(get_lang(), "fr")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_lang(), "fr")
            self.assertEqual(get_lang({"lang": "en"}), "en")


if __name__ == "__main__":
    unittest.main()
