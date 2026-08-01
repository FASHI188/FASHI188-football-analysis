#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_module(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PREFLIGHT = load_module(
    "kor_round_preflight_r1",
    ROOT / "football-data/research/kor_round_preflight_r1.py",
)
RUNNER = load_module(
    "kor_round_ablation_r1",
    ROOT / "football-data/research/kor_round_ablation_r1.py",
)


class KorRoundResearchTests(unittest.TestCase):
    def test_contract_is_research_only_and_single_run(self):
        contract = json.loads(
            (ROOT / "football-data/research/kor_round_ablation_r1_contract.json").read_text(encoding="utf-8")
        )
        PREFLIGHT.validate_contract(contract)
        self.assertEqual(contract["run_policy"]["maximum_experiment_runs"], 1)
        self.assertEqual(contract["run_policy"]["formal_weight"], 0)
        self.assertFalse(contract["run_policy"]["formal_promotion_authorized"])
        self.assertFalse(contract["run_policy"]["merge_authorized"])

    def test_baseline_excludes_outcome_columns(self):
        contract = json.loads(
            (ROOT / "football-data/research/kor_round_ablation_r1_contract.json").read_text(encoding="utf-8")
        )
        features = set(contract["baseline_features"]["numeric"] + contract["baseline_features"]["categorical"])
        self.assertFalse(features.intersection({
            "label_home_goals", "label_away_goals", "label_total_goals",
            "label_total_goals_bin", "label_goal_difference", "label_result",
        }))

    def test_softmax_probabilities_conserve(self):
        p = RUNNER.predict_proba([1.0, 2.0], [[0.1, 0.2], [0.3, -0.1], [-0.2, 0.4]])
        self.assertAlmostEqual(sum(p), 1.0, places=12)
        self.assertTrue(all(0.0 < x < 1.0 for x in p))

    def test_metrics_perfect_predictions(self):
        actual = ["H", "D", "A"]
        probs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
        metrics = RUNNER.class_metrics(actual, probs)
        self.assertEqual(metrics["Accuracy"], 1.0)
        self.assertEqual(metrics["Macro-F1"], 1.0)
        self.assertEqual(metrics["Draw Precision"], 1.0)
        self.assertEqual(metrics["Draw Recall"], 1.0)
        self.assertEqual(metrics["Draw F1"], 1.0)
        self.assertAlmostEqual(metrics["Brier"], 0.0)
        self.assertAlmostEqual(metrics["RPS"], 0.0)

    def test_ridge_solver_returns_finite_coefficients(self):
        xs = [[1.0, 0.0], [1.0, 1.0], [1.0, -1.0], [1.0, 0.5]]
        labels = ["H", "D", "A", "H"]
        weights = RUNNER.fit_ridge_softmax(xs, labels, 10.0)
        self.assertEqual(len(weights), 3)
        self.assertTrue(all(math.isfinite(v) for row in weights for v in row))

    def test_pass_gate_requires_all_thresholds(self):
        thresholds = {
            "holdout_draw_f1_delta_min": 0.01,
            "holdout_rps_delta_max": -0.002,
            "holdout_accuracy_delta_min": -0.01,
            "holdout_log_loss_delta_max": 0.005,
            "holdout_brier_delta_max": 0.005,
            "seasons_with_nonnegative_draw_f1_delta_min": 2,
            "seasons_with_nonpositive_rps_delta_min": 2,
        }
        base_delta = {
            "Accuracy": 0.0, "Macro-F1": 0.0, "Draw Precision": 0.0,
            "Draw Recall": 0.0, "Draw F1": 0.02, "Log Loss": 0.0,
            "Brier": 0.0, "RPS": -0.003,
        }
        per = [
            {"season": "2023", "delta": dict(base_delta)},
            {"season": "2024", "delta": dict(base_delta)},
            {"season": "2025", "delta": dict(base_delta)},
        ]
        self.assertTrue(RUNNER.determine_pass(per, thresholds)["all_pass"])
        per[2]["delta"]["Draw F1"] = 0.0
        self.assertFalse(RUNNER.determine_pass(per, thresholds)["all_pass"])


if __name__ == "__main__":
    unittest.main()
