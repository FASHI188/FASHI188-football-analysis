from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_reddit_feasibility_v1 import (
    UTC,
    build_query,
    classify,
    evaluate_post,
    safe_posts,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_reddit_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"ff9374580450b26c632a7e14e9057a361f76b6f7")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["query_contract"]["variants"],["url_exact_operator","quoted_exact_url"])
        self.assertFalse(p["hard_rules"]["selftext_read"])
        self.assertFalse(p["hard_rules"]["comments_read"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])

    def test_build_query_variants(self):
        p=json.loads(REG.read_text())
        target="https://sport.sky.it/calcio/serie-a/x"
        a=build_query(p["source"]["endpoints"][0],"url_exact_operator",target,p["source"]["query_params"])
        b=build_query(p["source"]["endpoints"][0],"quoted_exact_url",target,p["source"]["query_params"])
        self.assertIn("q=url%3Ahttps%3A%2F%2Fsport.sky.it",a)
        self.assertIn("%22https%3A%2F%2Fsport.sky.it",b)
        self.assertIn("sort=new",a)
        self.assertIn("t=all",a)

    def test_safe_posts_excludes_body(self):
        raw=json.dumps({
            "data":{"children":[{"data":{
                "id":"abc","created_utc":1664452800,"subreddit":"soccer",
                "permalink":"/r/soccer/x","url":"https://sport.sky.it/x",
                "url_overridden_by_dest":"https://sport.sky.it/x",
                "title":"DO NOT PERSIST TITLE","selftext":"DO NOT READ BODY"
            }}]}
        }).encode()
        posts=safe_posts(raw)
        self.assertEqual(len(posts),1)
        self.assertNotIn("title",posts[0])
        self.assertNotIn("selftext",posts[0])

    def test_evaluate_exact_identity_and_pit(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        post={
            "id":"abc","created_utc":1664452800,"subreddit":"soccer",
            "permalink":"/r/soccer/x","url":target,"url_overridden_by_dest":target,
        }
        lower=dt.datetime.fromtimestamp(1664450000,tz=UTC)
        upper=dt.datetime.fromtimestamp(1664460000,tz=UTC)
        out=evaluate_post(post,target,lower,upper)
        self.assertTrue(out["exact_identity"])
        self.assertTrue(out["pit_time_ok"])
        self.assertTrue(out["witness_pass"])

    def test_evaluate_wrong_url_fails(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        post={
            "id":"abc","created_utc":1664452800,"subreddit":"soccer",
            "permalink":"/r/soccer/x","url":"https://sport.sky.it/calcio/serie-a/y",
            "url_overridden_by_dest":None,
        }
        lower=dt.datetime.fromtimestamp(1664450000,tz=UTC)
        upper=dt.datetime.fromtimestamp(1664460000,tz=UTC)
        out=evaluate_post(post,target,lower,upper)
        self.assertFalse(out["witness_pass"])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,0,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,1,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=classify(0,0,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
