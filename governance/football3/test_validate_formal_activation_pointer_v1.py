from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import validate_formal_activation_pointer_v1 as activation

ROOT = Path(__file__).resolve().parents[2]
POINTER = ROOT / "football-data/config/formal_model_pointer_historical_xg_fusion_v2.json"
SCHEMA = ROOT / "governance/football3/formal_activation_pointer_schema_v1.json"


class FormalActivationPointerTests(unittest.TestCase):
    def setUp(self):
        self.pointer = json.loads(POINTER.read_text(encoding="utf-8"))
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    def test_valid_pointer(self):
        activation.validate_pointer(self.pointer, self.schema)

    def test_weight_change_rejected(self):
        bad = copy.deepcopy(self.pointer)
        bad["model"]["xg_weight"] = 0.5
        with self.assertRaises(activation.ActivationError):
            activation.validate_pointer(bad, self.schema)

    def test_missing_external_current_gate_rejected(self):
        bad = copy.deepcopy(self.pointer)
        bad["requires_current_exact_head_match"] = False
        with self.assertRaises(activation.ActivationError):
            activation.validate_pointer(bad, self.schema)

    def test_market_or_training_enablement_rejected(self):
        for key in ("market_features", "training", "tuning", "new_target_labels", "prospective_queue"):
            bad = copy.deepcopy(self.pointer)
            bad[key] = True
            with self.subTest(key=key), self.assertRaises(activation.ActivationError):
                activation.validate_pointer(bad, self.schema)

    def test_unknown_field_rejected(self):
        bad = copy.deepcopy(self.pointer)
        bad["unknown"] = True
        with self.assertRaises(activation.ActivationError):
            activation.validate_pointer(bad, self.schema)

    def test_scope_change_rejected(self):
        bad = copy.deepcopy(self.pointer)
        bad["formal_scope"] = bad["formal_scope"][:-1]
        with self.assertRaises(activation.ActivationError):
            activation.validate_pointer(bad, self.schema)

    def test_ready_merge_and_exact_score_remain_false(self):
        for key in ("ready", "merge", "exact_score_gate_changed"):
            bad = copy.deepcopy(self.pointer)
            bad[key] = True
            with self.subTest(key=key), self.assertRaises(activation.ActivationError):
                activation.validate_pointer(bad, self.schema)


if __name__ == "__main__":
    unittest.main()
