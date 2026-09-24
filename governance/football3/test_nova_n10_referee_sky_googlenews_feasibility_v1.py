from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_googlenews_feasibility_v1 import (
    UTC,
    build_query_url,
    candidate_filter,
    classify,
    parse_feed,
    parse_rfc822,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_googlenews_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"fb9212951b5f55603d46b7f67b40f84f41ca0e1f")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertFalse(p["hard_rules"]["google_article_body_read"])
        self.assertFalse(p["hard_rules"]["sky_article_body_read"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["decision_contract"]["rss_pubdate_counts_as_formal_observed_at"])

    def test_query_bounds(self):
        p=json.loads(REG.read_text())
        u=build_query_url(
            p["source"]["endpoint"],
            "Serie A - Arbitri e designazioni 8 giornata",
            "2022-09-29T11:00:00Z",
            "2022-10-01T13:00:00Z",
            p["source"]["locale_params"],
            p["query_contract"]["query_after_publication_day_offset"],
            p["query_contract"]["query_before_cutoff_day_offset"],
        )
        self.assertIn("news.google.com/rss/search?",u)
        self.assertIn("site%3Asport.sky.it",u)
        self.assertIn("after%3A2022-09-28",u)
        self.assertIn("before%3A2022-10-02",u)

    def test_parse_feed_metadata_only(self):
        raw=b'''<?xml version="1.0"?><rss><channel><item>
        <title>Serie A: arbitri e designazioni 8 giornata - Sky Sport</title>
        <link>https://news.google.com/rss/articles/x</link>
        <guid>g1</guid><pubDate>Thu, 29 Sep 2022 12:00:00 GMT</pubDate>
        <description>DO NOT USE THIS HTML</description>
        <source url="https://sport.sky.it">Sky Sport</source>
        </item></channel></rss>'''
        items=parse_feed(raw,100)
        self.assertEqual(len(items),1)
        self.assertNotIn("description",items[0])
        self.assertEqual(items[0]["source_text"],"Sky Sport")

    def test_rfc822_and_candidate(self):
        p=json.loads(REG.read_text())
        item={
            "title":"Serie A: arbitri e designazioni 8 giornata - Sky Sport",
            "source_text":"Sky Sport",
            "source_url":"https://sport.sky.it",
            "pubDate":"Thu, 29 Sep 2022 12:00:00 GMT",
        }
        lower=dt.datetime(2022,9,29,11,55,tzinfo=UTC)
        upper=dt.datetime(2022,10,1,13,0,tzinfo=UTC)
        ok,meta=candidate_filter(item,8,lower,upper,p["query_contract"])
        self.assertTrue(ok)
        self.assertTrue(meta["title_ok"])
        self.assertEqual(parse_rfc822(item["pubDate"]),dt.datetime(2022,9,29,12,0,tzinfo=UTC))

    def test_candidate_wrong_source_fails(self):
        p=json.loads(REG.read_text())
        item={
            "title":"Serie A: arbitri e designazioni 8 giornata - Sky Sport",
            "source_text":"Other",
            "source_url":"https://example.test",
            "pubDate":"Thu, 29 Sep 2022 12:00:00 GMT",
        }
        lower=dt.datetime(2022,9,29,11,55,tzinfo=UTC)
        upper=dt.datetime(2022,10,1,13,0,tzinfo=UTC)
        ok,_=candidate_filter(item,8,lower,upper,p["query_contract"])
        self.assertFalse(ok)

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,0,1,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_exact_identity_positive"])
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
