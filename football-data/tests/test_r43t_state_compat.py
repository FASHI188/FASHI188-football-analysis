from __future__ import annotations

import ast
import os
from pathlib import Path
import sys
import unittest

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from components.r43t_dynamic_bivariate_state import (
    INITIAL_VAR,
    MAX_STATE_ABS,
    OBS_NOISE_FLOOR,
    PROCESS_VAR,
    STATE_APPLY_SHRINK,
    STATE_AR,
    R43TDynamicBivariateState,
)


CONSTANTS = {
    "STATE_AR",
    "PROCESS_VAR",
    "INITIAL_VAR",
    "OBS_NOISE_FLOOR",
    "STATE_APPLY_SHRINK",
    "MAX_STATE_ABS",
}
FUNCTIONS = {"observation_cov", "project_lambdas", "simultaneous_update"}


def load_pinned_original_scope():
    source_path = os.environ.get("R43T_SOURCE_FILE")
    if not source_path:
        raise RuntimeError("R43T_SOURCE_FILE is required for exact compatibility test")
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    selected = []
    seen_constants = set()
    seen_functions = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if names & CONSTANTS:
                selected.append(node)
                seen_constants |= names & CONSTANTS
        elif isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS:
            selected.append(node)
            seen_functions.add(node.name)
    if seen_constants != CONSTANTS or seen_functions != FUNCTIONS:
        raise RuntimeError(
            f"pinned R43T extraction incomplete constants={seen_constants} functions={seen_functions}"
        )
    scope = {"np": np, "Any": object}
    exec(compile(ast.Module(body=selected, type_ignores=[]), source_path, "exec"), scope)
    return scope


class R43TStateCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = load_pinned_original_scope()

    def test_constants_match_pinned_source(self):
        expected = {
            "STATE_AR": STATE_AR,
            "PROCESS_VAR": PROCESS_VAR,
            "INITIAL_VAR": INITIAL_VAR,
            "OBS_NOISE_FLOOR": OBS_NOISE_FLOOR,
            "STATE_APPLY_SHRINK": STATE_APPLY_SHRINK,
            "MAX_STATE_ABS": MAX_STATE_ABS,
        }
        for name, value in expected.items():
            self.assertEqual(value, self.original[name])

    def test_observation_cov_bitwise_matches_original(self):
        for lh, la in ((1.4, 0.9), (0.05, 0.05), (2.7, 1.8)):
            expected = self.original["observation_cov"](lh, la)
            actual = R43TDynamicBivariateState.observation_cov(lh, la)
            self.assertTrue(np.array_equal(actual, expected))

    def test_project_lambdas_bitwise_matches_original(self):
        cases = [
            (1.3, 1.0, np.array([0.0, 0.0])),
            (0.2, 0.1, np.array([-1.5, 1.5])),
            (2.8, 0.7, np.array([0.44, -0.31])),
        ]
        for lh, la, x in cases:
            expected = self.original["project_lambdas"](lh, la, x.copy())
            actual = R43TDynamicBivariateState.project_lambdas(lh, la, x.copy())
            self.assertEqual(actual, expected)

    def test_simultaneous_update_bitwise_matches_original(self):
        x_pred = np.array([0.12, -0.07], dtype=float)
        p_pred = np.array([[0.31, 0.0], [0.0, 0.31]], dtype=float)
        group = [
            {"lambda_home": 1.55, "lambda_away": 0.95, "hg": 2, "ag": 1},
            {"lambda_home": 1.10, "lambda_away": 1.20, "hg": 0, "ag": 0},
            {"lambda_home": 1.80, "lambda_away": 0.70, "hg": 1, "ag": 2},
        ]
        expected_x, expected_p = self.original["simultaneous_update"](x_pred.copy(), p_pred.copy(), group)
        actual_x, actual_p = R43TDynamicBivariateState.simultaneous_update(x_pred.copy(), p_pred.copy(), group)
        self.assertTrue(np.array_equal(actual_x, expected_x))
        self.assertTrue(np.array_equal(actual_p, expected_p))

    def test_group_lifecycle_matches_original_two_groups(self):
        state = R43TDynamicBivariateState()
        original_x = np.zeros(2, dtype=float)
        original_p = np.eye(2) * self.original["INITIAL_VAR"]
        groups = [
            [
                {"lambda_home": 1.55, "lambda_away": 0.95, "hg": 2, "ag": 1},
                {"lambda_home": 1.10, "lambda_away": 1.20, "hg": 0, "ag": 0},
            ],
            [
                {"lambda_home": 1.80, "lambda_away": 0.70, "hg": 1, "ag": 2},
            ],
        ]
        for group in groups:
            original_x_pred = self.original["STATE_AR"] * original_x
            original_p_pred = (self.original["STATE_AR"] ** 2) * original_p + np.eye(2) * self.original["PROCESS_VAR"]
            state.begin_group()
            projections = [state.project(r["lambda_home"], r["lambda_away"]) for r in group]
            for projection, row in zip(projections, group):
                expected = self.original["project_lambdas"](
                    row["lambda_home"], row["lambda_away"], original_x_pred
                )
                self.assertEqual((projection.lambda_home, projection.lambda_away), expected)
                self.assertEqual(projection.state_total_pred, float(original_x_pred[0]))
                self.assertEqual(projection.state_diff_pred, float(original_x_pred[1]))
            original_x, original_p = self.original["simultaneous_update"](
                original_x_pred, original_p_pred, group
            )
            state.settle_group(group)
            self.assertTrue(np.array_equal(state.x, original_x))
            self.assertTrue(np.array_equal(state.P, original_p))

    def test_same_kickoff_cannot_update_between_predictions(self):
        state = R43TDynamicBivariateState()
        state.begin_group()
        first = state.project(1.2, 1.0)
        second = state.project(1.2, 1.0)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(RuntimeError, "does not match frozen prediction count"):
            state.settle_group([{"lambda_home": 1.2, "lambda_away": 1.0, "hg": 1, "ag": 1}])

    def test_component_is_not_formally_enabled(self):
        self.assertFalse(R43TDynamicBivariateState.enabled)


if __name__ == "__main__":
    unittest.main()
