from __future__ import annotations

import importlib.util
import tempfile
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
        missing = [prefix for prefix in module.REQUIRED_VERSION_PREFIXES if not any(version.startswith(prefix) for v in versions)]
        self.assertEqual(missing, [])

    def test_synthetic_pit_safe_covered_untested_field_becomes_candidate_and_preregistration(self):
        contracts = {
            "synthetic_signal": {
                "classification": "PIT_SAFE_PREDICTION_TIME",
                "source_semantics": "synthetic pre-kickoff observed_at contract",
            }
        }
        row = module.assess_field_candidate(
            "synthetic_signal",
            {"files": 2, "rows_in_files": 100, "nonempty": 95, "competitions": {"A", "B"}, "sample_paths": ["a.csv"]},
            all_columns={"synthetic_signal"},
            total_rows=100,
            total_competitions=2,
            dataflow_index={},
            pit_contracts=contracts,
        )
        self.assertTrue(row["qualifies_existing_pit_safe_untested"])
        decision, prereg = module.decide_and_preregister([row])
        self.assertEqual(decision, module.POSITIVE_DECISION)
        self.assertEqual(prereg["status"], "PRE_REGISTERED_NOT_RUN")
        self.assertEqual(prereg["features"], ["synthetic_signal"])
        self.assertFalse(prereg["run_authorized"])

    def test_unknown_field_is_near_miss_not_silently_dropped(self):
        row = module.assess_field_candidate(
            "mystery_feature",
            {"files": 1, "rows_in_files": 100, "nonempty": 100, "competitions": {"A"}, "sample_paths": ["a.csv"]},
            all_columns={"mystery_feature"},
            total_rows=100,
            total_competitions=1,
            dataflow_index={},
        )
        result = module.preserve_unknown_near_miss({
            "fields": [row],
            "UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED": [],
        })
        self.assertEqual(row["classification"], "UNKNOWN_PIT_STATUS")
        self.assertEqual([item["field"] for item in result["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]], ["mystery_feature"])

    def test_unknown_field_with_old_complete_dataflow_still_remains_near_miss(self):
        row = module.assess_field_candidate(
            "mystery_feature",
            {"files": 1, "rows_in_files": 100, "nonempty": 100, "competitions": {"A"}, "sample_paths": ["a.csv"]},
            all_columns={"mystery_feature"},
            total_rows=100,
            total_competitions=1,
            dataflow_index={"mystery_feature": [{"complete_dataflow_chain": True}]},
        )
        self.assertFalse(row["untested"])
        result = module.preserve_unknown_near_miss({
            "fields": [row],
            "UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED": [],
        })
        self.assertEqual([item["field"] for item in result["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]], ["mystery_feature"])

    def test_round_is_prediction_time_limited_scope_and_not_bookmaker_alias(self):
        columns = {"round", "B365H", "B365D", "B365A"}
        self.assertIsNone(module.bookmaker_triplet_key("round", columns))
        self.assertEqual(module.classify_column("round", columns)[0], "PIT_SAFE_PREDICTION_TIME")
        self.assertEqual(module.classify_column("B365D", columns)[0], "RETROSPECTIVE_MARKET_REFERENCE")

    def test_metadata_only_string_is_not_model_use_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "meta_only.py"
            script.write_text('FIELD_NAME = "mystery_feature"\nMETADATA = {"field": "mystery_feature"}\n', encoding="utf-8")
            index = module.build_dataflow_index(root, scripts=[script])
            self.assertNotIn("mystery_feature", index)

    def test_actual_row_access_without_full_chain_is_not_previous_model_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "reader.py"
            script.write_text('def read(raw):\n    return raw.get("round")\n', encoding="utf-8")
            index = module.build_dataflow_index(root, scripts=[script])
            self.assertIn("round", index)
            self.assertFalse(module.dataflow_chain_is_complete(index["round"]))

    def test_selected_accuracy_is_not_preferred_as_full_accuracy(self):
        evidence = [
            {"file": "m.json", "json_path": "SELECTIVE_1X2.accuracy", "value": 0.70},
            {"file": "m.json", "json_path": "FULL_1X2.accuracy", "value": 0.52},
        ]
        self.assertEqual(module.choose_metric("accuracy", evidence), 0.52)

    def test_metric_matching_uses_leaf_only(self):
        evidence = module.collect_metric_evidence([("x.json", {"best_draw_precision_diagnostic": {"hits": 975, "draw_precision": 0.339}})])
        self.assertEqual([item["value"] for item in evidence["draw_precision"]], [0.339])

    def test_canonical_sha_is_order_stable(self):
        self.assertEqual(module.canonical_sha({"b": 2, "a": 1}), module.canonical_sha({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
