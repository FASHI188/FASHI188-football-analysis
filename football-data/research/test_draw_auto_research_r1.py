#!/usr/bin/env python3
from __future__ import annotations

import json
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
    return MatchRow("X", "2024", f"2024-01-{(index%28)+1:02d}", f"H{index}", f"A{index}", label, {
        "home_history_matches": float(index+10), "away_history_matches": float(index+12),
        "home_last5_matches": 5.0, "away_last5_matches": 5.0,
        "home_last5_gf": float("nan") if missing else 1.2 + index/100,
        "away_last5_gf": 1.1, "home_last5_ga": 1.0, "away_last5_ga": 1.3,
        "home_last5_ppg": 1.4 + index/200, "away_last5_ppg": 1.2,
        "home_elo_pre_match": 1500.0, "away_elo_pre_match": 1490.0,
        "elo_difference_with_home_advantage": float(70-index),
        "cold_start_flag": 0.0, "stage_unverified_flag": 0.0,
    })

class CatalogAndBaselineTests(unittest.TestCase):
    def test_catalog_is_200_structurally_unique_without_redundant_offset(self):
        c = candidate_catalog(); self.assertEqual(len(c), 200); self.assertEqual(len({x["candidate_sha256"] for x in c}), 200)
        self.assertTrue(all("draw_logit_offset" not in x for x in c)); self.assertEqual({x["basis_variant"] for x in c}, {"linear","signed_sqrt","tanh","quadratic"})
    def test_synthetic_basis_predictions_are_materially_distinct(self):
        r=generate_synthetic(); self.assertTrue(r["all_basis_predictions_distinct"]); self.assertGreater(r["minimum_pairwise_difference"],1e-6); self.assertEqual(len(set(r["basis_prediction_fingerprints"].values())),4)
    def test_fixed_baseline_is_candidate_independent(self):
        train=[row(i,("H","D","A")[i%3]) for i in range(60)];target=[row(i+100,("H","D","A")[i%3]) for i in range(15)]
        a,_,ra=baseline_predictions(train,target);b,_,rb=baseline_predictions(train,target);self.assertTrue(np.array_equal(a,b));self.assertEqual(ra["candidate_parameters_used"],[]);self.assertEqual(rb["candidate_parameters_used"],[])
    def test_preprocessing_decisions_do_not_change_after_evaluation_transform(self):
        train=[row(i,missing=(i==0)) for i in range(20)];test=[row(i+100,missing=True) for i in range(10)];p=Preprocessor.fit(train,["home_net","low_goal_proxy"],"quadratic");before=p.receipt();p.transform(test);self.assertEqual(before,p.receipt());self.assertEqual(p.evaluation_rows_used_for_decisions,0)

class PreflightProductionTests(unittest.TestCase):
    def test_current_production_workflow_reference(self): preflight.validate_workflow_reference(".github/workflows/football-draw-auto-research-r1.yml")
    def test_legacy_workflow_reference_fails_closed(self):
        legacy=".github/workflows/football-draw-"+"composite-prereg-r1.yml"
        with self.assertRaises(ValueError):preflight.validate_workflow_reference(legacy)
        self.assertNotIn(legacy,(HERE/"validate_draw_auto_research_preflight_r1.py").read_text())
    def test_workflow_contract_uses_workflow_level_concurrency_only(self):
        text=WORKFLOW.read_text();self.assertEqual(text.count("concurrency:"),1);self.assertLess(text.index("concurrency:"),text.index("jobs:"));self.assertIn("max-parallel: 1",text);self.assertIn("slot: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]",text);self.assertIn("cancel-in-progress: false",text)
    def test_workflow_has_real_artifact_fallback_and_exit_restore(self):
        text=WORKFLOW.read_text();self.assertIn("Restore latest compatible Artifact fallback",text);self.assertIn("draw_auto_research_restore_r1.py",text);self.assertIn("Restore original controller exit code",text);self.assertIn('exit "$code"',text)
    def test_production_preflight_entry_executes_directly(self):
        if not (ROOT/".git").exists():self.skipTest("full repository required")
        with tempfile.TemporaryDirectory() as temp:
            out=pathlib.Path(temp)/"preflight.json";cp=subprocess.run([sys.executable,str(HERE/"validate_draw_auto_research_preflight_r1.py"),"--mode","preauth","--output",str(out)],cwd=ROOT,text=True,capture_output=True);self.assertEqual(cp.returncode,0,cp.stdout+cp.stderr);self.assertEqual(json.loads(out.read_text())["status"],"PASS_ZERO_LABEL_PREFLIGHT")

