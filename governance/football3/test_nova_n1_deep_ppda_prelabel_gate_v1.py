from __future__ import annotations

import json
import unittest
from pathlib import Path

import nova_n1_deep_ppda_prelabel_gate_v1 as m


class PrelabelGateTests(unittest.TestCase):
    def test_matrix_to_1x2_orientation(self) -> None:
        matrix=[[0.0 for _ in range(15)] for _ in range(15)]
        matrix[1][0]=0.5
        matrix[0][0]=0.3
        matrix[0][1]=0.2
        got, one=m.matrix_1x2(matrix,"x")
        self.assertEqual(one,[0.5,0.3,0.2])
        self.assertEqual(got,matrix)

    def test_bad_matrix_sum_fails_closed(self) -> None:
        matrix=[[0.0 for _ in range(15)] for _ in range(15)]
        matrix[0][0]=0.9
        with self.assertRaisesRegex(m.PrelabelGateError,"MATRIX_SUM"):
            m.matrix_1x2(matrix,"x")

    def test_probability_vector_fails_closed(self) -> None:
        with self.assertRaisesRegex(m.PrelabelGateError,"PROB_SUM"):
            m.prob_vector([0.4,0.4,0.4],"x")

    def test_league_aliases_are_exact_and_finite(self) -> None:
        self.assertEqual(m.normalize_league("La liga"),"La_liga")
        self.assertEqual(m.normalize_league("Ligue 1"),"Ligue_1")
        self.assertEqual(m.normalize_league("Serie A"),"Serie_A")
        with self.assertRaisesRegex(m.PrelabelGateError,"UNKNOWN_LEAGUE"):
            m.normalize_league("LaLiga fuzzy")

    def test_preregistration_forbids_old_candidate_and_activation(self) -> None:
        cfg=json.loads(Path("nova_n1_deep_ppda_preregistration_v1.json").read_text())
        self.assertEqual(cfg["status"],"DESIGN_LOCKED_PRELABEL")
        self.assertFalse(cfg["legacy_v3"]["candidate_code_parameters_weights_inherited"])
        self.assertFalse(cfg["legacy_v3"]["candidate_predictions_consumption_allowed"])
        self.assertTrue(cfg["formal_v2"]["sole_baseline"])
        self.assertEqual(cfg["model"]["candidate_weight"],0)
        self.assertEqual(cfg["model"]["matrix_delta"],0)
        self.assertEqual(cfg["experiment_budget"]["maximum_batches"],2)
        self.assertFalse(cfg["experiment_budget"]["test_second_chance_route_after_open"])
        self.assertFalse(cfg["completion"]["promotion_allowed"])


if __name__=="__main__":
    unittest.main()
