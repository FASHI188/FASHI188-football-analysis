from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE_PATH = ROOT / "audit" / "draw_signal_closure_engine_v502_r4.py"
VERIFY_PATH = ROOT / "research" / "draw_signal_complete_ledger_v502.py"

engine_spec = importlib.util.spec_from_file_location("draw_signal_closure_engine_v502_r4", ENGINE_PATH)
engine = importlib.util.module_from_spec(engine_spec)
assert engine_spec.loader is not None
engine_spec.loader.exec_module(engine)

verify_spec = importlib.util.spec_from_file_location("draw_signal_complete_ledger_v502", VERIFY_PATH)
verify = importlib.util.module_from_spec(verify_spec)
assert verify_spec.loader is not None
verify_spec.loader.exec_module(verify)


class DrawSignalCompleteLedgerTests(unittest.TestCase):
    def test_production_path_detects_relevant_actual_asset_missing_from_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "football-data" / "validation").mkdir(parents=True)
            registered = root / "football-data" / "validation" / "registered.py"
            hidden = root / "football-data" / "validation" / "hidden_relevant.py"
            registered.write_text("def score(): return 1\n", encoding="utf-8")
            hidden.write_text("def score(): return 2\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "."], check=True)
            registry = {
                "schema_version": "TEST",
                "entries": [{"path": registered.relative_to(root).as_posix(), "expected_included": True, "reason": "registered"}],
            }
            actual = engine.build_actual_asset_ledger(root)
            coverage = engine.research_asset_coverage(registry, actual)
            self.assertIn(hidden.relative_to(root).as_posix(), coverage["extra"])
            self.assertFalse(coverage["all_covered"])

    def test_actual_ledger_records_include_or_exclude_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            py = root / "football-data" / "research" / "x.py"
            log = root / "football-data" / "manifests" / "x.log"
            py.parent.mkdir(parents=True)
            log.parent.mkdir(parents=True)
            py.write_text("x=1\n", encoding="utf-8")
            log.write_text("generated\n", encoding="utf-8")
            rows = engine.build_actual_asset_ledger(root, [py.relative_to(root).as_posix(), log.relative_to(root).as_posix()])
            by_path = {row["path"]: row for row in rows}
            self.assertTrue(by_path[py.relative_to(root).as_posix()]["included"])
            self.assertTrue(by_path[py.relative_to(root).as_posix()]["inclusion_reason"])
            self.assertFalse(by_path[log.relative_to(root).as_posix()]["included"])
            self.assertTrue(by_path[log.relative_to(root).as_posix()]["exclusion_reason"])

    def test_decision_verifier_accepts_positive_domain_candidate(self):
        candidate = {"field": "round", "eligible_domain_specific_scopes": [{"competition": "KOR_KLeague1"}]}
        routes = {"unresolved": [], "blocks_exhausted": False}
        prereg = engine.make_preregistration([], [candidate], routes)
        result = verify.verify_decision_evidence(engine.POSITIVE_DECISION, [], [candidate], routes, prereg)
        self.assertTrue(result["consistent"])

    def test_decision_verifier_rejects_exhausted_with_unresolved_route(self):
        routes = {"unresolved": [{"id": "X"}], "candidate_improvements": [], "missing_result_evidence": [], "blocks_exhausted": True}
        with self.assertRaises(ValueError):
            verify.verify_decision_evidence(engine.NEGATIVE_DECISION, [], [], routes, None)

    def test_route_closure_classifies_failed_execution_as_unresolved(self):
        row = {"id": "X", "status": "FAILED_EXECUTION", "pit": "PIT_CLAIMED", "file_count": 1, "files": ["x.py"], "metrics": {}, "rejection_or_result": None, "family": "model"}
        result = engine.classify_route_closure(row)
        self.assertEqual(result["closure_state"], "UNRESOLVED")

    def test_route_closure_classifies_explicit_reject(self):
        row = {"id": "X", "status": "PASS", "pit": "PIT_SAFE", "file_count": 1, "files": ["x.py"], "metrics": {"accuracy": 0.5}, "rejection_or_result": "REJECT_NO_GAIN", "family": "model"}
        result = engine.classify_route_closure(row)
        self.assertEqual(result["closure_state"], "RESOLVED_REJECTED")


if __name__ == "__main__":
    unittest.main()
