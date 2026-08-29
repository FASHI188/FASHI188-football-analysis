from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_FILE = REPO_ROOT / "football-data" / "components" / "r43r_football_residual.py"
SOURCE_FILE = Path(os.environ["R43R_SOURCE_FILE"])
EXPECTED_SOURCE_COMMIT = "b76ecc841e7b52320d73a7045874765386c2f8e6"
EXPECTED_SOURCE_BLOB = "8748e795bb92780c47af934c3187db14c254a415"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestR43RExactCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = load_module("r43r_source", SOURCE_FILE)
        cls.gov = load_module("r43r_governance", COMPONENT_FILE)

    def test_source_identity_and_default_disabled(self):
        self.assertEqual(self.gov.SOURCE_COMMIT, EXPECTED_SOURCE_COMMIT)
        self.assertEqual(self.gov.SOURCE_BLOB_SHA, EXPECTED_SOURCE_BLOB)
        self.assertEqual(self.gov.RIDGE_PENALTY, self.src.RIDGE_PENALTY)
        self.assertEqual(self.gov.BETA_BOUNDS, self.src.BETA_BOUNDS)
        self.assertFalse(self.gov.R43RFootballResidual.enabled)
        self.assertFalse(self.gov.R43RFootballResidual.historical_architecture_gate_passed)
        self.assertFalse(self.gov.R43RFootballResidual.historical_breakthrough_candidate)

    def test_probs_exact(self):
        cases = [
            {"home": 0.52, "draw": 0.27, "away": 0.21},
            {"home": 52.0, "draw": 27.0, "away": 21.0},
            {"home": 0.333333333, "draw": 0.333333334, "away": 0.333333333},
        ]
        for raw in cases:
            self.assertEqual(self.gov.probs(raw), self.src.probs(raw))

    def test_residual_prob_exact(self):
        cases = [
            (
                {"home": 0.50, "draw": 0.28, "away": 0.22},
                {"home": 0.44, "draw": 0.31, "away": 0.25},
                -0.5,
            ),
            (
                {"home": 0.41, "draw": 0.30, "away": 0.29},
                {"home": 0.48, "draw": 0.25, "away": 0.27},
                0.0,
            ),
            (
                {"home": 0.36, "draw": 0.32, "away": 0.32},
                {"home": 0.30, "draw": 0.38, "away": 0.32},
                0.5,
            ),
        ]
        for pm, pf, beta in cases:
            self.assertEqual(
                self.gov.residual_prob(pm, pf, beta),
                self.src.residual_prob(pm, pf, beta),
            )

    def test_fit_beta_exact(self):
        train = [
            {"market": {"home": 0.50, "draw": 0.28, "away": 0.22}, "football": {"home": 0.44, "draw": 0.31, "away": 0.25}, "y": "home"},
            {"market": {"home": 0.42, "draw": 0.30, "away": 0.28}, "football": {"home": 0.47, "draw": 0.27, "away": 0.26}, "y": "draw"},
            {"market": {"home": 0.34, "draw": 0.31, "away": 0.35}, "football": {"home": 0.30, "draw": 0.36, "away": 0.34}, "y": "away"},
            {"market": {"home": 0.58, "draw": 0.24, "away": 0.18}, "football": {"home": 0.54, "draw": 0.26, "away": 0.20}, "y": "home"},
            {"market": {"home": 0.29, "draw": 0.31, "away": 0.40}, "football": {"home": 0.33, "draw": 0.29, "away": 0.38}, "y": "away"},
            {"market": {"home": 0.46, "draw": 0.29, "away": 0.25}, "football": {"home": 0.43, "draw": 0.34, "away": 0.23}, "y": "draw"},
            {"market": {"home": 0.39, "draw": 0.30, "away": 0.31}, "football": {"home": 0.41, "draw": 0.27, "away": 0.32}, "y": "home"},
            {"market": {"home": 0.31, "draw": 0.30, "away": 0.39}, "football": {"home": 0.27, "draw": 0.34, "away": 0.39}, "y": "draw"},
        ]
        self.assertEqual(self.gov.fit_beta(train), self.src.fit_beta(train))

    def test_class_apply_is_source_exact(self):
        pm = {"home": 0.47, "draw": 0.29, "away": 0.24}
        pf = {"home": 0.43, "draw": 0.33, "away": 0.24}
        beta = 0.123456789
        self.assertEqual(
            self.gov.R43RFootballResidual.apply(pm, pf, beta),
            self.src.residual_prob(pm, pf, beta),
        )


if __name__ == "__main__":
    unittest.main()
