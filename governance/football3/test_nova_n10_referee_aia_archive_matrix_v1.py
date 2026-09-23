from __future__ import annotations
import datetime as dt
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_archive_matrix_v1 import (
    eligible, parse_arquivo, parse_wayback, query_url, window
)

REG=Path(__file__).with_name("nova_n10_referee_aia_archive_matrix_registry_v1.json")
LED=Path(__file__).with_name("nova_n10_referee_aia_canonical_round_ledger_v1.json")

class T(unittest.TestCase):
    def test_registry_and_ledger(self):
        p=json.loads(REG.read_text())
        l=json.loads(LED.read_text())
        self.assertEqual(p["exact_base"],"c82910ca1da103bd9528c1de58680c07cacdd34e")
        self.assertEqual(l["parent_inventory_sha256"],p["expected_parent_inventory_sha256"])
        self.assertEqual([x["round"] for x in l["rows"]],list(range(1,39)))
        self.assertFalse(p["hard_rules"]["archived_page_content_fetched"])
        self.assertFalse(p["evidence_semantics"]["formal_available_at_proven"])

    def test_window(self):
        s,e=window({"published_date":"2022-10-12"},7)
        self.assertEqual(s,dt.datetime(2022,10,12,tzinfo=dt.timezone.utc))
        self.assertEqual(e,dt.datetime(2022,10,20,tzinfo=dt.timezone.utc))

    def test_wayback_parse_and_eligibility(self):
        raw=json.dumps([
            ["timestamp","original","statuscode","digest","mimetype"],
            ["20221013010000","https://www.aia-figc.it/news/x/","200","GOOD","text/html"],
            ["20221021010000","https://www.aia-figc.it/news/x/","200","LATE","text/html"],
            ["20221013020000","https://evil.example/x","200","EVIL","text/html"]
        ]).encode()
        rows=parse_wayback(raw)
        good=eligible(
            rows,
            dt.datetime(2022,10,12,tzinfo=dt.timezone.utc),
            dt.datetime(2022,10,20,tzinfo=dt.timezone.utc),
            "aia-figc.it"
        )
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["digest"],"GOOD")

    def test_arquivo_parse(self):
        raw=(json.dumps({"url":"https://www.aia-figc.it/news/x/","timestamp":"20221013120000","status":"200","digest":"ARQ","mime":"text/html"})+"\n").encode()
        rows=parse_arquivo(raw)
        self.assertEqual(rows[0]["digest"],"ARQ")
        self.assertEqual(rows[0]["statuscode"],"200")

    def test_query_is_metadata_only(self):
        p=json.loads(REG.read_text())
        s=dt.datetime(2022,10,12,tzinfo=dt.timezone.utc)
        e=dt.datetime(2022,10,20,tzinfo=dt.timezone.utc)
        u=query_url(p["providers"][0]["endpoint"],"https://www.aia-figc.it/news/x/",s,e,"WAYBACK_CDX")
        self.assertIn("output=json",u)
        self.assertIn("statuscode%3A200",u)
        self.assertNotIn("web/2022",u)
        self.assertFalse(p["witness_contract"]["archived_content_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["article_body_read"])

if __name__=="__main__":
    unittest.main()
