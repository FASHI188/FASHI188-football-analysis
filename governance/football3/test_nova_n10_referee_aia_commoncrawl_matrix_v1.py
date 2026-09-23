from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_commoncrawl_matrix_v1 import (
    collection_intersects, select_collections, parse_cdxj, eligible, window
)

REG=Path(__file__).with_name("nova_n10_referee_aia_commoncrawl_matrix_registry_v1.json")
LEDGER=Path(__file__).with_name("nova_n10_referee_aia_canonical_round_ledger_v1.json")

class T(unittest.TestCase):
    def test_registry_and_ledger(self):
        p=json.loads(REG.read_text())
        l=json.loads(LEDGER.read_text())
        self.assertEqual(p["exact_base"],"2000c097f866e78379ab0e8c1e29e50ee452bebf")
        self.assertEqual(l["canonical_round_n"],38)
        self.assertEqual([x["round"] for x in l["rows"]],list(range(1,39)))
        self.assertFalse(p["hard_rules"]["warc_content_fetched"])
        self.assertFalse(p["hard_rules"]["training_allowed"])

    def test_collection_intersection(self):
        c={"from":"2022-10-10T00:00:00Z","to":"2022-10-18T23:59:59Z"}
        import datetime as dt
        s=dt.datetime(2022,10,12,tzinfo=dt.timezone.utc)
        e=dt.datetime(2022,10,20,tzinfo=dt.timezone.utc)
        self.assertTrue(collection_intersects(c,s,e))
        c2={"from":"2022-10-20T00:00:00Z","to":"2022-10-30T00:00:00Z"}
        self.assertFalse(collection_intersects(c2,s,e))

    def test_select_collections_host(self):
        import datetime as dt
        rows=[
            {"id":"A","cdx-api":"https://index.commoncrawl.org/A-index","from":"2022-10-10T00:00:00Z","to":"2022-10-18T00:00:00Z"},
            {"id":"B","cdx-api":"https://evil.example/B-index","from":"2022-10-10T00:00:00Z","to":"2022-10-18T00:00:00Z"},
        ]
        out=select_collections(rows,dt.datetime(2022,10,12,tzinfo=dt.timezone.utc),dt.datetime(2022,10,20,tzinfo=dt.timezone.utc),"index.commoncrawl.org")
        self.assertEqual([x["id"] for x in out],["A"])

    def test_parse_and_eligible(self):
        raw=(
          '{"timestamp":"20221013120000","url":"https://www.aia-figc.it/news/x/","status":"200","digest":"A","mime":"text/html"}\n'
          '{"timestamp":"20221021120000","url":"https://www.aia-figc.it/news/x/","status":"200","digest":"B","mime":"text/html"}\n'
        ).encode()
        rows=parse_cdxj(raw)
        import datetime as dt
        good=eligible(rows,"https://www.aia-figc.it/news/x/",dt.datetime(2022,10,12,tzinfo=dt.timezone.utc),dt.datetime(2022,10,20,tzinfo=dt.timezone.utc))
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["digest"],"A")

    def test_window(self):
        import datetime as dt
        s,e=window({"published_date":"2022-10-12"},8)
        self.assertEqual(s,dt.datetime(2022,10,12,tzinfo=dt.timezone.utc))
        self.assertEqual(e,dt.datetime(2022,10,20,tzinfo=dt.timezone.utc))

if __name__=="__main__":
    unittest.main()
