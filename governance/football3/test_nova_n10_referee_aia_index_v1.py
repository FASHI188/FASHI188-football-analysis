from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_index_v1 import metadata_identity, domain_ok
REG=Path(__file__).with_name("nova_n10_referee_aia_index_registry_v1.json")
class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"10d10da1554099996a5e6dbaa986487b58646c73")
        self.assertFalse(p["hard_rules"]["article_body_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
    def test_identity(self):
        target={"expected_title":"SERIE A TIM - Designazioni 10ª Giornata","expected_date":"12/10/2022","expected_slug":"serie-a-tim-designazioni-10-giornata-20654"}
        raw=b'<a href="/news/serie-a-tim-designazioni-10-giornata-20654/">SERIE A TIM - Designazioni 10Âª Giornata</a><span>12/10/2022</span>'
        r=metadata_identity(raw,target)
        self.assertTrue(r["identity_pass"])
    def test_identity_fail(self):
        target={"expected_title":"SERIE A TIM - Designazioni 10ª Giornata","expected_date":"12/10/2022","expected_slug":"serie-a-tim-designazioni-10-giornata-20654"}
        self.assertFalse(metadata_identity(b"<html>other</html>",target)["identity_pass"])
    def test_domain(self):
        self.assertTrue(domain_ok("https://www.aia-figc.it/news/","aia-figc.it"))
        self.assertFalse(domain_ok("https://evil.example/","aia-figc.it"))
if __name__=="__main__": unittest.main()
