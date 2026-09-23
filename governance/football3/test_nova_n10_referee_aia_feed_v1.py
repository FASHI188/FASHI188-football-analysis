from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_feed_v1 import (
    discover_feed_links, parse_feed, target_match, target_date_match, domain_ok
)
REG=Path(__file__).with_name("nova_n10_referee_aia_feed_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"55bf455f9330f0a85e96bd5d49ffb270d69c5578")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["feed_item_body_read"])
        self.assertFalse(p["hard_rules"]["hidden_endpoint_guessing_allowed"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_feed_link_discovery(self):
        head=b"""<html><head>
        <link rel="alternate" type="application/rss+xml" href="/feed.xml" title="Feed">
        <link rel="alternate" type="text/html" href="/not-feed">
        <link rel="alternate" type="application/rss+xml" href="https://evil.example/feed.xml">
        </head>"""
        out=discover_feed_links(head,"https://www.aia-figc.it/news/","aia-figc.it",["application/rss+xml","application/atom+xml"])
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["url"],"https://www.aia-figc.it/feed.xml")

    def test_parse_rss_metadata_only(self):
        raw=b"""<?xml version="1.0"?><rss><channel><item>
        <title>SERIE A TIM - Designazioni 10 Giornata</title>
        <link>https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/</link>
        <pubDate>Wed, 12 Oct 2022 10:00:00 +0200</pubDate>
        <guid>20654</guid>
        <description>BODY MUST NOT BE EXPOSED</description>
        </item></channel></rss>"""
        kind,items=parse_feed(raw,"application/rss+xml")
        self.assertEqual(kind,"rss")
        self.assertEqual(len(items),1)
        self.assertEqual(set(items[0]),{"title","link","published_at","updated_at","guid"})
        self.assertNotIn("BODY MUST NOT BE EXPOSED",json.dumps(items[0]))

    def test_target_match(self):
        target={"frozen_title_terms":["serie a","designazioni","10","giornata"],"target_slug_term":"serie-a-tim-designazioni-10-giornata-20654"}
        item={"title":"SERIE A TIM - Designazioni 10 Giornata","link":"https://www.aia-figc.it/news/x","published_at":"2022-10-12T08:00:00+00:00","updated_at":None}
        self.assertTrue(target_match(item,target))
        self.assertTrue(target_date_match(item,"2022-10-12"))
        bad={"title":"Serie A 11 giornata","link":"https://www.aia-figc.it/news/y","published_at":"2022-10-12T08:00:00+00:00","updated_at":None}
        self.assertFalse(target_match(bad,target))

    def test_domain(self):
        self.assertTrue(domain_ok("https://www.aia-figc.it/feed.xml","aia-figc.it"))
        self.assertFalse(domain_ok("https://aia-figc.it.evil.example/feed.xml","aia-figc.it"))

if __name__=="__main__":
    unittest.main()
