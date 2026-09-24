from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_commoncrawl_feasibility_v1 import (
    UTC,
    collection_intersects,
    eligible_rows,
    normalize_identity,
    prefix_query_url,
    select_collections,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_commoncrawl_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"f6e1b332424e0e2203c6943d3446328fe4778f1a")
        self.assertEqual([x["round"] for x in p["samples"]],[8,9,38])
        self.assertFalse(p["hard_rules"]["wayback_requery_allowed"])
        self.assertFalse(p["hard_rules"]["commoncrawl_warc_content_fetched"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])
        self.assertFalse(p["hard_rules"]["training_allowed"])
        self.assertFalse(p["hard_rules"]["scoring_allowed"])
        self.assertEqual(p["source"]["http_404_semantics"],"NO_USABLE_INDEX_ROWS_FAIL_CLOSED_ZERO_EVIDENCE")
        self.assertFalse(p["source"]["http_404_transport_error"])

    def test_identity_normalization(self):
        a="https://sport.sky.it/calcio/serie-a/2023/05/31/arbitri-serie-a-designazioni-giornata-38"
        b="http://www.sport.sky.it/calcio/serie-a/2023/05/31/arbitri-serie-a-designazioni-giornata-38/amp?x=1#y"
        self.assertEqual(normalize_identity(a),normalize_identity(b))

    def test_collection_intersection_and_selection(self):
        start=dt.datetime(2022,9,29,13,0,tzinfo=UTC)
        end=dt.datetime(2022,10,1,13,0,tzinfo=UTC)
        good={
            "id":"CC-MAIN-2022-40",
            "cdx-api":"https://index.commoncrawl.org/CC-MAIN-2022-40-index",
            "from":"2022-09-24T15:15:38",
            "to":"2022-10-08T00:04:52",
        }
        bad={
            "id":"CC-MAIN-2022-49",
            "cdx-api":"https://index.commoncrawl.org/CC-MAIN-2022-49-index",
            "from":"2022-11-26T08:07:25",
            "to":"2022-12-10T10:42:42",
        }
        self.assertTrue(collection_intersects(good,start,end))
        self.assertFalse(collection_intersects(bad,start,end))
        out=select_collections([bad,good],start,end,"index.commoncrawl.org")
        self.assertEqual([x["id"] for x in out],["CC-MAIN-2022-40"])

    def test_prefix_query(self):
        u=prefix_query_url(
            "https://index.commoncrawl.org/CC-MAIN-2022-40-index",
            "https://sport.sky.it/calcio/serie-a/x",
        )
        self.assertIn("matchType=prefix",u)
        self.assertIn("output=json",u)
        self.assertIn("filter=status%3A200",u)
        self.assertIn("url=sport.sky.it%2Fcalcio%2Fserie-a%2Fx",u)

    def test_eligible_rows_exact_identity_and_window(self):
        target="https://sport.sky.it/calcio/serie-a/2022/09/29/arbitri-serie-a-designazioni-giornata-8"
        rows=[
            {"timestamp":"20220929150000","url":target+"?social=x","status":"200","mime":"text/html","digest":"A"},
            {"timestamp":"20221001140000","url":target,"status":"200","mime":"text/html","digest":"B"},
            {"timestamp":"20220929160000","url":target+"-wrong","status":"200","mime":"text/html","digest":"C"},
            {"timestamp":"20220929170000","url":target,"status":"404","mime":"text/html","digest":"D"},
        ]
        lower=dt.datetime(2022,9,29,12,55,tzinfo=UTC)
        upper=dt.datetime(2022,10,1,13,0,tzinfo=UTC)
        good=eligible_rows(rows,target,lower,upper)
        self.assertEqual(len(good),1)
        self.assertEqual(good[0]["timestamp"],"20220929150000")

    def test_parent_unresolved_contract(self):
        p=json.loads(REG.read_text())
        unresolved=set(p["parent"]["unresolved_rounds"])
        samples={x["round"] for x in p["samples"]}
        self.assertTrue(samples.issubset(unresolved))
        self.assertEqual(p["parent"]["existing_pit_pass_round_n"],18)
        self.assertEqual(p["parent"]["wayback_post_cutoff_rounds"],[24,25,34])


if __name__=="__main__":
    unittest.main()
