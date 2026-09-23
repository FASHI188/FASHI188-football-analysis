from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_memento_feasibility_v1 import (
    parse_timemap, eligible_records, excluded_provider, timemap_url, host_is
)

REG=Path(__file__).with_name("nova_n10_referee_aia_memento_feasibility_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"8e0561b74ca960b5828b6c43b84f06043599eac0")
        self.assertEqual(p["parent"]["canonical_ledger_sha256"],"0be7e00db3f370808aa8d7caab69bc24c7d9b117efab3f6dccd2fcfebb885a9b")
        self.assertEqual(len(p["samples"]),5)
        self.assertFalse(p["hard_rules"]["archived_page_content_fetched"])
        self.assertFalse(p["hard_rules"]["provider_reuse_wayback_or_arquivo_as_new_signal"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_parse_timemap_recursive(self):
        raw=json.dumps({
            "original_uri":"https://example.test/x",
            "mementos":{
                "list":[
                    {"datetime":"Fri, 12 Aug 2022 10:00:00 GMT","uri":"https://webarchive.loc.gov/all/20220812100000/https://example.test/x"},
                    {"datetime":"Sat, 13 Aug 2022 10:00:00 GMT","uri":["https://web.archive.org/web/20220813100000/https://example.test/x"]}
                ]
            }
        }).encode()
        rows=parse_timemap(raw)
        self.assertEqual(len(rows),2)

    def test_excluded_provider(self):
        self.assertTrue(excluded_provider("web.archive.org",["web.archive.org","arquivo.pt"]))
        self.assertTrue(excluded_provider("foo.arquivo.pt",["web.archive.org","arquivo.pt"]))
        self.assertFalse(excluded_provider("webarchive.loc.gov",["web.archive.org","arquivo.pt"]))

    def test_eligible_window_and_provider(self):
        rows=[
            {"datetime":"Fri, 12 Aug 2022 10:00:00 GMT","uri":"https://webarchive.loc.gov/all/20220812100000/https://example.test/x"},
            {"datetime":"Sat, 13 Aug 2022 10:00:00 GMT","uri":"https://web.archive.org/web/20220813100000/https://example.test/x"},
            {"datetime":"Sun, 21 Aug 2022 10:00:00 GMT","uri":"https://webarchive.loc.gov/all/20220821100000/https://example.test/x"}
        ]
        sample={"published_date":"2022-08-10"}
        out=eligible_records(rows,sample,["web.archive.org","arquivo.pt"])
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["provider_host"],"webarchive.loc.gov")

    def test_url_and_host(self):
        u=timemap_url("https://timetravel.mementoweb.org/timemap/json/{original_url}","https://www.aia-figc.it/news/x/")
        self.assertIn("timetravel.mementoweb.org/timemap/json/https://www.aia-figc.it/news/x/",u)
        self.assertTrue(host_is("https://timetravel.mementoweb.org/a","timetravel.mementoweb.org"))
        self.assertFalse(host_is("https://evil.example/a","timetravel.mementoweb.org"))

if __name__=="__main__":
    unittest.main()
