from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "research" / "draw_signal_closure_audit_v502.py"
spec = importlib.util.spec_from_file_location("draw_signal_closure_audit_v502", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class DrawSignalClosureAuditTests(unittest.TestCase):
    def test_required_version_families_are_registered(self):
        versions = {row["version"] for row in module.EXPERIMENT_SPECS}
        missing = [prefix for prefix in module.REQUIRED_VERSION_PREFIXES if not any(v.startswith(prefix) for v in versions)]
        self.assertEqual(missing, [])

    def test_column_classification_is_fail_closed(self):
        self.assertEqual(module.classify_column("FTR")[0], "POSTMATCH_FORBIDDEN")
        self.assertEqual(module.classify_column("HS")[0], "POSTMATCH_FORBIDDEN")
        self.assertEqual(module.classify_column("Date")[0], "PIT_SAFE_STRUCTURAL")
        self.assertEqual(module.classify_column("AvgD")[0], "RETROSPECTIVE_MARKET_REFERENCE")
        self.assertEqual(module.classify_column("1XBA")[0], "RETROSPECTIVE_MARKET_REFERENCE")
        self.assertEqual(module.classify_column("BFCH")[0], "RETROSPECTIVE_MARKET_REFERENCE")
        self.assertEqual(module.classify_column("Referee")[0], "PIT_UNPROVEN_CONTEXT")
        self.assertEqual(module.classify_column("mystery_feature")[0], "UNKNOWN_PIT_STATUS")

    def test_selected_accuracy_is_not_preferred_as_full_accuracy(self):
        evidence = [
            {"file": "m.json", "json_path": "SELECTIVE_1X2.accuracy", "value": 0.70},
            {"file": "m.json", "json_path": "FULL_1X2.accuracy", "value": 0.52},
        ]
        self.assertEqual(module.choose_metric("accuracy", evidence), 0.52)

    def test_metric_matching_uses_leaf_only(self):
        evidence = module.collect_metric_evidence([(
            "x.json",
            {"best_draw_precision_diagnostic": {"hits": 975, "draw_precision": 0.339}},
        )])
        self.assertEqual([item["value"] for item in evidence["draw_precision"]], [0.339])

    def test_repeated_target_detection(self):
        objects = [("x.json", {"evaluation_set_status": {"not_a_final_blind_holdout": True, "reason": "repeatedly inspected"}})]
        flag, files = module.repeated_test_flag(objects)
        self.assertTrue(flag)
        self.assertEqual(files, ["x.json"])

    def test_metric_missing_is_not_fabricated(self):
        evidence = module.collect_metric_evidence([("x.json", {"status": "REJECTED"})])
        self.assertIsNone(module.choose_metric("draw_f1", evidence["draw_f1"]))

    def test_canonical_sha_is_order_stable(self):
        self.assertEqual(module.canonical_sha({"b": 2, "a": 1}), module.canonical_sha({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
