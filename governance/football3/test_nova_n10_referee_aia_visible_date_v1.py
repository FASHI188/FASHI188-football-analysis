from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_visible_date_v1 import locate_marker, date_is_safe, target_h1_ok, domain_ok
REG=Path(__file__).with_name("nova_n10_referee_aia_visible_date_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"80fa4008f8e6c908d353637117a36a7d3634aa93")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["appointment_names_parsed"])
        self.assertFalse(p["hard_rules"]["appointment_content_persisted"])
        self.assertFalse(p["evidence_contract"]["formal_available_at_proven"])

    def test_marker(self):
        raw=b'<html><body><h1>SERIE A TIM - Designazioni 10 Giornata</h1><ul><li>12/10/2022</li></ul><p>RAPUANO SHOULD NOT BE PERSISTED</p></body></html>'
        x=locate_marker(raw,["serie a","designazioni","10","giornata"])
        self.assertIsNotNone(x)
        self.assertEqual(x["date_text"],"12/10/2022")
        self.assertNotIn(b"RAPUANO",x["persisted_prefix"])

    def test_identity_fail(self):
        with self.assertRaises(Exception):
            locate_marker(b'<h1>OTHER TITLE</h1><span>12/10/2022</span>',["serie a","designazioni","10","giornata"])

    def test_date_safe(self):
        self.assertTrue(date_is_safe("12/10/2022","12/10/2022","2022-10-15T00:00:00Z"))
        self.assertFalse(date_is_safe("15/10/2022","12/10/2022","2022-10-15T00:00:00Z"))

    def test_domain(self):
        self.assertTrue(domain_ok("https://www.aia-figc.it/news/x","aia-figc.it"))
        self.assertFalse(domain_ok("https://evil.example/x","aia-figc.it"))
        self.assertTrue(target_h1_ok("SERIE A TIM - Designazioni 10 Giornata",["serie a","designazioni","10","giornata"]))

if __name__=="__main__":
    unittest.main()
