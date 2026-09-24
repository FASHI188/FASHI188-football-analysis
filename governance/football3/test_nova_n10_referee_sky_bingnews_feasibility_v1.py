from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_bingnews_feasibility_v1 import (
    UTC,
    build_query_url,
    candidate_time_title,
    classify,
    extract_publisher_url,
    parse_feed,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_bingnews_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"cb858ada17e22eaab415941ec4080028f88e41db")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertFalse(p["hard_rules"]["bing_article_body_read"])
        self.assertFalse(p["hard_rules"]["sky_article_body_read"])
        self.assertFalse(p["hard_rules"]["rss_description_html_used_for_identity"])
        self.assertFalse(p["decision_contract"]["rss_pubdate_counts_as_formal_observed_at"])

    def test_query(self):
        p=json.loads(REG.read_text())
        u=build_query_url(
            p["source"]["endpoint"],
            "Serie A - arbitri e designazioni 8 giornata",
            p["source"]["query_params"],
        )
        self.assertIn("www.bing.com/news/search?",u)
        self.assertIn("site%3Asport.sky.it",u)
        self.assertIn("format=rss",u)
        self.assertIn("setlang=it-it",u)

    def test_parse_feed_ignores_description(self):
        raw=b'''<rss><channel><item>
        <title>Serie A: arbitri e designazioni 8 giornata - Sky Sport</title>
        <link>https://www.bing.com/news/apiclick.aspx?url=https%3A%2F%2Fsport.sky.it%2Fx</link>
        <guid>g1</guid><pubDate>Thu, 29 Sep 2022 12:00:00 GMT</pubDate>
        <description>DO NOT USE</description><Source>Sky Sport</Source>
        </item></channel></rss>'''
        items=parse_feed(raw,100)
        self.assertEqual(len(items),1)
        self.assertNotIn("description",items[0])
        self.assertEqual(items[0]["source_text"],"Sky Sport")

    def test_extract_embedded_publisher_url(self):
        p=json.loads(REG.read_text())
        target="https://sport.sky.it/calcio/serie-a/x"
        link="https://www.bing.com/news/apiclick.aspx?ref=FexRss&url=https%3A%2F%2Fsport.sky.it%2Fcalcio%2Fserie-a%2Fx"
        u,m=extract_publisher_url(link,p["identity_contract"])
        self.assertEqual(u,target)
        self.assertEqual(m,"QUERY_PARAM:url")

    def test_candidate_time_title(self):
        item={
            "title":"Serie A: arbitri e designazioni 8 giornata - Sky Sport",
            "pubDate":"Thu, 29 Sep 2022 12:00:00 GMT",
        }
        lower=dt.datetime(2022,9,29,11,55,tzinfo=UTC)
        upper=dt.datetime(2022,10,1,13,0,tzinfo=UTC)
        ok,meta=candidate_time_title(item,8,lower,upper)
        self.assertTrue(ok)
        self.assertTrue(meta["title_ok"])
        self.assertTrue(meta["pubdate_ok"])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,0,1,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,1,0,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=classify(0,0,2,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_metadata_candidates_but_identity_unresolved"])
        c4,n4=classify(0,0,0,p)
        self.assertEqual(c4,"STOP_DATA_COVERAGE")
        self.assertEqual(n4,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
