from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_pullpush_feasibility_v1 import (
    UTC,
    build_query,
    classify,
    evaluate,
    parse_response,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_pullpush_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"a7a827afabdd3b1d1dda76da454319ccc4ffe148")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertEqual(p["query_contract"]["variants"],["exact_url","quoted_exact_url"])
        self.assertFalse(p["hard_rules"]["title_read"])
        self.assertFalse(p["hard_rules"]["selftext_read"])
        self.assertFalse(p["hard_rules"]["comments_read"])
        self.assertFalse(p["hard_rules"]["author_read"])

    def test_build_query_pit_and_fields(self):
        p=json.loads(REG.read_text())
        q=build_query(
            p["source"]["endpoint"],
            "quoted_exact_url",
            "https://sport.sky.it/calcio/serie-a/x",
            1664450000,
            1664460000,
            p["source"],
            p["query_contract"],
        )
        self.assertIn("q=%22https%3A%2F%2Fsport.sky.it",q)
        self.assertIn("after=1664450000",q)
        self.assertIn("before=1664459999",q)
        self.assertIn("fields=id%2Ccreated_utc%2Curl%2Curl_overridden_by_dest%2Cpermalink%2Csubreddit",q)

    def test_parse_metadata_only(self):
        p=json.loads(REG.read_text())
        raw=json.dumps({"data":[{
            "id":"abc","created_utc":1664452800,"url":"https://sport.sky.it/x",
            "url_overridden_by_dest":"https://sport.sky.it/x","permalink":"/r/soccer/x","subreddit":"soccer"
        }]}).encode()
        rows,meta=parse_response(raw,p["source"])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["id"],"abc")

    def test_parse_rejects_forbidden_content_fields(self):
        p=json.loads(REG.read_text())
        raw=json.dumps({"data":[{"id":"abc","created_utc":1,"url":"https://sport.sky.it/x","selftext":"NO"}]}).encode()
        with self.assertRaises(Exception):
            parse_response(raw,p["source"])

    def test_evaluate_exact_identity_and_pit(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        row={
            "id":"abc","created_utc":1664452800,"url":target+"?utm=x",
            "url_overridden_by_dest":None,"permalink":"/r/soccer/x","subreddit":"soccer"
        }
        lower=dt.datetime.fromtimestamp(1664450000,tz=UTC)
        upper=dt.datetime.fromtimestamp(1664460000,tz=UTC)
        out=evaluate(row,target,lower,upper)
        self.assertTrue(out["exact_identity"])
        self.assertTrue(out["pit_time_ok"])
        self.assertTrue(out["witness_pass"])

    def test_classify_paths(self):
        p=json.loads(REG.read_text())
        c,n=classify(1,0,p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])
        c2,n2=classify(0,1,p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_or_contract_error"])
        c3,n3=classify(0,0,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
