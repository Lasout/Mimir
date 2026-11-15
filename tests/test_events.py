"""Tests hors-ligne du calendrier des banques centrales (parsers HTML purs + cache)."""

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from tradehelper.config import DEFAULTS
from tradehelper.data.events import (
    ECB_CAL_URL,
    FED_CAL_URL,
    fetch_central_bank_events,
    parse_ecb_calendar,
    parse_fed_calendar,
)
from tradehelper.data.store import Store

FED_FIXTURE = """
<html><body><h2>2026 FOMC Meetings</h2>
<ul>
<li>January 27-28 <a>Statement</a></li>
<li>March 17-18* <a>Statement</a></li>
<li>April 28-29 <a>Statement</a></li>
</ul>
<h2>2027 FOMC Meetings</h2>
<ul><li>January 26-27 <a>Statement</a></li></ul>
</body></html>
"""

ECB_FIXTURE = """
<html><body><div>
<p>16/12/2026 Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 1)</p>
<p>17/12/2026 Governing Council of the ECB: monetary policy meeting in Frankfurt (Day 2), followed by press conference</p>
<p>25/11/2026 Governing Council of the ECB: non-monetary policy meeting (in Frankfurt)</p>
<p>03/02/2027 Governing Council of the ECB: monetary policy meeting (Day 1)</p>
<p>04/02/2027 Governing Council of the ECB: monetary policy meeting (Day 2), followed by press conference</p>
</div></body></html>
"""


class TestParsers(unittest.TestCase):
    def test_parse_fed(self):
        ev = parse_fed_calendar(FED_FIXTURE)
        dates = {(e["date"], e["bank"]) for e in ev}
        # jour 2 (décision) retenu, année de l'en-tête prise en compte
        self.assertIn((dt.date(2026, 1, 28), "FOMC"), dates)
        self.assertIn((dt.date(2026, 3, 18), "FOMC"), dates)
        self.assertIn((dt.date(2027, 1, 27), "FOMC"), dates)
        self.assertNotIn((dt.date(2026, 1, 27), "FOMC"), dates)  # jour 1 exclu
        self.assertEqual(len(ev), 4)

    def test_parse_fed_html_vide(self):
        self.assertEqual(parse_fed_calendar("<html></html>"), [])

    def test_parse_ecb(self):
        ev = parse_ecb_calendar(ECB_FIXTURE)
        dates = {(e["date"], e["bank"]) for e in ev}
        # seuls les jours de décision (Day 2) des réunions monétaires sont gardés
        self.assertEqual(dates, {(dt.date(2026, 12, 17), "BCE"), (dt.date(2027, 2, 4), "BCE")})

    def test_parse_ecb_sans_day2_repli(self):
        html = "<p>03/02/2027 Governing Council of the ECB: monetary policy meeting (virtual)</p>"
        ev = parse_ecb_calendar(html)
        self.assertEqual([e["date"] for e in ev], [dt.date(2027, 2, 3)])


class TestFetchCbEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.db")
        self.cfg = dict(DEFAULTS)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _fetcher(self, compteur):
        def fetch(url):
            compteur[url] = compteur.get(url, 0) + 1
            if "federalreserve" in url:
                # dates dynamiques : une réunion passée, une dans 10 j, une dans 200 j
                past = dt.date.today() - dt.timedelta(days=30)
                soon = dt.date.today() + dt.timedelta(days=10)
                far = dt.date.today() + dt.timedelta(days=200)

                def fmt(d):
                    return d.strftime("%B ") + f"{d.day}-{(d + dt.timedelta(days=1)).day}, {d.year}"

                return f"<h2>{past.year} FOMC Meetings</h2><ul><li>{fmt(past)}</li><li>{fmt(soon)}</li><li>{fmt(far)}</li></ul>"
            return ECB_FIXTURE

        return fetch

    def test_fetch_filtre_et_cache(self):
        compteur = {}
        ev = fetch_central_bank_events(store=self.store, cfg=self.cfg, fetcher=self._fetcher(compteur))
        self.assertTrue(all(e["date"] >= dt.date.today() - dt.timedelta(days=1) for e in ev))
        # jour 2 de la réunion « dans 10 j » retenu (décision), la réunion à 200 j filtrée
        self.assertTrue(any(e["bank"] == "FOMC" and 8 <= (e["date"] - dt.date.today()).days <= 12 for e in ev))
        self.assertFalse(any((e["date"] - dt.date.today()).days > 90 for e in ev))  # horizon 90 j
        # BCE : fixture avec dates fixes (peut être dans le passé -> filtrées ou lointaines)
        urls = {FED_CAL_URL, ECB_CAL_URL}
        self.assertEqual(set(compteur), urls)
        # 2e appel : servi par le cache, pas de nouvel appel réseau
        ev2 = fetch_central_bank_events(store=self.store, cfg=self.cfg, fetcher=self._fetcher(compteur))
        self.assertEqual(ev, ev2)
        self.assertEqual(compteur[FED_CAL_URL], 1)

    def test_fetch_resilient_si_panne(self):
        def boom(url):
            raise RuntimeError("offline")

        ev = fetch_central_bank_events(store=self.store, cfg=self.cfg, fetcher=boom)
        self.assertEqual(ev, [])  # pas d'exception, liste vide


if __name__ == "__main__":
    unittest.main()
