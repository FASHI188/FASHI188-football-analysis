from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_dapi_content_probe_v1 import host_ok
REG=Path(__file__).with_name("nova_n10_referee_lega_dapi_content_probe_registry_v1.json")
class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"78b6694b40c45f5aeaf18afcaf8ca8513908e2fc")
        self.assertEqual(len(p["candidates"]),14)
        self.assertEqual(p["hard_rules"]["method"],"HEAD")
        self.assertFalse(p["hard_rules"]["response_body_read"])
        self.assertFalse(p["hard_rules"]["match_payload_read"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
    def test_host(self):
        self.assertTrue(host_ok("https://dapi.legaseriea.it/v2/content/en-gb/news","dapi.legaseriea.it"))
        self.assertFalse(host_ok("https://evil.example/news","dapi.legaseriea.it"))
if __name__=="__main__": unittest.main()
