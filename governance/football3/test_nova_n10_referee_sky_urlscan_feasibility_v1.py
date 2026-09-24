from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_urlscan_feasibility_v1 import (
    UTC,
    build_query,
    decide,
    eligible_results,
    normalize_identity,
    query_bounds,
    safe_result_metadata,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_urlscan_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"74c23b10dd1a191dd0bc9bfe9d0eec622b8c4bb4")
        self.assertEqual([x["round"] for x in p["samples"]],[11,24,34])
        self.assertEqual(p["prior_urlscan_evidence"]["target_family"],"AIA_OFFICIAL_URLS")
        self.assertFalse(p["source"]["api_key_allowed"])
        self.assertFalse(p["source"]["scan_submission_allowed"])
        self.assertFalse(p["hard_rules"]["wayback_requery_allowed"])
        self.assertFalse(p["hard_rules"]["commoncrawl_requery_allowed"])

    def test_identity_normalization(self):
        a="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        b="http://www.sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34/amp?x=1#y"
        self.assertEqual(normalize_identity(a),normalize_identity(b))

    def test_query_bounds_and_query(self):
        row={
            "sky_visible_published_utc":"2023-05-04T11:10:00Z",
            "first_fixture_cutoff_utc":"2023-05-06T13:00:00Z",
        }
        lower,upper,start,end=query_bounds(row,300)
        self.assertEqual(lower,dt.datetime(2023,5,4,11,5,tzinfo=UTC))
        self.assertEqual(upper,dt.datetime(2023,5,6,13,0,tzinfo=UTC))
        q=build_query("https://urlscan.io/api/v1/search/",row,100,300)
        self.assertIn("page.domain%3Asport.sky.it",q)
        self.assertIn("size=100",q)
        self.assertIn("2023-05-04T11%3A05%3A00.000Z",q)

    def test_safe_metadata_only(self):
        raw={
            "_id":"abc",
            "task":{"time":"2023-05-04T12:00:00Z","url":"https://sport.sky.it/x","extra":"ignore"},
            "page":{"url":"https://sport.sky.it/x","title":"ignore","status":"ignore"},
            "data":"ignore",
        }
        m=safe_result_metadata(raw)
        self.assertEqual(set(m),{"scan_id","scan_time","task_url","page_url"})
        self.assertEqual(m["scan_id"],"abc")

    def test_eligible_exact_identity_and_pit(self):
        target="https://sport.sky.it/calcio/serie-a/2023/05/04/arbitri-serie-a-designazioni-giornata-34"
        lower=dt.datetime(2023,5,4,11,5,tzinfo=UTC)
        upper=dt.datetime(2023,5,6,13,0,tzinfo=UTC)
        rows=[
            {"_id":"a","task":{"time":"2023-05-04T12:00:00Z","url":target+"?utm=x"},"page":{"url":target}},
            {"_id":"b","task":{"time":"2023-05-06T14:00:00Z","url":target},"page":{"url":target}},
            {"_id":"c","task":{"time":"2023-05-04T12:30:00Z","url":target+"-wrong"},"page":{"url":target+"-wrong"}},
        ]
        out=eligible_results(rows,target,lower,upper)
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]["scan_id"],"a")

    def test_decision_fail_closed(self):
        p=json.loads(REG.read_text())
        c,n=decide([],[],p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertEqual(n,p["reasonable_subroutes"]["if_zero_no_external_errors"])
        c2,n2=decide([],[24],p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertEqual(n2,p["reasonable_subroutes"]["if_external_error"])
        c3,n3=decide([34],[24],p)
        self.assertEqual(c3,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n3,p["reasonable_subroutes"]["if_positive"])


if __name__=="__main__":
    unittest.main()
