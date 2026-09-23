from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_official_archive_collector_v1 import (
    domain_ok, in_target, matches_url, publication_of, parse_sitemap,
    round_no, sha256_bytes, title_of
)

REG = Path(__file__).with_name("nova_n10_referee_official_archive_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_zero_label(self):
        p = json.loads(REG.read_text())
        self.assertEqual(p["exact_base"], "b201d3476b22f6fd2e80378990d83dfa6320b7da")
        self.assertEqual(p["target"]["inventory_competitions"], ["EPL", "La_liga", "Serie_A"])
        self.assertEqual(p["target"]["gap_only_competitions"], ["Bundesliga", "Ligue_1"])
        self.assertEqual(p["safety"]["result_labels_read"], 0)
        self.assertEqual(p["safety"]["score_values_read"], 0)
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])
        self.assertEqual(p["hard_rules"]["candidate_weight"], 0)
        self.assertEqual(p["hard_rules"]["matrix_delta"], 0)

    def test_publication_and_title(self):
        raw = b'''<html><head><meta property="article:published_time" content="2023-06-01T08:01:00+02:00">
        <title>Designaciones jornada 38</title></head><body><h1>Designaciones jornada 38</h1></body></html>'''
        p, precision, src = publication_of(raw)
        self.assertEqual(p, "2023-06-01T08:01:00+02:00")
        self.assertEqual(precision, "DATETIME_TZ")
        self.assertEqual(src, "META")
        self.assertEqual(title_of(raw), "Designaciones jornada 38")

    def test_date_only_and_target(self):
        raw = b"<html><body><h1>SERIE A TIM - Designazioni 6 giornata</h1>07/09/2022</body></html>"
        p, precision, _ = publication_of(raw)
        self.assertEqual(p, "2022-09-07")
        self.assertEqual(precision, "DATE_ONLY")
        self.assertTrue(in_target(p, "2022-08-01", "2023-06-30"))

    def test_sitemap(self):
        raw = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://x.test/news/a</loc></url></urlset>'
        kind, locs = parse_sitemap(raw)
        self.assertEqual(kind, "urlset")
        self.assertEqual(locs, ["https://x.test/news/a"])

    def test_url_and_round(self):
        self.assertTrue(domain_ok("https://www.aia-figc.it/news/x", "aia-figc.it"))
        self.assertFalse(domain_ok("https://evil.example/news/x", "aia-figc.it"))
        self.assertTrue(matches_url("https://rfef.es/es/noticias/designaciones-jornada-38", [r"/noticias/.*designacion"]))
        self.assertEqual(round_no("Match officials for Matchweek 14", "", [r"matchweek\s*(\d+)"]), 14)
        self.assertEqual(round_no("SERIE A - Designazioni 28 giornata", "", [r"(\d+)\s*giornata"]), 28)

    def test_sha_deterministic(self):
        self.assertEqual(sha256_bytes(b"abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

if __name__ == "__main__":
    unittest.main()
