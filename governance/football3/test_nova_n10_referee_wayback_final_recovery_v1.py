from __future__ import annotations

import json
import unittest
from pathlib import Path

REG=Path(__file__).with_name("nova_n10_referee_wayback_final_recovery_registry_v1.json")


class T(unittest.TestCase):
    def test_final_recovery_is_single_bounded_batch(self):
        p=json.loads(REG.read_text())
        f=p["final_recovery_contract"]
        self.assertEqual(p["exact_base"],"6c45c6629e0de3ccb486623df4a3fab8a109d8c0")
        self.assertEqual(f["final_recovery_batch_number"],1)
        self.assertEqual(f["max_final_recovery_batches"],1)
        self.assertTrue(f["close_wayback_for_remaining_external_error_rounds_after_terminal_run"])

    def test_retry_partition_exact(self):
        p=json.loads(REG.read_text())
        s=set(p["final_recovery_contract"]["successful_rounds_frozen"])
        r=set(p["final_recovery_contract"]["retry_rounds_exact"])
        self.assertEqual(r,{8,9,11,12,14,15,16,17,19,20,21,22,23,27,28,32,34,36,38})
        self.assertEqual(s|r,set(range(1,39)))
        self.assertFalse(s&r)

    def test_post_cutoff_rounds_are_not_retried(self):
        p=json.loads(REG.read_text())
        f=p["final_recovery_contract"]
        self.assertEqual(f["genuine_post_cutoff_rounds_frozen"],[24,25])
        self.assertNotIn(24,f["retry_rounds_exact"])
        self.assertNotIn(25,f["retry_rounds_exact"])
        self.assertIn(24,f["successful_rounds_frozen"])
        self.assertIn(25,f["successful_rounds_frozen"])
        self.assertFalse(f["requery_post_cutoff_rounds_allowed"])

    def test_resume_parent_is_exact_terminal_artifact(self):
        p=json.loads(REG.read_text())
        r=p["resume_parent"]
        self.assertEqual(r["run"],35942808507)
        self.assertEqual(r["head"],"6c45c6629e0de3ccb486623df4a3fab8a109d8c0")
        self.assertEqual(r["artifact_id"],10785654644)
        self.assertEqual(r["artifact_zip_sha256"],"d6ce7831172016d7eb7399d12b8040587e2660be68a2b82b61af1f8bc3cae989")
        self.assertEqual(r["fixture_n"],380)

    def test_zero_label_and_oof_closed(self):
        p=json.loads(REG.read_text())
        h=p["hard_rules"]
        self.assertFalse(h["result_labels_read"])
        self.assertFalse(h["score_values_read"])
        self.assertFalse(h["referee_assignment_body_parsed"])
        self.assertFalse(h["archived_page_body_read"])
        self.assertFalse(h["training_allowed"])
        self.assertFalse(h["scoring_allowed"])
        self.assertEqual(h["candidate_weight"],0)
        self.assertEqual(h["matrix_delta"],0)
        self.assertFalse(p["decision_contract"]["referee_oof_allowed"])


if __name__=="__main__":
    unittest.main()
