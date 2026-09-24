from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_hnalgolia_feasibility_v1 import (
    UTC,
    build_query,
    classify,
    evaluate_hit,
    parse_page,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_hnalgolia_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"5d0a8263f29d571d726e5ee6f831b329d58f69b1")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertFalse(p["hard_rules"]["story_text_read"])
        self.assertFalse(p["hard_rules"]["comment_text_read"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])

    def test_build_query_metadata_only(self):
        p=json.loads(REG.read_text())
        q=build_query(
            p["source"]["endpoint"],
            "https://sport.sky.it/calcio/serie-a/x",
            1664450000,
            1664460000,
            0,
            p["source"],
            p["query_contract"],
        )
        self.assertIn("query=https%3A%2F%2Fsport.sky.it",q)
        self.assertIn("tags=story",q)
        self.assertIn("restrictSearchableAttributes=url",q)
        self.assertIn("numericFilters=created_at_i%3E%3D1664450000%2Ccreated_at_i%3C1664460000",q)
        self.assertIn("attributesToRetrieve=objectID%2Curl%2Ccreated_at_i",q)

    def test_parse_page_accepts_metadata_only(self):
        raw=json.dumps({
            "hits":[{"objectID":"1","url":"https://sport.sky.it/x","created_at_i":1664452800}],
            "page":0,"nbPages":1,"nbHits":1,"hitsPerPage":100
        }).encode()
        hits,meta=parse_page(raw)
        self.assertEqual(hits[0]["objectID"],"1")
        self.assertEqual(meta["nbPages"],1)

    def test_parse_page_rejects_text_field(self):
        raw=json.dumps({
            "hits":[{"objectID":"1","url":"https://sport.sky.it/x","created_at_i":1664452800,"story_text":"NO"}],
            "page":0,"nbPages":1,"nbHits":1,"hitsPerPage":100
        }).encode()
        with self.assertRaises(Exception):
            parse_page(raw)

    def test_evaluate_hit_exact_identity_and_pit(self):
        target="https://sport.sky.it/calcio/serie-a/x"
        hit={"objectID":"1","url":target+"?utm=x","created_at_i":1664452800}
        lower=dt.datetime.fromtimestamp(1664450000,tz=UTC)
        upper=dt.datetime.fromtimestamp(1664460000,tz=UTC)
        out=evaluate_hit(hit,target,lower,upper)
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
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=classify(0,0,p)
        self.assertEqual(c3,"STOP_DATA_COVERAGE")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
