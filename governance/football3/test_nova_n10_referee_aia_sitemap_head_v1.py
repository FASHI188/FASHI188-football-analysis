from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_sitemap_head_v1 import (
    parse_robots_sitemaps, parse_sitemap, extract_head_metadata,
    identity_pass, publication_pass, domain_ok
)
REG=Path(__file__).with_name("nova_n10_referee_aia_sitemap_head_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"bcf22301e3b0700e3724a86cccd924e9f5b498cb")
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["old_aia_index_route_repeated"])
        self.assertFalse(p["hard_rules"]["sitemap_lastmod_used_as_publication_proof"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

    def test_robots_only_official(self):
        raw=b"""User-agent: *\nSitemap: https://www.aia-figc.it/sitemap.xml\nSitemap: https://evil.example/x.xml\n"""
        out=parse_robots_sitemaps(raw,"https://www.aia-figc.it/robots.txt","aia-figc.it")
        self.assertEqual(out,["https://www.aia-figc.it/sitemap.xml"])

    def test_sitemap(self):
        raw=b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/</loc><lastmod>2026-01-01</lastmod></url></urlset>'
        kind,locs=parse_sitemap(raw)
        self.assertEqual(kind,"urlset")
        self.assertEqual(len(locs),1)

    def test_head_metadata_and_identity(self):
        head=b'''<html><head>
        <title>SERIE A TIM - Designazioni 10 Giornata</title>
        <link rel="canonical" href="https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/">
        <meta property="article:published_time" content="2022-10-12T10:00:00+02:00">
        </head>'''
        meta=extract_head_metadata(head,"https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/")
        target={
            "slug_term":"serie-a-tim-designazioni-10-giornata-20654",
            "title_terms":["serie a","designazioni","10","giornata"],
            "expected_publication_date":"2022-10-12",
            "safe_cutoff_utc":"2022-10-15T00:00:00Z",
            "publication_floor_utc":"2022-10-12T00:00:00Z"
        }
        self.assertTrue(identity_pass(meta,"https://www.aia-figc.it/news/serie-a-tim-designazioni-10-giornata-20654/",target,"aia-figc.it"))
        ok,sel=publication_pass(meta,target)
        self.assertTrue(ok)
        self.assertEqual(sel["source"],"article:published_time")

    def test_date_only_safe(self):
        meta={"publication_candidates":[{"value":"2022-10-12","precision":"DATE_ONLY","source":"datepublished"}]}
        target={
            "expected_publication_date":"2022-10-12",
            "safe_cutoff_utc":"2022-10-15T00:00:00Z",
            "publication_floor_utc":"2022-10-12T00:00:00Z"
        }
        ok,_=publication_pass(meta,target)
        self.assertTrue(ok)
        self.assertTrue(domain_ok("https://www.aia-figc.it/a","aia-figc.it"))

if __name__=="__main__":
    unittest.main()
