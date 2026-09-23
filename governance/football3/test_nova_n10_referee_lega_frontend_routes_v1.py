from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_frontend_routes_v1 import domain_ok, extract_routes, script_sources, select_usable_routes
REG=Path(__file__).with_name("nova_n10_referee_lega_frontend_routes_registry_v1.json")
class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"78b6694b40c45f5aeaf18afcaf8ca8513908e2fc")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["match_payload_read"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["hidden_endpoint_bruteforce_allowed"])
    def test_domain(self):
        self.assertTrue(domain_ok("https://www.legaseriea.it/a","legaseriea.it"))
        self.assertFalse(domain_ok("https://evil.example/a","legaseriea.it"))
    def test_scripts(self):
        h=b'<html><head><script src="/_next/static/a.js"></script><script src="https://evil.example/x.js"></script></head>'
        self.assertEqual(script_sources(h,"https://www.legaseriea.it/x","legaseriea.it"),["https://www.legaseriea.it/_next/static/a.js"])
    def test_route_filter(self):
        raw=b'"https://dapi.legaseriea.it/content/news" "/api/articles/search" "/matches/results"'
        out=extract_routes(raw,["dapi.legaseriea.it","content","news","article","search"],["match","result","score","player","stat"])
        self.assertIn("https://dapi.legaseriea.it/content/news",out)
        self.assertIn("/api/articles/search",out)
        self.assertNotIn("/matches/results",out)
    def test_usable_fail_close(self):
        raw=["/search","https://nextjs.org/docs/app/api-reference/functions/use-search-params","/content/articles","https://dapi.legaseriea.it/publication/list"]
        out=select_usable_routes(raw,"legaseriea.it","dapi.legaseriea.it")
        self.assertNotIn("/search",out)
        self.assertFalse(any("nextjs.org" in x for x in out))
        self.assertIn("/content/articles",out)
        self.assertIn("https://dapi.legaseriea.it/publication/list",out)
if __name__=="__main__": unittest.main()
