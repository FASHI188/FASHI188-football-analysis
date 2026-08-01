from __future__ import annotations

import hashlib
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


def candidate_stat(rows: int = 1260) -> dict:
    return {
        "files": 1,
        "rows_in_files": rows,
        "nonempty": rows,
        "competitions": {"KOR_KLeague1"},
        "sample_paths": ["football-data/processed/KOR_KLeague1/official_results.csv"],
        "domains": {
            "KOR_KLeague1": {
                "rows": rows,
                "nonempty": rows,
                "seasons": {"2021", "2022", "2023", "2024", "2025", "2026"},
            }
        },
    }


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
        candidate = {
            "field": "round",
            "classification": engine.STRICT_PIT_CLASSIFICATION,
            "eligible_domain_specific_scopes": [{"competition": "KOR_KLeague1"}],
        }
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

    def test_field_name_only_cannot_create_strict_pit_candidate(self):
        contracts = {"round": {"classification": engine.STRICT_PIT_CLASSIFICATION}}
        classification = engine.classify_column("round", {"round"}, contracts)[0]
        self.assertEqual(classification, engine.UNVERIFIED_PIT_CLASSIFICATION)
        row = engine.assess_field_candidate(
            "round",
            candidate_stat(),
            all_columns={"round"},
            total_rows=1260,
            total_competitions=1,
            dataflow_index={},
            pit_contracts=contracts,
        )
        self.assertFalse(row["qualifies_existing_pit_safe_untested"])
        self.assertFalse(row["qualifies_domain_specific_pit_safe_untested"])

    def test_strict_pit_contract_fails_on_source_or_manifest_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.csv"
            profile = root / "profile.json"
            request = root / "request.json"
            source.write_text("round,season\n1,2021\n", encoding="utf-8")
            profile.write_text(json.dumps({"downloaded_at": "2026-07-27T06:57:22Z"}), encoding="utf-8")
            request.write_text(json.dumps({"raw_payload_archived": True}), encoding="utf-8")
            contract = {
                "classification": engine.STRICT_PIT_CLASSIFICATION,
                "source": "source.csv",
                "source_sha256": "0" * 64,
                "profile_manifest": "profile.json",
                "profile_manifest_sha256": file_sha(profile),
                "request_manifest": "request.json",
                "request_manifest_sha256": file_sha(request),
                "source_semantics": "official schedule round",
                "scope": ["KOR_KLeague1"],
                "prediction_time_availability": {"observed_at": "2021-01-01T00:00:00Z"},
                "historical_revision_risk": "bounded",
                "raw_payload_archived": True,
            }
            self.assertFalse(engine.verify_pit_field_contract(root, "round", contract)["verified"])
            contract["source_sha256"] = file_sha(source)
            contract["profile_manifest_sha256"] = "f" * 64
            self.assertFalse(engine.verify_pit_field_contract(root, "round", contract)["verified"])

    def test_no_timestamp_and_no_exception_degrades_to_reconstructed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.csv"
            profile = root / "profile.json"
            request = root / "request.json"
            source.write_text("round,season\n1,2021\n", encoding="utf-8")
            profile.write_text("{}", encoding="utf-8")
            request.write_text(json.dumps({"raw_payload_archived": True}), encoding="utf-8")
            contract = {
                "classification": engine.STRICT_PIT_CLASSIFICATION,
                "source": "source.csv",
                "source_sha256": file_sha(source),
                "profile_manifest": "profile.json",
                "profile_manifest_sha256": file_sha(profile),
                "request_manifest": "request.json",
                "request_manifest_sha256": file_sha(request),
                "source_semantics": "official schedule round",
                "scope": ["KOR_KLeague1"],
                "prediction_time_availability": {},
                "historical_revision_risk": "post-hoc reconstruction",
                "raw_payload_archived": True,
            }
            result = engine.verify_pit_field_contract(root, "round", contract)
            self.assertFalse(result["verified"])
            self.assertEqual(result["classification"], engine.RECONSTRUCTED_CLASSIFICATION)
            self.assertTrue(result["forward_pit_proof_required"])

    def test_reconstructed_candidate_preregisters_without_run_or_promotion_authority(self):
        candidate = {
            "field": "round",
            "classification": engine.RECONSTRUCTED_CLASSIFICATION,
            "eligible_domain_specific_scopes": [{"competition": "KOR_KLeague1", "rows": 1260, "season_count": 6}],
        }
        routes = {"unresolved": [{"id": "X"}], "blocks_exhausted": True}
        prereg = engine.make_preregistration([], [candidate], routes)
        self.assertEqual(prereg["features"][0]["scope"], engine.RECONSTRUCTED_SCOPE)
        self.assertFalse(prereg["run_authorized"])
        self.assertFalse(prereg["formal_promotion_authorized"])
        self.assertTrue(prereg["forward_pit_proof_required"])
        self.assertEqual(prereg["holdout_status"], "NOT_YET_PROVEN_UNTOUCHED")


if __name__ == "__main__":
    unittest.main()
