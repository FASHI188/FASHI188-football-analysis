from __future__ import annotations
import datetime as dt
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_cdn_witness_v1 import (
    token_from_url, token_time_utc, created_at_utc, referee_found, official_host_ok
)
REG=Path(__file__).with_name("nova_n10_referee_lega_cdn_witness_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"6497f8a0fcec25ac5d456e76291af53328c4904d")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["target_score_values_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])
        self.assertFalse(p["decision_contract"]["formal_available_at_proven_by_this_batch"])
    def test_token_decode(self):
        u="https://img.legaseriea.it/vimages/6347f494/x.pdf"
        self.assertEqual(token_from_url(u),"6347f494")
        self.assertEqual(token_time_utc(u),dt.datetime(2022,10,13,11,20,52,tzinfo=dt.timezone.utc))
    def test_created(self):
        t="foo created on 06/10/2022 on 18:46:08 bar"
        x=created_at_utc(t)
        self.assertIsNotNone(x)
        self.assertEqual(x.isoformat(),"2022-10-06T16:46:08+00:00")
    def test_referee(self):
        self.assertTrue(referee_found("Referee:\nANTONIO RAPUANO\nFourth Official","ANTONIO RAPUANO"))
        self.assertFalse(referee_found("Referee:\nOTHER","ANTONIO RAPUANO"))
    def test_host(self):
        self.assertTrue(official_host_ok("https://img.legaseriea.it/vimages/x/a.pdf"))
        self.assertFalse(official_host_ok("https://evil.example/a.pdf"))
if __name__=="__main__": unittest.main()
