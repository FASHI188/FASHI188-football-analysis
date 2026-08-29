from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "governance" / "r43gov0" / "locks" / "m10_rebuild_lock.json"


class M10RebuildLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = json.loads(LOCK.read_text(encoding="utf-8"))

    def test_gate_identity_is_frozen(self):
        x = self.lock
        self.assertEqual(x["status"], "FROZEN")
        self.assertEqual(x["m10_run_id"], 33239550708)
        self.assertEqual(x["m10_head"], "7c1815c47102412e88f72189e2b8f837d9b73a42")
        self.assertEqual(x["m10_gate_artifact_id"], 9710934083)
        self.assertEqual(
            x["m10_gate_artifact_digest"],
            "sha256:bb9340f34689bec2f1a400435bced06010c58c916f5b088f17782b24400bd2a4",
        )
        self.assertEqual(x["m10_gate_status"], "PASS")
        self.assertTrue(x["rebuild_complete"])

    def test_formal_default_and_nonpromotion_are_frozen(self):
        x = self.lock
        self.assertEqual(x["formal_default_profile"], "V500_frozen_unchanged")
        self.assertEqual(
            x["formal_default_v500_blob"],
            "9c302506c49aa1847e60cd7896fc1a80f3b6b457",
        )
        self.assertFalse(x["r43_components_enabled_by_default"])
        self.assertFalse(x["market_research_candidate_formal_promotion_allowed"])
        self.assertEqual(x["promotion_status"], "NOT_PROMOTED")
        self.assertFalse(x["fresh_confirmatory_test_claim"])

    def test_nonmarket_feature_numeric_states_remain_off(self):
        x = self.lock
        self.assertFalse(x["lineup_numeric_1x2_enabled"])
        self.assertFalse(x["player_technical_numeric_1x2_enabled"])
        self.assertFalse(x["head_coach_numeric_1x2_enabled"])
        self.assertFalse(x["availability_numeric_1x2_enabled"])

    def test_formal_and_sealed_paths_were_not_modified_by_rebuild(self):
        x = self.lock
        self.assertFalse(x["current_modified"])
        self.assertFalse(x["main_modified"])
        self.assertFalse(x["u0_y0_predictions_modified"])


if __name__ == "__main__":
    unittest.main()
