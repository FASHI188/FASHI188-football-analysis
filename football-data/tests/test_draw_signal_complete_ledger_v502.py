from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "research" / "draw_signal_closure_audit_v502.py"
VERIFY_PATH = ROOT / "research" / "draw_signal_complete_ledger_v502.py"

core_spec = importlib.util.spec_from_file_location("draw_signal_closure_audit_v502", CORE_PATH)
core = importlib.util.module_from_spec(core_spec)
assert core_spec.loader is not None
core_spec.loader.exec_module(core)

verify_spec = importlib.util.spec_from_file_location("draw_signal_complete_ledger_v502", VERIFY_PATH)
verifier = importlib.util.module_from_spec(verify_spec)
assert verify_spec.loader is not None
verify_spec.loader.exec_module(verifier)


class DrawSignalCompleteLedgerTests(unittest.TestCase):
    def test_deliberately_omitted_research_asset_fails_coverage(self):
        coverage = core.research_asset_coverage(["a.py", "b.json"], [{"path": "a.py", "matched": True}])
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["missing"], ["b.json"])
        with self.assertRaises(ValueError):
            verifier.verify_asset_coverage(coverage)

    def test_content_based_asset_discovery_does_not_require_draw_or_1x2_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "season_phase_route.py"
            path.write_text('DIRECTIONS=("home","draw","away")\ndef evaluate(rows):\n    return accuracy_score(rows)\n', encoding="utf-8")
            self.assertTrue(core.is_expected_research_asset(path))

    def test_both_legal_decisions_are_accepted_when_evidence_matches(self):
        negative = verifier.verify_decision_evidence(core.NEGATIVE_DECISION, [], None)
        self.assertTrue(negative["consistent"])
        candidate = {"field": "new_signal"}
        prereg = core.make_preregistration([candidate])
        positive = verifier.verify_decision_evidence(core.POSITIVE_DECISION, [candidate], prereg)
        self.assertEqual(positive["candidate_count"], 1)

    def test_mismatched_decision_fails_closed(self):
        with self.assertRaises(ValueError):
            verifier.verify_decision_evidence(core.NEGATIVE_DECISION, [{"field": "new_signal"}], None)

    def test_workflow_allows_both_decisions_without_negative_hardcode(self):
        workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "football-draw-challenger-v502.yml"
        text = workflow.read_text(encoding="utf-8")
        self.assertIn("ALLOWED_DECISIONS", text)
        self.assertNotIn("== 'EXISTING_DATA_DRAW_SIGNAL_EXHAUSTED_NO_NEW_TRAINING'", text)
        self.assertNotIn("UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED'] == []", text)

    def test_verify_closure_outputs_accepts_positive_artifact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = {"field": "new_signal"}
            prereg = core.make_preregistration([candidate])
            coverage = {"expected": ["a.py"], "matched": ["a.py"], "missing": [], "extra": [], "all_covered": True, "expected_count": 1, "matched_count": 1}
            base = {"formal_weight": 0, "provider_network_used": False, "external_request_attempts": 0, "api_football_key_accessed": False, "model_training": 0, "decision": core.POSITIVE_DECISION}
            (root / "closure_audit.json").write_text(json.dumps({**base, "preregistration": prereg, "research_asset_coverage": coverage}), encoding="utf-8")
            (root / "feature_difference.json").write_text(json.dumps({"EXISTING_PIT_SAFE_UNTESTED_FEATURES": [candidate], "UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED": []}), encoding="utf-8")
            (root / "decision.json").write_text(json.dumps({"decision": core.POSITIVE_DECISION}), encoding="utf-8")
            (root / "complete_research_file_ledger.json").write_text(json.dumps({"count": 1, "rows": [{"path": "a.py", "formal_weight": 0}], "coverage": coverage}), encoding="utf-8")
            (root / "metadata.json").write_text(json.dumps(base), encoding="utf-8")
            result = verifier.verify_closure_outputs(root)
            self.assertEqual(result["decision_check"]["expected_decision"], core.POSITIVE_DECISION)


if __name__ == "__main__":
    unittest.main()
