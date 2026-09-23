from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_index_recovery_v2 import metadata_identity, domain_ok

REG=Path(__file__).with_name("nova_n10_referee_aia_index_recovery_registry_v2.json")

class T(unittest.TestCase):
    def test_registry_and_recovery_trigger(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"bcf22301e3b0700e3724a86cccd924e9f5b498cb")
        self.assertEqual(p["recovery_trigger"]["prior_pr"],452)
        self.assertEqual(p["recovery_trigger"]["prior_run"],35818082497)
        self.assertEqual(p["recovery_trigger"]["new_availability_pr"],459)
        self.assertEqual(p["recovery_trigger"]["new_availability_run"],35828281347)
        self.assertEqual(p["recovery_trigger"]["max_recovery_attempts"],1)
        self.assertFalse(p["hard_rules"]["additional_index_variants_allowed"])
        self.assertFalse(p["hard_rules"]["rerun_after_this_recovery_if_fail"])

    def test_zero_label_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["article_body_fetch_allowed"])
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["match_payload_read"])
        self.assertFalse(h["standings_payload_read"])
        self.assertFalse(h["player_stats_payload_read"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])

    def test_identity(self):
        target={
            "expected_title":"SERIE A TIM - Designazioni 10ª Giornata",
            "expected_date":"12/10/2022",
            "expected_slug":"serie-a-tim-designazioni-10-giornata-20654"
        }
        raw='<a href="/news/serie-a-tim-designazioni-10-giornata-20654/">SERIE A TIM - Designazioni 10ª Giornata</a><span>12/10/2022</span>'.encode("utf-8")
        r=metadata_identity(raw,target)
        self.assertTrue(r["identity_pass"])

    def test_identity_fail(self):
        target={
            "expected_title":"SERIE A TIM - Designazioni 10ª Giornata",
            "expected_date":"12/10/2022",
            "expected_slug":"serie-a-tim-designazioni-10-giornata-20654"
        }
        self.assertFalse(metadata_identity(b"<html>other</html>",target)["identity_pass"])

    def test_domain(self):
        self.assertTrue(domain_ok("https://www.aia-figc.it/news/","aia-figc.it"))
        self.assertFalse(domain_ok("https://aia-figc.it.evil.example/","aia-figc.it"))

if __name__=="__main__":
    unittest.main()
