#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

import draw_auto_research_controller_r1 as controller
import draw_auto_research_run_wrapper_r1 as wrapper
import draw_auto_research_restore_r1 as restore
import validate_draw_auto_research_preflight_r1 as preflight
from draw_auto_research_baseline_r1 import baseline_predictions
from draw_auto_research_engine_r1 import MatchRow, Preprocessor, candidate_catalog, validate_candidate_result
from draw_auto_research_gate_r1 import evaluate_challenger_gate
from draw_auto_research_synthetic_evidence_r1 import generate as generate_synthetic

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "football-draw-auto-research-r1.yml"


def row(index: int, label: str = "D", missing: bool = False) -> MatchRow:
    return MatchRow("X", "2024", f"2024-01-{(index % 28) + 1:02d}", f"H{index}", f"A{index}", label, {
        "home_history_matches": float(index + 10), "away_history_matches": float(index + 12),
        "home_last5_matches": 5.0, "away_last5_matches": 5.0,
        "home_last5_gf": float("nan") if missing else 1.2 + index / 100,
        "away_last5_gf": 1.1, "home_last5_ga": 1.0, "away_last5_ga": 1.3,
        "home_last5_ppg": 1.4 + index / 200, "away_last5_ppg": 1.2,
        "home_elo_pre_match": 1500.0, "away_elo_pre_match": 1490.0,
        "elo_difference_with_home_advantage": float(70 - index),
        "cold_start_flag": 0.0, "stage_unverified_flag": 0.0,
    })


class CatalogAndBaselineTests(unittest.TestCase):
    def test_catalog_is_200_structurally_unique_without_redundant_offset(self):
        catalog = candidate_catalog()
        self.assertEqual(len(catalog), 200)
        self.assertEqual(len({item["candidate_sha256"] for item in catalog}), 200)
        self.assertTrue(all("draw_logit_offset" not in item for item in catalog))
        self.assertEqual({item["basis_variant"] for item in catalog}, {"linear", "signed_sqrt", "tanh", "quadratic"})

    def test_synthetic_basis_predictions_are_materially_distinct(self):
        result = generate_synthetic()
        self.assertTrue(result["all_basis_predictions_distinct"])
        self.assertGreater(result["minimum_pairwise_difference"], 1e-6)
        self.assertEqual(len(set(result["basis_prediction_fingerprints"].values())), 4)

    def test_fixed_baseline_is_candidate_independent(self):
        train = [row(i, ("H", "D", "A")[i % 3]) for i in range(60)]
        target = [row(i + 100, ("H", "D", "A")[i % 3]) for i in range(15)]
        first, _, first_receipt = baseline_predictions(train, target)
        second, _, second_receipt = baseline_predictions(train, target)
        self.assertTrue(np.array_equal(first, second))
        self.assertEqual(first_receipt["candidate_parameters_used"], [])
        self.assertEqual(second_receipt["candidate_parameters_used"], [])

    def test_preprocessing_decisions_do_not_change_after_evaluation_transform(self):
        train = [row(i, missing=(i == 0)) for i in range(20)]
        target = [row(i + 100, missing=True) for i in range(10)]
        processor = Preprocessor.fit(train, ["home_net", "low_goal_proxy"], "quadratic")
        before = processor.receipt()
        processor.transform(target)
        self.assertEqual(before, processor.receipt())
        self.assertEqual(processor.evaluation_rows_used_for_decisions, 0)


