from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_dapi_discovery_v1 import host_ok, html_title, schema_paths
REG=Path(__file__).with_name("nova_n10_referee_lega_dapi_discovery_registry_v1.json")
class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"69fa131dc2429b584cf88f93574b0d9284735d0a")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["match_payload_read"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
    def test_host(self):
        self.assertTrue(host_ok("https://dapi.legaseriea.it/docs","dapi.legaseriea.it"))
        self.assertFalse(host_ok("https://evil.example/docs","dapi.legaseriea.it"))
    def test_schema_filter(self):
        raw=json.dumps({"paths":{"/content/articles":{},"/news/search":{},"/matches":{},"/players/stats":{}}}).encode()
        pref,forbid=schema_paths(raw)
        self.assertEqual(pref,["/content/articles","/news/search"])
        self.assertEqual(set(forbid),{"/matches","/players/stats"})
    def test_title(self):
        self.assertEqual(html_title(b"<html><title> Distribution API Docs </title></html>"),"Distribution API Docs")
if __name__=="__main__": unittest.main()
