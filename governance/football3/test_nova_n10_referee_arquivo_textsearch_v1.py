from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_arquivo_textsearch_v1 import (
    build_query, normalize_candidate, response_items, title_identity_ok
)
REG=Path(__file__).with_name("nova_n10_referee_arquivo_textsearch_registry_v1.json")
class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"4a1fc3ace3e51ea7d967fb0f07977ff5af546a38")
        self.assertFalse(p["hard_rules"]["archive_content_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
    def test_title(self):
        self.assertTrue(title_identity_ok("SERIE A TIM - Designazioni 10ª Giornata"))
        self.assertFalse(title_identity_ok("SERIE BKT - Designazioni 10ª Giornata"))
    def test_items(self):
        self.assertEqual(response_items({"response_items":[{"x":1}]}),[{"x":1}])
        self.assertEqual(response_items({"x":[]}),[])
    def test_candidate(self):
        target={"official_domain":"aia-figc.it","official_publication_floor_utc":"2022-10-12T00:00:00Z","safe_cutoff_utc":"2022-10-15T00:00:00Z"}
        item={"title":"SERIE A TIM - Designazioni 10ª Giornata","originalURL":"https://www.aia-figc.it/news/x","timestamp":"20221012120000","digest":"D"}
        self.assertIsNotNone(normalize_candidate(item,target))
        item["timestamp"]="20221015120000"
        self.assertIsNone(normalize_candidate(item,target))
    def test_query(self):
        q=build_query("https://arquivo.pt/textsearch","\"SERIE A TIM\" site:aia-figc.it","2022-10-12T00:00:00Z","2022-10-15T00:00:00Z",50)
        self.assertIn("from=20221012000000",q)
        self.assertIn("to=20221014235959",q)
if __name__=="__main__": unittest.main()
