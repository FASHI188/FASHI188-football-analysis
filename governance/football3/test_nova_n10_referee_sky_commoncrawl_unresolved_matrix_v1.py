from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_commoncrawl_unresolved_matrix_v1 import round_decision

REG=Path(__file__).with_name("nova_n10_referee_sky_commoncrawl_unresolved_matrix_registry_v1.json")


class T(unittest.TestCase):
    def test_registry_target_partition(self):
        p=json.loads(REG.read_text())
        self.assertEqual(p["exact_base"],"f8ea3a61d357b4d66fc19f0761e8432073801aab")
        self.assertEqual(
            p["target_rounds"],
            [11,12,14,15,16,17,19,20,21,22,23,24,25,27,28,32,34],
        )
        self.assertEqual(p["already_tested_commoncrawl_rounds"],[8,9,38])
        self.assertEqual(
            sorted(p["target_rounds"]+p["already_tested_commoncrawl_rounds"]),
            sorted(p["all_unresolved_rounds"]),
        )
        self.assertEqual(len(p["target_rounds"]),17)

    def test_no_requery_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["wayback_requery_allowed"])
        self.assertFalse(h["commoncrawl_requery_already_tested_rounds"])
        self.assertFalse(h["commoncrawl_warc_content_fetched"])
        self.assertFalse(h["referee_assignment_body_parsed"])

    def test_zero_label_model_contract(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["score_or_result_semantic_parse"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])
        self.assertEqual(h["candidate_weight"],0)
        self.assertEqual(h["matrix_delta"],0)

    def test_round_decision_no_overlap(self):
        self.assertEqual(round_decision(0,[],[]),"NO_COLLECTION_OVERLAP")

    def test_round_decision_zero_and_positive(self):
        ok=[{"error":None}]
        self.assertEqual(round_decision(1,ok,[]),"ZERO_CAPTURE")
        self.assertEqual(round_decision(1,ok,[{"timestamp":"20230101000000"}]),"ELIGIBLE_CAPTURE")

    def test_round_decision_external_error_is_fail_closed(self):
        mixed=[{"error":None},{"error":"HTTPError:503"}]
        self.assertEqual(
            round_decision(2,mixed,[{"timestamp":"20230101000000"}]),
            "EXTERNAL_ERROR",
        )
        p=json.loads(REG.read_text())
        self.assertTrue(p["close_contract"]["reasonable_same_source_subroutes_exhausted_after_this_batch"])
        self.assertTrue(p["close_contract"]["commoncrawl_route_close_if_no_external_errors"])


if __name__=="__main__":
    unittest.main()
