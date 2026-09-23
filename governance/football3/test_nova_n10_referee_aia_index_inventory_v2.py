from __future__ import annotations
import json, unittest
from pathlib import Path
from nova_n10_referee_aia_index_inventory_v2 import (
    discover_pagination, extract_target_cards, parse_round,
    same_category_pagination, title_allowed
)

REG=Path(__file__).with_name("nova_n10_referee_aia_index_inventory_registry_v2.json")

class T(unittest.TestCase):
    def test_registry_contract(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"49d3e99d18f98803cef43fe79fc2e0bf738a9a59")
        self.assertTrue(p["crawl_contract"]["mechanically_discovered_pagination_only"])
        self.assertFalse(p["crawl_contract"]["blind_page_number_generation"])
        self.assertFalse(p["crawl_contract"]["article_body_fetch_allowed"])
        self.assertFalse(p["hard_rules"]["article_snippet_persisted"])
        self.assertFalse(p["hard_rules"]["appointment_names_parsed"])
        self.assertFalse(p["date_contract"]["formal_available_at_proven"])

    def test_pagination_mechanical(self):
        base="https://www.aia-figc.it/news/?c=9"
        self.assertEqual(same_category_pagination("/news/?c=9&p=2",base,"aia-figc.it","c","9","p"),2)
        self.assertIsNone(same_category_pagination("/news/?c=8&p=2",base,"aia-figc.it","c","9","p"))
        self.assertIsNone(same_category_pagination("https://evil.example/news/?c=9&p=2",base,"aia-figc.it","c","9","p"))
        raw=b'<a href="/news/?c=9&p=3">3</a><a href="/news/?p=2&0&c=9">2</a><a href="/news/?c=8&p=4">x</a>'
        self.assertEqual(discover_pagination(raw,base,"aia-figc.it","c","9","p"),[
            "https://www.aia-figc.it/news/?p=2&c=9",
            "https://www.aia-figc.it/news/?p=3&c=9"
        ])

    def test_title_and_round_filter(self):
        req=["serie a","designazion"]
        exc=["serie b","serie c","femminile","primavera"]
        self.assertTrue(title_allowed("SERIE A TIM - Designazioni 10ª Giornata",req,exc))
        self.assertFalse(title_allowed("SERIE A FEMMINILE - Designazioni 10ª Giornata",req,exc))
        p=json.loads(REG.read_text())
        self.assertEqual(parse_round("SERIE A TIM - Designazioni 10ª Giornata",p["target"]["round_patterns"]),10)
        self.assertEqual(parse_round("SERIE A TIM - DESIGNAZIONI 16a GIORNATA",p["target"]["round_patterns"]),16)

    def test_card_metadata_only(self):
        raw='''<html><body>
        <article><span>12/10/2022</span><a href="/news/serie-a-tim-designazioni-10-giornata-20654/">SERIE A TIM - Designazioni 10ª Giornata</a>
        <p>snippet with appointment names MUST NOT BE PERSISTED</p></article>
        <article><span>13/10/2022</span><a href="/news/serie-b-designazioni-9-giornata/">SERIE B - Designazioni 9 Giornata</a></article>
        </body></html>'''.encode()
        target={
            "required_title_terms":["serie a","designazion"],
            "excluded_title_terms":["serie b","serie c","serie d","femminile","primavera","calcio a 5","futsal"],
            "round_patterns":[r"(?:designazioni\s*)?(\d{1,2})\s*(?:ª|a)?\s*giornata",r"giornata\s*(\d{1,2})"]
        }
        rows=extract_target_cards(raw,"https://www.aia-figc.it/news/?c=9","aia-figc.it",target,"PAGEHASH")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["published_date"],"2022-10-12")
        self.assertEqual(rows[0]["round"],10)
        self.assertNotIn("snippet",json.dumps(rows))

    def test_no_article_content_fields(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["crawl_contract"]["retained_fields"],[
            "title","published_date","link","round","source_page","source_page_sha256"
        ])
        self.assertFalse(p["crawl_contract"]["snippet_persisted"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])

if __name__=="__main__":
    unittest.main()
