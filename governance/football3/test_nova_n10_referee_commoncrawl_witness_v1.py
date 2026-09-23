from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_commoncrawl_witness_v1 import (
    build_query, collection_intersects, eligible, official_domain_ok,
    parse_cdxj, select_collections
)

REG = Path(__file__).with_name("nova_n10_referee_commoncrawl_witness_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"01ed3ebf13b26f00e0bdc856ae3ab53ab6a38401")
        self.assertEqual(p["target"]["competition"],"Serie_A")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])
        self.assertFalse(p["hard_rules"]["warc_content_fetch_allowed"])
        self.assertEqual(p["hard_rules"]["candidate_weight"],0)
        self.assertEqual(p["hard_rules"]["matrix_delta"],0)

    def test_collection_intersection(self):
        row={"from":"2022-10-10T00:00:00Z","to":"2022-10-13T23:59:59Z"}
        self.assertTrue(collection_intersects(row,"2022-10-12T00:00:00Z","2022-10-15T00:00:00Z"))
        row2={"from":"2022-10-15T00:00:00Z","to":"2022-10-20T00:00:00Z"}
        self.assertFalse(collection_intersects(row2,"2022-10-12T00:00:00Z","2022-10-15T00:00:00Z"))

    def test_select_collections_host_and_window(self):
        rows=[
            {"id":"A","cdx-api":"https://index.commoncrawl.org/A-index","from":"2022-10-10T00:00:00Z","to":"2022-10-13T23:59:59Z"},
            {"id":"B","cdx-api":"https://evil.example/B-index","from":"2022-10-10T00:00:00Z","to":"2022-10-13T23:59:59Z"},
            {"id":"C","cdx-api":"https://index.commoncrawl.org/C-index","from":"2022-11-01T00:00:00Z","to":"2022-11-10T00:00:00Z"},
        ]
        out=select_collections(rows,"2022-10-12T00:00:00Z","2022-10-15T00:00:00Z","index.commoncrawl.org")
        self.assertEqual([x["id"] for x in out],["A"])

    def test_parse_and_eligible(self):
        raw=(
            '{"timestamp":"20221012120000","url":"https://www.aia-figc.it/news/x","status":"200","digest":"OK","mime":"text/html"}\n'
            '{"timestamp":"20221011120000","url":"https://www.aia-figc.it/news/x","status":"200","digest":"EARLY","mime":"text/html"}\n'
            '{"timestamp":"20221015120000","url":"https://www.aia-figc.it/news/x","status":"200","digest":"LATE","mime":"text/html"}\n'
            '{"timestamp":"20221012130000","url":"https://evil.example/news/x","status":"200","digest":"EVIL","mime":"text/html"}\n'
        ).encode()
        rows=parse_cdxj(raw)
        target={
            "official_publication_floor_utc":"2022-10-12T00:00:00Z",
            "safe_cutoff_utc":"2022-10-15T00:00:00Z",
            "official_domain":"aia-figc.it",
        }
        good=eligible(rows,target)
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["digest"],"OK")

    def test_domain_and_query(self):
        self.assertTrue(official_domain_ok("https://www.aia-figc.it/a","aia-figc.it"))
        self.assertFalse(official_domain_ok("https://aia-figc.it.evil.example/a","aia-figc.it"))
        q=build_query("https://index.commoncrawl.org/CC-MAIN-2022-40-index","https://www.aia-figc.it/news/x")
        self.assertIn("output=json",q)
        self.assertIn("filter=status%3A200",q)

if __name__=="__main__":
    unittest.main()
