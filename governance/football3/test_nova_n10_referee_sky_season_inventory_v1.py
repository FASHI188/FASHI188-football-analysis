from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_sky_season_inventory_v1 import (
    ArchiveParser, candidate_links, normalized_url, round_signal,
    referee_signal, serie_a_signal, HeaderTimestampParser, TimestampFound
)

REG=Path(__file__).with_name("nova_n10_referee_sky_season_inventory_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"31c756e256b8d9b4ffbe4411b9dedec2a350d9d0")
        self.assertEqual(p["discovery_contract"]["day_offsets"],[0,1,2])
        self.assertEqual(p["selection_contract"]["coverage_denominator"],38)
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["referee_assignment_body_parsed"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_round_signal_boundaries(self):
        self.assertTrue(round_signal("Arbitri Serie A 10^ giornata","",10))
        self.assertTrue(round_signal("","https://sport.sky.it/calcio/serie-a/x/giornata-10",10))
        self.assertFalse(round_signal("Arbitri Serie A 10^ giornata","",1))
        self.assertTrue(round_signal("Serie A, gli arbitri della prima giornata","",1))

    def test_signals(self):
        self.assertTrue(referee_signal("Le designazioni","",["arbitri","designazioni"]))
        self.assertTrue(serie_a_signal("","https://sport.sky.it/calcio/serie-a/2022/x"))
        self.assertFalse(serie_a_signal("Premier League","https://sport.sky.it/calcio/premier-league/x"))

    def test_archive_parser_and_candidate_filter(self):
        raw='''<html><body>
        <a href="/calcio/serie-a/2022/10/12/arbitri-serie-a-designazioni-giornata-10">Arbitri Serie A, le designazioni per la 10^ giornata</a>
        <a href="/calcio/serie-a/2022/10/12/altro">Serie A mercato</a>
        <a href="/archivio/2022/10/12?pag=2">2</a>
        </body></html>'''
        parser=ArchiveParser("https://sport.sky.it/archivio/2022/10/12")
        parser.feed(raw)
        rep={"date":"2022-10-12","pages":[{"page":1,"anchors":parser.anchors}]}
        target={"round":10}
        discovery={"referee_terms":["arbitri","arbitro","designazioni","designazione"]}
        source={"domain_suffix":"sport.sky.it"}
        out=candidate_links(rep,target,discovery,source)
        self.assertEqual(len(out),1)
        self.assertIn("giornata-10",out[0]["normalized_url"])

    def test_normalized_url_amp(self):
        self.assertEqual(
            normalized_url("https://sport.sky.it/calcio/serie-a/x/amp?foo=1#bar"),
            "https://sport.sky.it/calcio/serie-a/x"
        )

    def test_visible_timestamp_parser_stops(self):
        p=HeaderTimestampParser(
            28,
            ["arbitri","arbitro","designazioni","designazione"],
            {"2023-03-29","2023-03-30","2023-03-31"},
        )
        html='''<html><head><title>Serie A, 28^ giornata: le partite, dove vederle e gli arbitri | Sky Sport</title></head>
        <body><div>30 mar 2023 - 15:00</div><p>ARBITRO BODY MUST NOT BE NEEDED</p></body></html>'''
        with self.assertRaises(TimestampFound):
            p.feed(html)
        self.assertTrue(p.title_identity_pass)
        self.assertEqual(p.marker_iso_local,"2023-03-30T15:00:00")

if __name__=="__main__":
    unittest.main()
