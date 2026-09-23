from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_urlscan_feasibility_v1 import (
    build_query, date_query_bounds, exact_url, eligible_results, safe_result_metadata
)
REG=Path(__file__).with_name("nova_n10_referee_aia_urlscan_feasibility_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"3364c9c30dc1480aef8555c64e348b78792b9560")
        self.assertEqual(len(p["samples"]),5)
        self.assertFalse(p["source"]["api_key_allowed"])
        self.assertFalse(p["source"]["scan_submission_allowed"])
        self.assertFalse(p["hard_rules"]["scan_content_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])

    def test_bounds_and_query(self):
        start,end,s,e=date_query_bounds("2022-10-12")
        self.assertEqual(start,"2022-10-12T00:00:00.000Z")
        self.assertEqual(end,"2022-10-19T23:59:59.999Z")
        q=build_query("https://urlscan.io/api/v1/search/","2022-10-12",100)
        self.assertIn("page.domain%3Aaia-figc.it",q)
        self.assertIn("size=100",q)

    def test_exact_url(self):
        self.assertTrue(exact_url("https://www.aia-figc.it/news/x","https://www.aia-figc.it/news/x/"))
        self.assertFalse(exact_url("https://www.aia-figc.it/news/y","https://www.aia-figc.it/news/x/"))

    def test_safe_metadata(self):
        raw={"_id":"abc","task":{"time":"2022-10-13T12:00:00.000Z","url":"https://www.aia-figc.it/news/x/","extra":"ignore"},"page":{"url":"https://www.aia-figc.it/news/x/","title":"ignore"},"data":"ignore"}
        m=safe_result_metadata(raw)
        self.assertEqual(set(m),{"scan_id","scan_time","task_url","page_url"})
        self.assertEqual(m["scan_id"],"abc")

    def test_eligible(self):
        sample={"published_date":"2022-10-12","url":"https://www.aia-figc.it/news/x/"}
        rows=[
            {"_id":"a","task":{"time":"2022-10-13T12:00:00Z","url":"https://www.aia-figc.it/news/x/"},"page":{"url":"https://www.aia-figc.it/news/x/"}},
            {"_id":"b","task":{"time":"2022-10-21T12:00:00Z","url":"https://www.aia-figc.it/news/x/"},"page":{"url":"https://www.aia-figc.it/news/x/"}},
            {"_id":"c","task":{"time":"2022-10-14T12:00:00Z","url":"https://www.aia-figc.it/news/y/"},"page":{"url":"https://www.aia-figc.it/news/y/"}}
        ]
        out=eligible_results(rows,sample)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["scan_id"],"a")

if __name__=="__main__":
    unittest.main()
