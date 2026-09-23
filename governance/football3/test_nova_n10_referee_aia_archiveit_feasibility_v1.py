from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_archiveit_feasibility_v1 import (
    build_query, date_bounds, parse_cdx, in_window, host
)

REG=Path(__file__).with_name("nova_n10_referee_aia_archiveit_feasibility_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"ace6bb4d6db5c332406d759d31a5ac25afe8b132")
        self.assertEqual(p["parent"]["canonical_ledger_sha256"],"0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b")
        self.assertEqual(len(p["samples"]),5)
        self.assertFalse(p["hard_rules"]["archive_content_fetched"])
        self.assertFalse(p["hard_rules"]["collection_id_guessing_allowed"])
        self.assertFalse(p["hard_rules"]["training_allowed"])

    def test_bounds(self):
        frm,to=date_bounds("2022-10-12")
        self.assertEqual(frm,"20221012000000")
        self.assertEqual(to,"20221019235959")
        self.assertTrue(in_window("20221015000000","2022-10-12"))
        self.assertFalse(in_window("20221020000000","2022-10-12"))

    def test_query(self):
        q=build_query("https://wayback.archive-it.org/all/timemap/cdx","https://www.aia-figc.it/news/x/","2022-10-12")
        self.assertIn("url=https%3A%2F%2Fwww.aia-figc.it%2Fnews%2Fx%2F",q)
        self.assertIn("from=20221012000000",q)
        self.assertIn("to=20221019235959",q)
        self.assertIn("filter=statuscode%3A200",q)

    def test_parse_cdx(self):
        raw=b"20221012120000 https://www.aia-figc.it/news/x/ 200\n20221013120000 https://www.aia-figc.it/news/x/ 404\n"
        rows=parse_cdx(raw)
        self.assertEqual(rows,[{"timestamp":"20221012120000","original":"https://www.aia-figc.it/news/x/","statuscode":"200"}])

    def test_host(self):
        self.assertEqual(host("https://wayback.archive-it.org/all/timemap/cdx"),"wayback.archive-it.org")

if __name__=="__main__":
    unittest.main()
