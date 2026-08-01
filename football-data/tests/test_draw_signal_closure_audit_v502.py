from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "audit" / "draw_signal_closure_engine_v502_r4.py"
spec = importlib.util.spec_from_file_location("draw_signal_closure_engine_v502_r4", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


def stat(*, rows=100, nonempty=100, competitions=("A",), seasons=("2021", "2022", "2023")):
    domains = {
        competition: {"rows": rows, "nonempty": nonempty, "seasons": set(seasons)}
        for competition in competitions
    }
    return {
        "files": len(competitions),
        "rows_in_files": rows * len(competitions),
        "nonempty": nonempty * len(competitions),
        "competitions": set(competitions),
        "sample_paths": ["a.csv"],
        "domains": domains,
    }


class DrawSignalClosureAuditTests(unittest.TestCase):
    def test_asian_handicap_aliases_are_market_references(self):
        columns = {
            "AHh", "AvgAHA", "AvgAHH", "B365AHA", "B365AHH", "B36CA",
            "BFEAHA", "BFEAHH", "MaxAHA", "MaxAHH", "PAHA", "PAHH",
        }
        for field in columns:
            self.assertEqual(
                module.classify_column(field, columns)[0],
                "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN",
                field,
            )

    def test_round_is_reconstructed_not_strict_pit_and_qualifies_research_domain(self):
        columns = {"round", "B365H", "B365D", "B365A"}
        self.assertIsNone(module.bookmaker_triplet_key("round", columns))
        row = module.assess_field_candidate(
            "round",
            stat(rows=1260, nonempty=1260, competitions=("KOR_KLeague1",), seasons=("2021", "2022", "2023", "2024", "2025", "2026")),
            all_columns=columns,
            total_rows=27616,
            total_competitions=17,
            dataflow_index={},
        )
        self.assertEqual(row["classification"], module.RECONSTRUCTED_CLASSIFICATION)
        self.assertFalse(row["qualifies_existing_pit_safe_untested"])
        self.assertTrue(row["qualifies_domain_specific_reconstructed_research_candidate"])
        self.assertTrue(row["forward_pit_proof_required"])
        self.assertFalse(row["formal_promotion_authorized"])
        self.assertEqual(row["eligible_domain_specific_scopes"][0]["competition"], "KOR_KLeague1")

    def test_unknown_field_remains_near_miss(self):
        row = module.assess_field_candidate(
            "mystery_feature",
            stat(rows=1000, nonempty=1000, competitions=("A", "B", "C")),
            all_columns={"mystery_feature"},
            total_rows=3000,
            total_competitions=3,
            dataflow_index={},
        )
        self.assertEqual(row["classification"], "UNKNOWN_PIT_STATUS")
        self.assertFalse(row["pit_safe"])
        self.assertFalse(row["qualifies_existing_pit_safe_untested"])
        self.assertFalse(row["qualifies_domain_specific_pit_safe_untested"])

    def test_disconnected_reader_and_scorer_in_same_script_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "research.py"
            script.write_text(
                "def reader(raw):\n"
                "    return raw.get('round')\n\n"
                "def unrelated(y, p):\n"
                "    return accuracy_score(y, p)\n",
                encoding="utf-8",
            )
            index = module.build_dataflow_index(root, scripts=[script], contracts={})
            self.assertIn("round", index)
            self.assertFalse(module.dataflow_chain_is_complete(index["round"]))

    def test_metadata_only_string_is_not_model_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "meta.py"
            script.write_text("FIELD='round'\nMETA={'field':'round'}\n", encoding="utf-8")
            index = module.build_dataflow_index(root, scripts=[script], contracts={})
            self.assertNotIn("round", index)

    def test_explicit_connected_def_use_contract_can_be_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "model.py"
            manifest = root / "result.json"
            script.write_text(
                "def build(raw):\n"
                "    raw_round = raw.get('round')\n"
                "    X_round = [raw_round]\n"
                "    return X_round\n\n"
                "def score(X_round, y):\n"
                "    return accuracy_score(y, X_round)\n\n"
                "# expanding-window\n",
                encoding="utf-8",
            )
            manifest.write_text(json.dumps({"result": {"status": "REJECTED"}, "split": "expanding-window"}), encoding="utf-8")
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            contract = {
                "script": "model.py",
                "script_sha256": sha(script),
                "field_access_function": "build",
                "feature_builder_function": "build",
                "feature_symbol": "X_round",
                "scorer_function": "score",
                "scorer_call": "accuracy_score",
                "scorer_argument_symbol": "X_round",
                "manifest": "result.json",
                "manifest_sha256": sha(manifest),
                "result_json_path": "result.status",
                "time_split_evidence": "expanding-window",
            }
            evidence = module.verify_explicit_field_contract(root, "round", contract)
            self.assertTrue(evidence["verified"], evidence)

    def test_unresolved_route_blocks_exhausted_when_raw_candidates_empty(self):
        routes = module.build_route_closure_ledger([
            {"id": "X", "status": None, "pit": "PIT_SAFE", "file_count": 1, "files": ["x.py"], "metrics": {}, "rejection_or_result": None, "family": "model"}
        ])
        decision, prereg = module.decide_and_preregister([], [], routes)
        self.assertEqual(decision, module.UNRESOLVED_DECISION)
        self.assertIsNone(prereg)

    def test_resolved_routes_and_no_candidates_allow_negative_decision(self):
        routes = module.build_route_closure_ledger([
            {"id": "X", "status": "REJECTED", "pit": "PIT_SAFE", "file_count": 1, "files": ["x.py"], "metrics": {"accuracy": 0.5}, "rejection_or_result": "REJECT_NO_GAIN", "family": "model"}
        ])
        decision, prereg = module.decide_and_preregister([], [], routes)
        self.assertEqual(decision, module.NEGATIVE_DECISION)
        self.assertIsNone(prereg)

    def test_reconstructed_domain_candidate_creates_non_authorized_preregistration(self):
        row = module.assess_field_candidate(
            "round",
            stat(rows=1260, nonempty=1260, competitions=("KOR_KLeague1",), seasons=("2021", "2022", "2023", "2024", "2025", "2026")),
            all_columns={"round"},
            total_rows=27616,
            total_competitions=17,
            dataflow_index={},
        )
        routes = module.build_route_closure_ledger([])
        decision, prereg = module.decide_and_preregister([], [row], routes)
        self.assertEqual(decision, module.POSITIVE_DECISION)
        self.assertFalse(prereg["run_authorized"])
        self.assertFalse(prereg["formal_promotion_authorized"])
        self.assertTrue(prereg["forward_pit_proof_required"])
        self.assertEqual(prereg["features"][0]["scope"], module.RECONSTRUCTED_SCOPE)
        self.assertEqual(prereg["holdout_status"], "NOT_YET_PROVEN_UNTOUCHED")


if __name__ == "__main__":
    unittest.main()