class ExitAndRecoveryTests(unittest.TestCase):
    def test_exit_1_is_recorded_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            state=pathlib.Path(temp)/"state";code=pathlib.Path(temp)/"code";self.assertEqual(wrapper.run_and_capture([sys.executable,"-c","import sys;sys.exit(1)"],state,code),0);self.assertEqual(code.read_text().strip(),"1");self.assertEqual(json.loads((state/"run_failure_receipt.json").read_text())["exit_code"],1)
    def test_exit_2_is_recorded_and_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            state=pathlib.Path(temp)/"state";code=pathlib.Path(temp)/"code";wrapper.run_and_capture([sys.executable,"-c","import sys;sys.exit(2)"],state,code);self.assertEqual(code.read_text().strip(),"2");self.assertIn("RUN_FAILURE",(state/"ledger.jsonl").read_text())
    def test_exception_without_checkpoint_creates_receipt_and_stops_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            state=pathlib.Path(temp)/"state";code=pathlib.Path(temp)/"code"
            with mock.patch.object(wrapper.subprocess,"run",side_effect=RuntimeError("boom")):wrapper.run_and_capture(["x"],state,code)
            self.assertFalse((state/"checkpoint.json").exists());self.assertTrue((state/"run_failure_receipt.json").exists())
            with mock.patch("builtins.print") as p:controller.probe(state)
            self.assertFalse(json.loads(p.call_args.args[0])["should_continue"])
    def _artifact(self,root:pathlib.Path,auth="a",head="h",spec="s",identity="i"):
        state=root/"payload";state.mkdir(parents=True);checkpoint={"authorization_digest":auth,"frozen_code_head":head,"spec_digest":spec,"identity_digest":identity,"status":"RUNNING"};(state/"checkpoint.json").write_text(json.dumps(checkpoint));sha=restore.sha256_file(state/"checkpoint.json");(state/"manifest.json").write_text(json.dumps({"authorization_digest":auth,"frozen_code_head":head,"spec_digest":spec,"identity_digest":identity,"files":{"checkpoint.json":sha}}));return root
    def test_compatible_artifact_restores(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)/"artifact";self._artifact(root);target=pathlib.Path(temp)/"state";restore.restore_artifact(root,target,authorization_digest="a",frozen_code_head="h",spec_digest="s",identity_digest="i");self.assertTrue((target/"checkpoint.json").exists())
    def test_old_head_or_wrong_authorization_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)/"artifact";self._artifact(root)
            with self.assertRaises(ValueError):restore.validate_artifact(root,authorization_digest="wrong",frozen_code_head="h",spec_digest="s",identity_digest="i")
            with self.assertRaises(ValueError):restore.validate_artifact(root,authorization_digest="a",frozen_code_head="old",spec_digest="s",identity_digest="i")
    def test_artifact_hash_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=pathlib.Path(temp)/"artifact";self._artifact(root);(root/"payload"/"checkpoint.json").write_text("{}")
            with self.assertRaises(ValueError):restore.validate_artifact(root,authorization_digest="a",frozen_code_head="h",spec_digest="s",identity_digest="i")

class ChallengerGateTests(unittest.TestCase):
    def result(self,good:bool):
        delta={"Draw F1":0.02 if good else 0.0,"Macro-F1":0.01,"Accuracy":0.0,"Log Loss":-0.01,"Brier":-0.01,"RPS":-0.01,"Draw ECE":0.0};leagues={f"L{i}":{"delta":{"Draw F1":0.01,"RPS":-0.01}} for i in range(17)};return {"fold_count":51,"pooled_delta":delta,"league_results":leagues,"prediction_fingerprint_unique":True,"safety_gates":{"all_fits_converged":True,"probability_gates_pass":True,"evaluation_rows_used_for_preprocessing_decisions":0}}
    def test_gate_passes_only_pre_registered_quality_and_stability(self):self.assertEqual(evaluate_challenger_gate(self.result(True))["status"],"PASS");self.assertEqual(evaluate_challenger_gate(self.result(False))["status"],"FAIL")
    def test_final_report_outputs_no_challenger_when_none_qualify(self):
        c={"status":"STOPPED","stop_reason":"TEST","completed_candidates":[],"failed_candidates":[],"duplicate_prediction_candidates":[],"cumulative_runtime_seconds":0,"top5":[],"eligible_challenger":None};self.assertIn("NO_CHALLENGER",controller.final_markdown(c,{}))
    def test_incomplete_result_fails_closed(self):
        with self.assertRaises(ValueError):validate_candidate_result({"status":"COMPLETED","fold_count":51,"fold_results":[]})
if __name__=="__main__":unittest.main(verbosity=2)
