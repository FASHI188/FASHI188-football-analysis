from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT_FILE = REPO_ROOT / "football-data" / "components" / "r43u_diagonal_gain.py"
SOURCE_FILE = Path(os.environ["R43U_SOURCE_FILE"])
EXPECTED_SOURCE_COMMIT = "3983d9168ca51234a810ede379d97c62afa3fff8"
EXPECTED_SOURCE_BLOB = "4ad46cca4acb618068f6db2601cf96bad4109698"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestR43UExactCompatibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # R43U0 imports R43T0/R43Q0 only for build_scored/run; inflate itself is
        # self-contained. Stub those imports so the pinned source can be loaded
        # without pulling unrelated historical code into this compatibility gate.
        cls._old_t0 = sys.modules.get("run_r43t0")
        cls._old_q0 = sys.modules.get("run_r43q0")
        sys.modules["run_r43t0"] = types.ModuleType("run_r43t0")
        sys.modules["run_r43q0"] = types.ModuleType("run_r43q0")
        cls.src = load_module("r43u_source", SOURCE_FILE)
        cls.gov = load_module("r43u_governance", COMPONENT_FILE)

    @classmethod
    def tearDownClass(cls):
        if cls._old_t0 is None:
            sys.modules.pop("run_r43t0", None)
        else:
            sys.modules["run_r43t0"] = cls._old_t0
        if cls._old_q0 is None:
            sys.modules.pop("run_r43q0", None)
        else:
            sys.modules["run_r43q0"] = cls._old_q0

    def test_source_identity_and_default_disabled(self):
        self.assertEqual(self.gov.SOURCE_COMMIT, EXPECTED_SOURCE_COMMIT)
        self.assertEqual(self.gov.SOURCE_BLOB_SHA, EXPECTED_SOURCE_BLOB)
        self.assertEqual(self.gov.DIAGONAL_FACTOR, self.src.DIAGONAL_FACTOR)
        self.assertEqual(self.gov.DIAGONAL_FACTOR, 1.25)
        self.assertFalse(self.gov.R43UDiagonalGain.enabled)
        self.assertTrue(self.gov.R43UDiagonalGain.historical_architecture_gate_passed)
        self.assertFalse(self.gov.R43UDiagonalGain.historical_full_volume_53pct_target_met)
        self.assertFalse(self.gov.R43UDiagonalGain.forward_confirmation_passed)

    def test_inflate_exact_square_matrices(self):
        matrices = [
            np.array([[0.20, 0.10], [0.30, 0.40]], dtype=float),
            np.arange(1, 10, dtype=float).reshape(3, 3),
            np.array(
                [
                    [0.11, 0.07, 0.03, 0.01],
                    [0.12, 0.14, 0.08, 0.02],
                    [0.06, 0.10, 0.09, 0.05],
                    [0.02, 0.03, 0.04, 0.03],
                ],
                dtype=float,
            ),
        ]
        for matrix in matrices:
            np.testing.assert_array_equal(self.gov.inflate(matrix), self.src.inflate(matrix))

    def test_inflate_exact_rectangular_matrix(self):
        matrix = np.arange(1, 13, dtype=float).reshape(3, 4)
        np.testing.assert_array_equal(self.gov.inflate(matrix), self.src.inflate(matrix))

    def test_input_not_mutated(self):
        matrix = np.array([[0.4, 0.1], [0.2, 0.3]], dtype=float)
        before = matrix.copy()
        self.gov.inflate(matrix)
        np.testing.assert_array_equal(matrix, before)

    def test_class_apply_exact(self):
        matrix = np.arange(1, 17, dtype=float).reshape(4, 4)
        np.testing.assert_array_equal(
            self.gov.R43UDiagonalGain.apply(matrix),
            self.src.inflate(matrix),
        )


if __name__ == "__main__":
    unittest.main()
