from __future__ import annotations
import datetime as dt
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import nova_n10_referee_sky_deep_pagination_v1 as m

REG=Path(__file__).with_name("nova_n10_referee_sky_deep_pagination_registry_v1.json")

class T(unittest.TestCase):
    def test_registry_partition(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"9d5c164230d250a446e882d0c40bb7198b5dafb1")
        covered=p["parent"]["covered_rounds"]
        gaps=p["parent"]["gap_rounds"]
        self.assertEqual(len(covered),12)
        self.assertEqual(len(gaps),26)
        self.assertEqual(sorted(set(covered+gaps)),list(range(1,39)))
        self.assertEqual(p["source"]["max_archive_pages_per_day"],20)

    def test_hard_zero_label_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["repeat_parent_covered_rounds"])
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["article_body_read"])
        self.assertFalse(h["referee_assignment_body_parsed"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])

    def test_full_integer_pagination_to_advertised_max(self):
        calls=[]
        def fake_fetch(url, **kwargs):
            calls.append(url)
            if "?pag=" not in url:
                html=b'<html><body><a href="/archivio/2022/10/12?pag=2">2</a><a href="/archivio/2022/10/12?pag=8">8</a></body></html>'
            else:
                html=b"<html><body>page</body></html>"
            return html,url,{"content-type":"text/html"}
        source={
            "request_timeout_seconds":1,
            "archive_page_max_bytes":100000,
            "max_archive_pages_per_day":20,
            "domain_suffix":"sport.sky.it",
            "archive_url_template":"https://sport.sky.it/archivio/{yyyy}/{mm}/{dd}",
        }
        with patch.object(m,"fetch_bytes",side_effect=fake_fetch):
            r=m.fetch_archive_day_full(dt.date(2022,10,12),source)
        self.assertFalse(r["pagination_truncated"])
        self.assertEqual(r["max_advertised_page"],8)
        self.assertEqual(r["page_n"],8)
        self.assertEqual(len(calls),8)

    def test_pagination_truncation_at_cap(self):
        calls=[]
        def fake_fetch(url, **kwargs):
            calls.append(url)
            if "?pag=" not in url:
                html=b'<html><body><a href="/archivio/2022/10/12?pag=25">25</a></body></html>'
            else:
                html=b"<html><body>page</body></html>"
            return html,url,{"content-type":"text/html"}
        source={
            "request_timeout_seconds":1,
            "archive_page_max_bytes":100000,
            "max_archive_pages_per_day":20,
            "domain_suffix":"sport.sky.it",
            "archive_url_template":"https://sport.sky.it/archivio/{yyyy}/{mm}/{dd}",
        }
        with patch.object(m,"fetch_bytes",side_effect=fake_fetch):
            r=m.fetch_archive_day_full(dt.date(2022,10,12),source)
        self.assertTrue(r["pagination_truncated"])
        self.assertEqual(r["max_advertised_page"],25)
        self.assertEqual(r["page_n"],20)
        self.assertEqual(len(calls),20)

    def test_parent_provenance(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["parent"]["pr"],473)
        self.assertEqual(p["parent"]["run"],35878227811)
        self.assertEqual(p["parent"]["inventory_sha256"],"d28647fdafbd9d5b4908d162928816a457be28aa8ff5f0a9ff9cd058540d05c3")
        self.assertEqual(p["discovery_contract"]["day_offsets"],[0,1,2])
        self.assertTrue(p["pagination_contract"]["fetch_all_integer_pages_1_through_max_advertised"])

if __name__=="__main__":
    unittest.main()
