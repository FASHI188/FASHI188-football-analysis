from __future__ import annotations

import datetime as dt
import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_archiveit_feasibility_v1 import (
    UTC,
    build_query,
    classify,
    evaluate_row,
    parse_cdx,
    query_bounds,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_archiveit_feasibility_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"1c67b272dbc2b2f9f65e9dc334fd8c1b00b419dd")
        self.assertEqual([x["round"] for x in p["samples"]],[8,24,34])
        self.assertFalse(p["hard_rules"]["collection_id_guessing_allowed"])
        self.assertFalse(p["hard_rules"]["archive_content_fetched"])
        self.assertFalse(p["hard_rules"]["article_body_read"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])

    def test_query_bounds_strict_upper(self):
        frm,to=query_bounds("2023-05-04T11:10:00Z","2023-05-06T13:00:00Z")
        self.assertEqual(frm,"20230504111000")
        self.assertEqual(to,"20230506125959")

    def test_build_query(self):
        q=build_query(
            "https://wayback.archive-it.org/all/timemap/cdx",
            "https://sport.sky.it/calcio/serie-a/x",
            "2023-05-04T11:10:00Z",
            "2023-05-06T13:00:00Z",
        )
        self.assertIn("url=https%3A%2F%2Fsport.sky.it%2Fcalcio%2Fserie-a%2Fx",q)
        self.assertIn("from=20230504111000",q)
        self.assertIn("to=20230506125959",q)
        self.assertIn("fl=timestamp%2Coriginal%2Cstatuscode",q)
        self.assertIn("filter=statuscode%3A200",q)

    def test_parse_cdx(self):
        raw=b"20230504120000 https://sport.sky.it/calcio/serie-a/x 200\n20230504130000 https://sport.sky.it/calcio/serie-a/x 404\n"
        rows=parse_cdx(raw)
        self.assertEqual(rows,[{"timestamp":"20230504120000","original":"https://sport.sky.it/calcio/serie-a/x","statuscode":"200"}])

    def test_evaluate_exact_identity_and_pit(self):
        row={"timestamp":"20230504120000","original":"http://www.sport.sky.it/calcio/serie-a/x/amp?utm=a","statuscode":"200"}
        out=evaluate_row(
            row,
            "https://sport.sky.it/calcio/serie-a/x",
            "2023-05-04T11:10:00Z",
            "2023-05-06T13:00:00Z",
        )
        self.assertTrue(out["exact_identity"])
        self.assertTrue(out["pit_time_ok"])
        self.assertTrue(out["witness_pass"])

    def test_evaluate_rejects_after_cutoff(self):
        row={"timestamp":"20230506130000","original":"https://sport.sky.it/calcio/serie-a/x","statuscode":"200"}
        out=evaluate_row(
            row,
            "https://sport.sky.it/calcio/serie-a/x",
            "2023-05-04T11:10:00Z",
            "2023-05-06T13:00:00Z",
        )
        self.assertFalse(out["pit_time_ok"])
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
