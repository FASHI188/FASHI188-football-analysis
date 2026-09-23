from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_gdelt_gkg_daily_v1 import (
    build_daily_url, exact_target_url, normalize_identity, scan_zip, urls_from_line
)
import io, zipfile

REG=Path(__file__).with_name("nova_n10_referee_aia_gdelt_gkg_daily_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"15cf52d412603729ff2e0eb1b13edf2718d158c2")
        self.assertEqual(p["target"]["round"],10)
        self.assertEqual(len(p["target"]["date_sequence"]),8)
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["full_gkg_row_persisted"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_identity_normalization(self):
        target={
            "normalized_host":"aia-figc.it",
            "normalized_path":"/news/serie-a-tim-designazioni-10-giornata-20654"
        }
        self.assertTrue(exact_target_url(
            "https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/",
            target
        ))
        self.assertTrue(exact_target_url(
            "http://aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654",
            target
        ))
        self.assertFalse(exact_target_url(
            "https://www.aia-figc.it/news/serie-a-tim-designazioni-11-giornata-20689/",
            target
        ))
        self.assertEqual(
            normalize_identity("https://www.aia-figc.it/news/x/?a=1#b"),
            ("aia-figc.it","/news/x")
        )

    def test_daily_url(self):
        self.assertEqual(
            build_daily_url("https://data.gdeltproject.org/gkg/{yyyymmdd}.gkg.csv.zip","2022-10-12"),
            "https://data.gdeltproject.org/gkg/20221012.gkg.csv.zip"
        )

    def test_url_extraction(self):
        line=b'20221012\thttps://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/;https://example.test/x\n'
        urls=urls_from_line(line)
        self.assertIn("https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/",urls)
        self.assertIn("https://example.test/x",urls)

    def test_zip_scan_persists_hash_not_full_line(self):
        target={
            "normalized_host":"aia-figc.it",
            "normalized_path":"/news/serie-a-tim-designazioni-10-giornata-20654"
        }
        payload=(
            b'20221012\thttps://example.test/x\n'
            b'20221012\thttps://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/\n'
        )
        bio=io.BytesIO()
        with zipfile.ZipFile(bio,"w",zipfile.ZIP_DEFLATED) as z:
            z.writestr("20221012.gkg.csv",payload)
        r=scan_zip(bio.getvalue(),target,".gkg.csv")
        self.assertEqual(r["match_n"],1)
        self.assertEqual(len(r["matches"][0]["line_sha256"]),64)
        self.assertNotIn("line",r["matches"][0])
        self.assertEqual(
            r["matches"][0]["matched_urls"],
            ["https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/"]
        )

if __name__=="__main__":
    unittest.main()
