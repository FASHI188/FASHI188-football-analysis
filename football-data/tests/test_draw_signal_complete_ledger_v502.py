from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "research" / "draw_signal_closure_audit_v502.py"
FINAL_PATH = ROOT / "research" / "draw_signal_complete_ledger_v502.py"

core_spec = importlib.util.spec_from_file_location("draw_signal_closure_audit_v502", CORE_PATH)
core = importlib.util.module_from_spec(core_spec)
assert core_spec.loader is not None
core_spec.loader.exec_module(core)

final_spec = importlib.util.spec_from_file_location("draw_signal_complete_ledger_v502", FINAL_PATH)
finalizer = importlib.util.module_from_spec(final_spec)
assert final_spec.loader is not None
final_spec.loader.exec_module(finalizer)


class DrawSignalCompleteLedgerTests(unittest.TestCase):
    def test_family_inference_does_not_rename_known_routes(self):
        self.assertEqual(finalizer.infer_family("validate_1x2_injury_onset_fast100_v6131.py"), "availability and disciplinary context")
        self.assertEqual(finalizer.infer_family("v6_1x2_market_movement_v6105_status.json"), "bookmaker market level/movement/dispersion")
        self.assertEqual(finalizer.infer_family("v6_zero_modified_skellam_draw_v672.py"), "score/goal-difference matrix")

    def test_pit_inference_is_fail_closed(self):
        self.assertEqual(finalizer.infer_pit("market.py", "closing odds"), "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN")
        self.assertEqual(finalizer.infer_pit("random100.py", ""), "RANDOM_SAMPLE_NOT_PROMOTION_GRADE")
        self.assertEqual(finalizer.infer_pit("unknown.py", ""), "PIT_STATUS_NOT_PROVEN_IN_FILE")

    def test_extra_identity_fields_are_not_new_signals(self):
        feature = {
            "fields": [{
                "field": "venue", "classification": "UNKNOWN_PIT_STATUS",
                "pit_safe": False, "substantive_predictive_candidate": True,
                "previously_tested_exact_or_alias": False, "tested_alias_families": [],
                "untested": True, "qualifies_existing_pit_safe_untested": False,
            }]
        }
        result = finalizer.tighten_feature_difference(feature)
        row = result["fields"][0]
        self.assertEqual(row["classification"], "PIT_SAFE_STRUCTURAL")
        self.assertFalse(row["substantive_predictive_candidate"])
        self.assertEqual(result["EXISTING_PIT_SAFE_UNTESTED_FEATURES"], [])
        self.assertEqual(result["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"], [])

    def test_research_file_discovery_excludes_cache(self):
        paths = finalizer.relevant_research_files(ROOT.parent)
        self.assertTrue(paths)
        self.assertFalse(any("/cache/" in path.as_posix() for path in paths))
        self.assertTrue(all("draw" in path.name.lower() or "1x2" in path.name.lower() for path in paths))


if __name__ == "__main__":
    unittest.main()
