from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_lega_sitemap_v1 import (
    candidate_url, domain_ok, normalize_datetime, parse_sitemap, robots_sitemaps
)
REG=Path(__file__).with_name("nova_n10_referee_lega_sitemap_registry_v1.json")

class T(unittest.TestCase):
    def test_registry(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"7c11355623d61391ba8b205beb2772f9ff249046")
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["match_payload_read"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["publication_contract"]["sitemap_lastmod_is_publication_proof"])

    def test_robots(self):
        raw=b"User-agent: *\nSitemap: https://www.legaseriea.it/sitemap.xml\nSitemap: https://evil.example/sitemap.xml\n"
        self.assertEqual(
            robots_sitemaps(raw,"https://www.legaseriea.it/robots.txt","legaseriea.it"),
            ["https://www.legaseriea.it/sitemap.xml"]
        )

    def test_sitemap(self):
        raw=b'''<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
        <url><loc>https://www.legaseriea.it/serie-a/news/le-designazioni-arbitrali-della-10a</loc><lastmod>2022-10-12</lastmod></url>
        </urlset>'''
        kind,rows=parse_sitemap(raw)
        self.assertEqual(kind,"urlset")
        self.assertEqual(rows[0]["lastmod"],"2022-10-12")

    def test_candidate(self):
        self.assertTrue(candidate_url(
            "https://www.legaseriea.it/serie-a/news/le-designazioni-arbitrali-della-10a",
            ["designazioni","arbitri","arbitrali"],["serie-a","news"]
        ))
        self.assertFalse(candidate_url(
            "https://www.legaseriea.it/serie-a/news/milan-inter",
            ["designazioni","arbitri","arbitrali"],["serie-a","news"]
        ))

    def test_datetime_and_domain(self):
        self.assertEqual(normalize_datetime("2022-10-12T08:01:00+02:00"),"2022-10-12T06:01:00+00:00")
        self.assertTrue(domain_ok("https://www.legaseriea.it/a","legaseriea.it"))
        self.assertFalse(domain_ok("https://legaseriea.it.evil.example/a","legaseriea.it"))

if __name__=="__main__": unittest.main()
