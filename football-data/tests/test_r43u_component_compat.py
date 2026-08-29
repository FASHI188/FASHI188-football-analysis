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

from components.r43u_fixed_diagonal import DIAGONAL_FACTOR, R43UFixedDiagonalInflationComponent


def load_pinned_original_inflate():
    source_path = os.environ.get("R43U_SOURCE_FILE")
    if not source_path:
        raise RuntimeError("R43U_SOURCE_FILE is required for exact compatibility test")
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "DIAGONAL_FACTOR" for t in node.targets):
            selected.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "inflate":
            selected.append(node)
    if len(selected) != 2:
        raise RuntimeError("could not extract exact R43U DIAGONAL_FACTOR/inflate from pinned source")
    scope = {"np": np}
    exec(compile(ast.Module(body=selected, type_ignores=[]), source_path, "exec"), scope)
    return scope["inflate"], float(scope["DIAGONAL_FACTOR"])


class R43UComponentCompatibilityTests(unittest.TestCase):
    def test_default_is_disabled(self):
        self.assertFalse(R43UFixedDiagonalInflationComponent().enabled)

    def test_factor_matches_pinned_source(self):
        _, original_factor = load_pinned_original_inflate()
        self.assertEqual(DIAGONAL_FACTOR, original_factor)
        self.assertEqual(DIAGONAL_FACTOR, 1.25)

    def test_migrated_operation_is_bitwise_equal_to_pinned_original(self):
        original_inflate, _ = load_pinned_original_inflate()
        raw = np.arange(1.0, 26.0, dtype=float).reshape(5, 5)
        matrix = raw / raw.sum()
        expected = original_inflate(matrix)
        cells = [
            {"home_goals": h, "away_goals": a, "probability": float(matrix[h, a])}
            for h in range(matrix.shape[0])
            for a in range(matrix.shape[1])
        ]
        actual_cells = R43UFixedDiagonalInflationComponent().apply(cells, None, {})
        actual = np.zeros_like(expected)
        for cell in actual_cells:
            actual[int(cell["home_goals"]), int(cell["away_goals"])] = float(cell["probability"])
        self.assertTrue(np.array_equal(actual, expected))


if __name__ == "__main__":
    unittest.main()