class WorkflowContractTests(unittest.TestCase):
    def test_current_production_workflow_reference(self):
        preflight.validate_workflow_reference(".github/workflows/football-draw-auto-research-r1.yml")

    def test_legacy_workflow_reference_fails_closed(self):
        legacy = ".github/workflows/football-draw-" + "composite-prereg-r1.yml"
        with self.assertRaises(ValueError):
            preflight.validate_workflow_reference(legacy)
        self.assertNotIn(legacy, (HERE / "validate_draw_auto_research_preflight_r1.py").read_text(encoding="utf-8"))

    def test_workflow_contract_uses_branch_queue_concurrency(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("concurrency:"), 1)
        self.assertLess(text.index("concurrency:"), text.index("jobs:"))
        block = text[text.index("concurrency:"):text.index("jobs:")]
        self.assertIn("${{ github.workflow }}", block)
        self.assertIn("${{ github.ref_name }}", block)
        self.assertNotIn("github.sha", block)
        self.assertIn("queue: max", block)
        self.assertIn("cancel-in-progress: false", block)
        self.assertIn("max-parallel: 1", text)

    def test_workflow_event_contract_push_authorized_runs(self):
        result = preflight.resolve_workflow_event("push", "preflight", True)
        self.assertTrue(result["authorized"])
        self.assertTrue(result["research_allowed"])
        self.assertEqual(result["preflight_mode"], "authorized")

    def test_workflow_event_contract_dispatch_research_authorized_runs(self):
        result = preflight.resolve_workflow_event("workflow_dispatch", "research", True)
        self.assertTrue(result["research_allowed"])

    def test_workflow_event_contract_dispatch_preflight_never_runs_research(self):
        result = preflight.resolve_workflow_event("workflow_dispatch", "preflight", True)
        self.assertTrue(result["authorized"])
        self.assertFalse(result["research_allowed"])
        self.assertEqual(result["preflight_mode"], "authorized")

    def test_workflow_artifact_fallback_is_fail_closed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("set +e", text)
        self.assertIn("draw_auto_research_artifact_fallback_r1.py", text)
        self.assertIn("--validate-existing", text)

    def test_workflow_cross_platform_determinism_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("cross-platform-determinism", text)
        self.assertIn("windows-latest", text)
        self.assertIn("canonical_json_sha256", text)

    def test_production_preflight_entry_executes_directly(self):
        if not (ROOT / ".git").exists():
            self.skipTest("full repository required")
        with tempfile.TemporaryDirectory() as temp:
            output = pathlib.Path(temp) / "preflight.json"
            completed = subprocess.run(
                [sys.executable, str(HERE / "validate_draw_auto_research_preflight_r1.py"), "--mode", "preauth", "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "PASS_ZERO_LABEL_PREFLIGHT")


class ExitAndRecoveryTests(unittest.TestCase):
    def _running_checkpoint(self) -> dict:
        return {
            "status": "RUNNING",
            "stop_reason": None,
            "authorization_digest": "a",
            "frozen_code_head": "h",
            "spec_digest": "s",
            "identity_digest": "i",
            "completed_candidates": [],
            "failed_candidates": [],
            "duplicate_prediction_candidates": [],
            "batch_index": 0,
            "top5": [],
            "eligible_challenger": None,
            "cumulative_runtime_seconds": 0.0,
            "active_batch_records": [],
        }

    def test_running_checkpoint_exit_1_terminalizes_and_probe_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp) / "state"
            state.mkdir()
            controller.atomic_json(state / "checkpoint.json", self._running_checkpoint())
            code_file = pathlib.Path(temp) / "code"
            wrapper.run_and_capture([sys.executable, "-c", "import sys;sys.exit(1)"], state, code_file)
            checkpoint = json.loads((state / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], "FAILED_RUNTIME")
            self.assertEqual(checkpoint["stop_reason"], "CONTROLLER_EXIT_1_RUNTIME_FAILURE")
            self.assertTrue((state / "run_failure_receipt.json").is_file())
            manifest = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("run_failure_receipt.json", manifest["files"])
            self.assertIn("ledger.jsonl", manifest["files"])
            with mock.patch("builtins.print") as printed:
                controller.probe(state)
            self.assertFalse(json.loads(printed.call_args.args[0])["should_continue"])

    def test_running_checkpoint_exit_2_terminalizes_safety_and_probe_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp) / "state"
            state.mkdir()
            controller.atomic_json(state / "checkpoint.json", self._running_checkpoint())
            code_file = pathlib.Path(temp) / "code"
            wrapper.run_and_capture([sys.executable, "-c", "import sys;sys.exit(2)"], state, code_file)
            checkpoint = json.loads((state / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], "FAILED_SAFETY")
            self.assertEqual(checkpoint["stop_reason"], "CONTROLLER_EXIT_2_SAFETY_FAILURE")
            with mock.patch("builtins.print") as printed:
                controller.probe(state)
            self.assertFalse(json.loads(printed.call_args.args[0])["should_continue"])

    def test_probe_prioritizes_failure_receipt_over_running_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp)
            controller.atomic_json(state / "checkpoint.json", self._running_checkpoint())
            controller.atomic_json(state / "run_failure_receipt.json", {"status": "FAILED", "stop_reason": "X"})
            with mock.patch("builtins.print") as printed:
                controller.probe(state)
            value = json.loads(printed.call_args.args[0])
            self.assertEqual(value["stop_reason"], "X")
            self.assertFalse(value["should_continue"])

    def test_exception_without_checkpoint_creates_receipt_manifest_and_stops_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp) / "state"
            code = pathlib.Path(temp) / "code"
            with mock.patch.object(wrapper.subprocess, "run", side_effect=RuntimeError("boom")):
                wrapper.run_and_capture(["x"], state, code)
            self.assertFalse((state / "checkpoint.json").exists())
            self.assertTrue((state / "run_failure_receipt.json").exists())
            self.assertTrue((state / "manifest.json").exists())
            with mock.patch("builtins.print") as printed:
                controller.probe(state)
            self.assertFalse(json.loads(printed.call_args.args[0])["should_continue"])

    def test_controller_safety_validation_reaches_exit_2(self):
        spec = {"budget": {"maximum_candidates": 1, "batch_size": 1, "maximum_cumulative_seconds": 100, "minimum_batch_improvement": 0.001, "maximum_stagnant_batches": 3}, "challenger_gate": {}}
        identity = {}
        authorization = {"frozen_code_head": "h"}
        checkpoint = controller.initial_checkpoint(spec, authorization, identity)
        candidate = {"candidate_id": "C001", "candidate_sha256": "x"}
        with tempfile.TemporaryDirectory() as temp, \
             mock.patch.object(controller, "read_json", side_effect=lambda path: spec if path == controller.SPEC_PATH else identity), \
             mock.patch.object(controller, "validate_authorization", return_value=authorization), \
             mock.patch.object(controller, "load_checkpoint", return_value=checkpoint), \
             mock.patch.object(controller, "candidate_catalog", return_value=[candidate]), \
             mock.patch.object(controller, "load_rows", return_value=[]), \
             mock.patch.object(controller, "build_outer_folds", return_value=[object()]), \
             mock.patch.object(controller, "evaluate_candidate", return_value={"prediction_fingerprint_unique": True}), \
             mock.patch.object(controller, "validate_candidate_result", side_effect=ValueError("probability safety failure")):
            code = controller.run_batch(pathlib.Path(temp), "1", "1", 60)
            self.assertEqual(code, 2)
            terminal = json.loads((pathlib.Path(temp) / "checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(terminal["status"], "FAILED_SAFETY")
            self.assertEqual(terminal["stop_reason"], "SAFETY_GATE_FAILURE")

    def _artifact(self, root: pathlib.Path, auth: str = "a", head: str = "h", spec: str = "s", identity: str = "i") -> pathlib.Path:
        state = root / "payload"
        state.mkdir(parents=True)
        checkpoint = {
            "authorization_digest": auth,
            "frozen_code_head": head,
            "spec_digest": spec,
            "identity_digest": identity,
            "status": "RUNNING",
            "completed_candidates": [],
            "failed_candidates": [],
            "duplicate_prediction_candidates": [],
            "batch_index": 0,
        }
        (state / "checkpoint.json").write_text(json.dumps(checkpoint) + "\n", encoding="utf-8", newline="\n")
        sha = restore.sha256_file(state / "checkpoint.json")
        manifest = {"authorization_digest": auth, "frozen_code_head": head, "spec_digest": spec, "identity_digest": identity, "files": {"checkpoint.json": sha}}
        (state / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8", newline="\n")
        return root

    def test_compatible_artifact_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            target = pathlib.Path(temp) / "state"
            restore.restore_artifact(root, target, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")
            self.assertTrue((target / "checkpoint.json").exists())

    def test_old_head_or_wrong_authorization_artifact_rejected_as_incompatible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            with self.assertRaises(restore.ArtifactIncompatibleError):
                restore.validate_artifact(root, authorization_digest="wrong", frozen_code_head="h", spec_digest="s", identity_digest="i")
            with self.assertRaises(restore.ArtifactIncompatibleError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="old", spec_digest="s", identity_digest="i")

    def test_artifact_hash_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            (root / "payload" / "checkpoint.json").write_text("{}\n", encoding="utf-8", newline="\n")
            with self.assertRaises(restore.ArtifactIntegrityError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")

    def test_unregistered_extra_file_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            (root / "payload" / "extra.txt").write_text("extra\n", encoding="utf-8", newline="\n")
            with self.assertRaises(restore.ArtifactIntegrityError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")

    def test_path_traversal_registration_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            manifest_path = root / "payload" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["../escape"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8", newline="\n")
            with self.assertRaises(restore.ArtifactIntegrityError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")

    @unittest.skipIf(os.name == "nt", "symlink creation policy differs on Windows runners")
    def test_symbolic_link_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            target = root / "payload" / "target.txt"
            target.write_text("x\n", encoding="utf-8", newline="\n")
            link = root / "payload" / "link.txt"
            link.symlink_to(target.name)
            with self.assertRaises(restore.ArtifactIntegrityError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")

    def test_checkpoint_result_reference_must_be_registered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp) / "artifact"
            self._artifact(root)
            checkpoint_path = root / "payload" / "checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["completed_candidates"] = ["C001"]
            checkpoint_path.write_text(json.dumps(checkpoint) + "\n", encoding="utf-8", newline="\n")
            manifest_path = root / "payload" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"]["checkpoint.json"] = restore.sha256_file(checkpoint_path)
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8", newline="\n")
            with self.assertRaises(restore.ArtifactIntegrityError):
                restore.validate_artifact(root, authorization_digest="a", frozen_code_head="h", spec_digest="s", identity_digest="i")


class ChallengerGateTests(unittest.TestCase):
    def result(self, good: bool):
        delta = {"Draw F1": 0.02 if good else 0.0, "Macro-F1": 0.01, "Accuracy": 0.0, "Log Loss": -0.01, "Brier": -0.01, "RPS": -0.01, "Draw ECE": 0.0}
        leagues = {f"L{i}": {"delta": {"Draw F1": 0.01, "RPS": -0.01}} for i in range(17)}
        return {"fold_count": 51, "pooled_delta": delta, "league_results": leagues, "prediction_fingerprint_unique": True, "safety_gates": {"all_fits_converged": True, "probability_gates_pass": True, "evaluation_rows_used_for_preprocessing_decisions": 0}}

    def test_gate_passes_only_pre_registered_quality_and_stability(self):
        self.assertEqual(evaluate_challenger_gate(self.result(True))["status"], "PASS")
        self.assertEqual(evaluate_challenger_gate(self.result(False))["status"], "FAIL")

    def test_final_report_outputs_no_challenger_when_none_qualify(self):
        checkpoint = {"status": "STOPPED", "stop_reason": "TEST", "completed_candidates": [], "failed_candidates": [], "duplicate_prediction_candidates": [], "cumulative_runtime_seconds": 0, "top5": [], "eligible_challenger": None}
        self.assertIn("NO_CHALLENGER", controller.final_markdown(checkpoint, {}))

    def test_incomplete_result_fails_closed(self):
        with self.assertRaises(ValueError):
            validate_candidate_result({"status": "COMPLETED", "fold_count": 51, "fold_results": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
