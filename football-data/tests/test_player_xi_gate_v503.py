from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_gate():
    path = ROOT / "validation" / "player_xi_gate_v503.py"
    spec = importlib.util.spec_from_file_location("player_xi_gate_v503", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load player-XI gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlayerXIGateTests(unittest.TestCase):
    def test_safe_false_prohibited_condition_passes(self) -> None:
        gate = load_gate()
        passed, detail = gate.adjudicate_checks({
            "two_outer_seasons": True,
            "margin_mse_ci_improves": True,
            "target_actual_xi_used_as_input": False,
        })
        self.assertTrue(passed)
        self.assertEqual(detail["status"], "PASS")

    def test_target_actual_xi_true_fails(self) -> None:
        gate = load_gate()
        passed, detail = gate.adjudicate_checks({
            "two_outer_seasons": True,
            "target_actual_xi_used_as_input": True,
        })
        self.assertFalse(passed)
        self.assertEqual(
            detail["prohibited_condition_failures"],
            ["target_actual_xi_used_as_input"],
        )

    def test_positive_false_fails(self) -> None:
        gate = load_gate()
        passed, detail = gate.adjudicate_checks({
            "two_outer_seasons": False,
            "target_actual_xi_used_as_input": False,
        })
        self.assertFalse(passed)
        self.assertEqual(detail["positive_failures"], ["two_outer_seasons"])


if __name__ == "__main__":
    unittest.main()
