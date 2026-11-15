"""Tests hors-ligne de la configuration (dont le chargement de `.env`)."""

import os
import unittest
from unittest import mock

from tradehelper.config import _parse_dotenv, load_config


class TestParseDotenv(unittest.TestCase):
    def test_parse_lignes_simples(self):
        out = _parse_dotenv(
            "# commentaire\n"
            "ALPHAVANTAGE_API_KEY=abc123\n"
            "\n"
            "TRADEHELPER_LANG = \"en\"\n"
            "TRADEHELPER_ADJUST_PRICES='0'\n"
            "ligne_sans_egal\n"
        )
        self.assertEqual(out["ALPHAVANTAGE_API_KEY"], "abc123")
        self.assertEqual(out["TRADEHELPER_LANG"], "en")
        self.assertEqual(out["TRADEHELPER_ADJUST_PRICES"], "0")
        self.assertNotIn("ligne_sans_egal", out)
        self.assertNotIn("# commentaire", out)

    def test_valeur_avec_egal(self):
        out = _parse_dotenv("TRADEHELPER_CONFIG=/tmp/a=b.json")
        self.assertEqual(out["TRADEHELPER_CONFIG"], "/tmp/a=b.json")


class TestLoadConfig(unittest.TestCase):
    def test_env_gagne_sur_dotenv(self):
        with mock.patch.dict(os.environ, {"ALPHAVANTAGE_API_KEY": "ENVKEY"}, clear=False):
            with mock.patch("tradehelper.config._load_dotenv",
                            return_value={"ALPHAVANTAGE_API_KEY": "DOTENVKEY"}):
                cfg = load_config()
        self.assertEqual(cfg["alphavantage_api_key"], "ENVKEY")

    def test_dotenv_alimente_la_config(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("tradehelper.config._load_dotenv",
                            return_value={"ALPHAVANTAGE_API_KEY": "DOTENVKEY",
                                          "TRADEHELPER_LANG": "en"}):
                cfg = load_config()
        self.assertEqual(cfg["alphavantage_api_key"], "DOTENVKEY")
        self.assertEqual(cfg["lang"], "en")

    def test_defauts_sans_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("tradehelper.config._load_dotenv", return_value={}):
                cfg = load_config()
        self.assertIsNone(cfg["alphavantage_api_key"])
        self.assertEqual(cfg["lang"], "fr")
        self.assertTrue(cfg["adjust_prices"])


if __name__ == "__main__":
    unittest.main()
