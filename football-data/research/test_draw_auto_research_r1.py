#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

import numpy as np

import draw_auto_research_controller_r1 as controller
from draw_auto_research_engine_r1 import MatchRow, Preprocessor, build_outer_folds, candidate_catalog, validate_candidate_result
from draw_auto_research_math_r1 import fit_offset_logistic, hda_from_draw_and_elo, metrics


def row(comp: str, season: str, index: int, label: str = "D", missing: bool = False) -> MatchRow:
    values = {
        "home_history_matches": float(index + 1), "away_history_matches": float(index + 2),
        "home_last5_matches": 5.0, "away_last5_matches": 5.0,
        "home_last5_gf": float("nan") if missing else 1.2, "away_last5_gf": 1.1,
        "home_last5_ga": 1.0, "away_last5_ga": 1.3,
        "home_last5_ppg": 1.4, "away_last5_ppg": 1.2,
        "home_elo_pre_match": 1500.0, "away_elo_pre_match": 1490.0,
        "elo_difference_with_home_advantage": 70.0,
        "cold_start_flag": 0.0, "stage_unverified_flag": 0.0,
    }
    return MatchRow(comp, season, f"202{season[-1]}-01-{index+1:02d}", f"H{index}", f"A{index}", label, values)


class AutoResearchTests(unittest.TestCase):
    def test_candidate_catalog_exactly_200_and_unique(self) -> None:
        catalog = candidate_catalog()
        self.assertEqual(len(catalog), 200)
        self.assertEqual(len({item["candidate_sha256"] for item in catalog}), 200)
        self.assertEqual(catalog[0]["candidate_id"], "C001")
        self.assertEqual(catalog[-1]["candidate_id"], "C200")

    def test_catalog_is_deterministic(self) -> None:
        self.assertEqual(candidate_catalog(), candidate_catalog())

    def test_no_random_split_contract(self) -> None:
        spec = json.loads((pathlib.Path(__file__).parent / "draw_auto_research_spec_r1.json").read_text())
        self.assertFalse(spec["validation"]["random_split"])
        self.assertEqual(spec["data_status"], "VIEWED_DEVELOPMENT_DATA")

    def test_builds_51_rolling_outer_folds(self) -> None:
        rows = []
        for comp_index in range(17):
            comp = f"C{comp_index:02d}"
            for season_index in range(1, 6):
                season = str(2020 + season_index)
                for index in range(3):
                    rows.append(row(comp, season, index, ("H", "D", "A")[index % 3]))
        folds = build_outer_folds(rows)
        self.assertEqual(len(folds), 51)
        self.assertTrue(all(fold.target_season not in fold.prior_seasons for fold in folds))

    def test_preprocessor_missing_indicator_uses_training_only(self) -> None:
        train = [row("X", "2021", i, missing=(i == 0)) for i in range(5)]
        evaluation = [row("X", "2022", i, missing=True) for i in range(5)]
        processor = Preprocessor.fit(train, ["home_net", "low_goal_proxy"])
        before = processor.receipt()
        processor.transform(evaluation)
        after = processor.receipt()
        self.assertEqual(before, after)
        self.assertEqual(after["evaluation_rows_used_for_decisions"], 0)

    def test_evaluation_missingness_cannot_add_indicator(self) -> None:
        train = [row("X", "2021", i, missing=False) for i in range(5)]
        evaluation = [row("X", "2022", i, missing=True) for i in range(5)]
        processor = Preprocessor.fit(train, ["home_net"])
        self.assertNotIn("home_net", processor.missing_indicator_features)
        self.assertEqual(processor.transform(evaluation).shape[1], len(processor.kept_features))

    def test_duplicate_feature_decision_uses_training_only(self) -> None:
        train = [row("X", "2021", i) for i in range(5)]
        processor = Preprocessor.fit(train, ["elo_abs", "elo_abs_sq", "stage_unverified"])
        self.assertEqual(processor.evaluation_rows_used_for_decisions, 0)
        self.assertIn("stage_unverified", processor.dropped)

    def test_logistic_fit_probability_gate(self) -> None:
        x = np.asarray([[i / 10.0] for i in range(40)], dtype=float)
        y = np.asarray([0.0] * 20 + [1.0] * 20)
        fit = fit_offset_logistic(x, y, np.zeros(40), l2=1.0, positive_weight=1.0)
        self.assertTrue(fit.converged)
        self.assertTrue(fit.probability_gate_pass)

    def test_hda_probability_conservation(self) -> None:
        result = hda_from_draw_and_elo(np.asarray([0.2, 0.3]), np.asarray([60.0, -30.0]))
        self.assertTrue(np.allclose(result.sum(axis=1), 1.0))

    def test_metrics_require_complete_probabilities(self) -> None:
        with self.assertRaises(ValueError):
            metrics(np.asarray([[0.5, 0.5, 0.5]]), ["D"])

    def test_missing_candidate_sections_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            validate_candidate_result({"status": "COMPLETED", "fold_count": 51, "fold_results": []})

    def test_leakage_receipt_fails_closed(self) -> None:
        fake = {
            "status": "COMPLETED", "fold_count": 51, "fold_results": [{}] * 51,
            "league_results": {"X": {}},
            "safety_gates": {"all_51_folds_present": True, "all_fits_converged": True,
                             "probability_gates_pass": True,
                             "evaluation_rows_used_for_preprocessing_decisions": 1},
            "pooled_candidate_metrics": {key: 0.1 for key in (
                "Accuracy", "Macro-F1", "Draw Precision", "Draw Recall", "Draw F1",
                "Log Loss", "Brier", "RPS", "Draw ECE", "Top-label ECE")},
        }
        with self.assertRaises(ValueError):
            validate_candidate_result(fake)

    def test_budget_stop_at_200(self) -> None:
        spec = {"budget": {"maximum_candidates": 200, "maximum_cumulative_seconds": 21600, "maximum_stagnant_batches": 3}}
        checkpoint = {"completed_candidates": [f"C{i:03d}" for i in range(1, 201)], "failed_candidates": [], "cumulative_runtime_seconds": 10, "consecutive_stagnant_batches": 0, "safety_failure": None}
        self.assertEqual(controller.stop_reason(checkpoint, spec), "MAXIMUM_200_CANDIDATES_REACHED")

    def test_budget_stop_at_six_hours(self) -> None:
        spec = {"budget": {"maximum_candidates": 200, "maximum_cumulative_seconds": 21600, "maximum_stagnant_batches": 3}}
        checkpoint = {"completed_candidates": [], "failed_candidates": [], "cumulative_runtime_seconds": 21600, "consecutive_stagnant_batches": 0, "safety_failure": None}
        self.assertEqual(controller.stop_reason(checkpoint, spec), "CUMULATIVE_6_HOURS_REACHED")

    def test_stagnation_stop_after_three_batches(self) -> None:
        spec = {"budget": {"maximum_candidates": 200, "maximum_cumulative_seconds": 21600, "maximum_stagnant_batches": 3}}
        checkpoint = {"completed_candidates": [], "failed_candidates": [], "cumulative_runtime_seconds": 1, "consecutive_stagnant_batches": 3, "safety_failure": None}
        self.assertEqual(controller.stop_reason(checkpoint, spec), "THREE_CONSECUTIVE_STAGNANT_BATCHES")

    def test_checkpoint_identity_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp)
            controller.atomic_json(state / "checkpoint.json", {"authorization_digest": "wrong", "spec_digest": "x", "identity_digest": "y", "user_authorization_record": "rec0WJJzXiuDvAqSb", "formal_weight": 0, "repository_writeback": 0})
            with self.assertRaises(ValueError):
                controller.load_checkpoint(state, {}, {}, {})

    def test_duplicate_start_probe_resumes_not_reinitializes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp)
            controller.atomic_json(state / "checkpoint.json", {"status": "RUNNING", "completed_candidates": ["C001"]})
            with mock.patch("builtins.print") as output:
                controller.probe(state)
            payload = json.loads(output.call_args.args[0])
            self.assertTrue(payload["should_dispatch_successor"])
            self.assertEqual(json.loads((state / "checkpoint.json").read_text())["completed_candidates"], ["C001"])

    def test_stopped_probe_does_not_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp)
            controller.atomic_json(state / "checkpoint.json", {"status": "STOPPED"})
            with mock.patch("builtins.print") as output:
                controller.probe(state)
            self.assertFalse(json.loads(output.call_args.args[0])["should_dispatch_successor"])

    def test_manifest_detects_result_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state = pathlib.Path(temp)
            (state / "results").mkdir()
            (state / "results" / "C001.json").write_text("{}\n")
            checkpoint = {"status": "RUNNING", "stop_reason": None, "authorization_digest": "a", "frozen_code_head": "b" * 40}
            controller.build_manifest(state, checkpoint, {}, {})
            manifest = json.loads((state / "manifest.json").read_text())
            self.assertIn("results/C001.json", manifest["files"])

    def test_final_report_marks_viewed_data(self) -> None:
        checkpoint = {"status": "STOPPED", "stop_reason": "TEST", "completed_candidates": [], "failed_candidates": [], "cumulative_runtime_seconds": 0, "top5": []}
        report = controller.final_markdown(checkpoint, pathlib.Path("."), {})
        self.assertIn("VIEWED_DEVELOPMENT_DATA", report)
        self.assertIn("不是未来盲测", report)

    def test_authorization_record_is_fixed(self) -> None:
        spec = json.loads((pathlib.Path(__file__).parent / "draw_auto_research_spec_r1.json").read_text())
        self.assertEqual(spec["user_authorization_record"], "rec0WJJzXiuDvAqSb")
        self.assertEqual(spec["formal_weight"], 0)

    def test_recovery_slots_are_more_than_batches(self) -> None:
        workflow = (pathlib.Path(__file__).parents[2] / ".github/workflows/football-draw-composite-prereg-r1.yml").read_text()
        self.assertIn("slot: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
