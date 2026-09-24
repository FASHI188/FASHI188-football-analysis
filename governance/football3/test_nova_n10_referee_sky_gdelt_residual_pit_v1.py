from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_gdelt_residual_pit_v1 import (
    classify,
    residual_dates_for_round,
)

REG=Path(__file__).with_name("nova_n10_referee_sky_gdelt_residual_pit_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_scope(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"c9623a41399965a2699c141d9f20c34d52afab62")
        self.assertEqual(
            p["target_rounds"],
            [8,11,12,14,15,16,17,19,20,21,22,23,25,27,28,32,34],
        )
        self.assertEqual(p["excluded_prior_sample_rounds"],[9,24,38])
        self.assertFalse(p["hard_rules"]["publication_day_requery_allowed"])
        self.assertFalse(p["hard_rules"]["prior_sample_round_requery_allowed"])
        self.assertFalse(p["hard_rules"]["result_labels_read"])
        self.assertFalse(p["hard_rules"]["score_values_read"])

    def test_target_exclusion_partition(self):
        p=json.loads(REG.read_text())
        self.assertFalse(set(p["target_rounds"]) & set(p["excluded_prior_sample_rounds"]))
        self.assertEqual(p["publication_day_parent"]["zero_round_n"],17)
        self.assertEqual(p["publication_day_parent"]["positive_round_n"],0)
        self.assertEqual(p["publication_day_parent"]["error_round_n"],0)

    def test_residual_dates_exclude_publication_day(self):
        pub,res=residual_dates_for_round(
            "2023-05-04T11:10:00Z",
            "2023-05-06T13:00:00Z",
            5,
            4,
        )
        self.assertEqual(pub,"2023-05-04")
        self.assertEqual(res,["2023-05-05","2023-05-06"])
        self.assertNotIn(pub,res)

    def test_residual_dates_same_day_empty(self):
        pub,res=residual_dates_for_round(
            "2023-05-04T11:10:00Z",
            "2023-05-04T13:00:00Z",
            5,
            4,
        )
        self.assertEqual(pub,"2023-05-04")
        self.assertEqual(res,[])

    def test_classify_positive(self):
        p=json.loads(REG.read_text())
        c,n,closed=classify([8],["2023-01-01"],p)
        self.assertEqual(c,"POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertFalse(closed)
        self.assertEqual(n,p["reasonable_subroutes"]["if_positive"])

    def test_classify_external_and_zero(self):
        p=json.loads(REG.read_text())
        c,n,closed=classify([],["2023-01-01"],p)
        self.assertEqual(c,"STOP_DATA_COVERAGE")
        self.assertFalse(closed)
        self.assertEqual(n,p["reasonable_subroutes"]["if_external_error"])
        c2,n2,closed2=classify([],[],p)
        self.assertEqual(c2,"STOP_DATA_COVERAGE")
        self.assertTrue(closed2)
        self.assertEqual(n2,p["reasonable_subroutes"]["if_complete_zero"])


if __name__=="__main__":
    unittest.main()
