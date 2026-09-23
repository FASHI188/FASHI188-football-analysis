from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_archive_witness_v1 import (
    build_cdx_url, cdx_to_value, eligible, official_domain_ok, parse_cdx,
    safe_capture, snapshot_url
)

REG=Path(__file__).with_name("nova_n10_referee_archive_witness_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_zero_label(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"5cd614134246c8d6c756e6fd170986b700d1d09c")
        self.assertEqual(len(p["samples"]),3)
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])
        self.assertTrue(p["hard_rules"]["capture_at_or_after_safe_cutoff_forbidden"])
        self.assertEqual(p["safety"]["candidate_weight"],0)

    def test_capture_gate_strict(self):
        self.assertTrue(safe_capture("20221028235959","2022-10-29T00:00:00Z"))
        self.assertFalse(safe_capture("20221029000000","2022-10-29T00:00:00Z"))
        self.assertFalse(safe_capture("20221030000000","2022-10-29T00:00:00Z"))
        self.assertEqual(cdx_to_value("2022-10-29T00:00:00Z"),"20221028235959")

    def test_cdx_parse_and_eligible(self):
        raw=json.dumps([
            ["timestamp","original","statuscode","digest","mimetype"],
            ["20221027120000","https://www.premierleague.com/en/news/2866213","200","ABC","text/html"],
            ["20221029120000","https://www.premierleague.com/en/news/2866213","200","DEF","text/html"],
            ["20221027130000","https://evil.example/x","200","GHI","text/html"]
        ]).encode()
        rows=parse_cdx(raw)
        sample={"official_domain":"premierleague.com","safe_cutoff_utc":"2022-10-29T00:00:00Z"}
        good=eligible(rows,sample)
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["digest"],"ABC")

    def test_domains(self):
        self.assertTrue(official_domain_ok("https://www.rfef.es/a","rfef.es"))
        self.assertTrue(official_domain_ok("https://rfef.es/a","rfef.es"))
        self.assertFalse(official_domain_ok("https://rfef.es.evil.example/a","rfef.es"))

    def test_urls_are_deterministic(self):
        u=build_cdx_url("https://web.archive.org/cdx/search/cdx","https://x.test/a","2022-10-29T00:00:00Z")
        self.assertIn("to=20221028235959",u)
        self.assertIn("output=json",u)
        self.assertEqual(snapshot_url("https://web.archive.org/web/","20221027120000","https://x.test/a"),
                         "https://web.archive.org/web/20221027120000id_/https://x.test/a")

if __name__=="__main__":
    unittest.main()
