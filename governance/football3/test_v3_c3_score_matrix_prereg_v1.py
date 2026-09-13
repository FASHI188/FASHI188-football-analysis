from __future__ import annotations

import copy
import json
import math
import unittest
from pathlib import Path

import validate_v3_c3_score_matrix_prereg_v1 as c3

HERE = Path(__file__).resolve().parent
CONTRACT = json.loads((HERE / "v3_c3_score_matrix_prereg_contract_v1.json").read_text(encoding="utf-8"))


def cells(values):
    return [{"home_goals": h, "away_goals": a, "probability": p} for h, a, p in values]


V1 = cells([
    (0,0,0.14),(1,0,0.18),(0,1,0.12),(1,1,0.15),(2,0,0.10),(0,2,0.08),
    (2,1,0.08),(1,2,0.06),(3,0,0.04),(0,3,0.02),(2,2,0.03)
])
XG = cells([
    (0,0,0.12),(1,0,0.14),(0,1,0.15),(1,1,0.14),(2,0,0.07),(0,2,0.11),
    (2,1,0.06),(1,2,0.09),(3,0,0.02),(0,3,0.06),(2,2,0.04)
])


class ContractTests(unittest.TestCase):
    def test_01_contract_valid(self):
        c3.validate_contract(CONTRACT)

    def test_02_schema_locked(self):
        self.assertEqual(CONTRACT["schema_version"], c3.SCHEMA)

    def test_03_exact_base_locked(self):
        self.assertEqual(CONTRACT["exact_base"], c3.EXPECTED_BASE)

    def test_04_zero_label_batch(self):
        g = CONTRACT["data_gate"]
        self.assertFalse(g["target_labels_read_in_this_batch"])
        self.assertFalse(g["training_in_this_batch"])
        self.assertFalse(g["tuning_in_this_batch"])

    def test_05_formal_surfaces_forbidden(self):
        self.assertTrue(all(CONTRACT["forbidden_changes"].values()))

    def test_06_inactive_is_zero_zero(self):
        self.assertEqual(CONTRACT["inactive_until_scientific_pass"], {"status":"NOT_AVAILABLE","weight":0,"matrix_delta":0,"data_ready":False})

    def test_07_single_parameter_beta_only(self):
        fam = CONTRACT["candidate_family"]
        self.assertEqual(fam["parameters"], ["beta"])
        self.assertEqual(fam["parameter_count"], 1)
        self.assertIsNone(fam["candidate_grid"])

    def test_08_no_draw_or_score_bonus(self):
        fam = CONTRACT["candidate_family"]
        self.assertFalse(fam["draw_bonus"])
        self.assertFalse(fam["score_bonus"])
        self.assertEqual(fam["thresholds"], [])

    def test_09_consumed_groups_not_fresh(self):
        d = CONTRACT["development_and_confirmation"]
        self.assertFalse(d["known_consumed_groups_may_be_fresh_confirmation"])
        self.assertTrue({"C072-I2","C072-K2","C077-B","STAGE6_1335"}.issubset(set(d["known_consumed_identity_groups"])))

    def test_10_confirmation_is_prospective(self):
        self.assertIn("prospective", CONTRACT["development_and_confirmation"]["fresh_confirmation"])


class MathematicalFeasibilityTests(unittest.TestCase):
    def test_11_formal_mix_support_equals_component_support(self):
        q = c3.formal_v2_mix(V1, XG)
        self.assertEqual(set(q), {(x["home_goals"],x["away_goals"]) for x in V1})

    def test_12_formal_mix_normalized(self):
        self.assertAlmostEqual(math.fsum(c3.formal_v2_mix(V1,XG).values()), 1.0, 14)

    def test_13_signal_is_component_only(self):
        s = c3.conditional_component_disagreement(V1,XG)
        self.assertEqual(set(s), {0,1,2,3,4})
        self.assertTrue(all(x is None or math.isfinite(x) for x in s.values()))

    def test_14_beta_zero_exact_mapping_identity(self):
        q = c3.formal_v2_mix(V1,XG)
        got = c3.conditional_tilt(V1,XG,0.0)
        self.assertEqual(got, q)

    def test_15_nonzero_beta_preserves_support(self):
        q = c3.formal_v2_mix(V1,XG)
        got = c3.conditional_tilt(V1,XG,0.7)
        self.assertEqual(set(got), set(q))

    def test_16_nonzero_beta_preserves_total_marginals(self):
        q = c3.formal_v2_mix(V1,XG)
        got = c3.conditional_tilt(V1,XG,0.7)
        a,b = c3.total_marginals(q), c3.total_marginals(got)
        self.assertEqual(set(a),set(b))
        self.assertLessEqual(max(abs(a[t]-b[t]) for t in a),5e-12)

    def test_17_candidate_stays_normalized(self):
        got = c3.conditional_tilt(V1,XG,1.25)
        self.assertAlmostEqual(math.fsum(got.values()),1.0,12)

    def test_18_inputs_not_mutated(self):
        a,b=copy.deepcopy(V1),copy.deepcopy(XG)
        c3.conditional_tilt(V1,XG,0.5)
        self.assertEqual(V1,a); self.assertEqual(XG,b)

    def test_19_mismatched_support_rejected(self):
        with self.assertRaises(c3.PreregError):
            c3.formal_v2_mix(V1,XG[:-1])

    def test_20_negative_probability_rejected(self):
        bad=copy.deepcopy(V1); bad[0]["probability"]=-0.1
        with self.assertRaises(c3.PreregError):
            c3.formal_v2_mix(bad,XG)

    def test_21_nonfinite_beta_rejected(self):
        with self.assertRaises(c3.PreregError):
            c3.conditional_tilt(V1,XG,float("nan"))

    def test_22_ineligible_single_cell_total_falls_back(self):
        v1=cells([(0,0,.4),(1,0,.3),(0,1,.3)])
        xg=cells([(0,0,.2),(1,0,.5),(0,1,.3)])
        q=c3.formal_v2_mix(v1,xg); got=c3.conditional_tilt(v1,xg,2.0)
        self.assertEqual(got[(0,0)],q[(0,0)])

    def test_23_positive_beta_moves_margin_with_signal(self):
        v1=cells([(2,0,.2),(1,1,.6),(0,2,.2)])
        xg=cells([(2,0,.6),(1,1,.2),(0,2,.2)])
        q=c3.formal_v2_mix(v1,xg); got=c3.conditional_tilt(v1,xg,1.0)
        self.assertGreater(got[(2,0)]/q[(2,0)], got[(0,2)]/q[(0,2)])

    def test_24_primary_metric_is_conditional_score_nll(self):
        self.assertIn("conditional exact-score", CONTRACT["metrics"]["primary"])

    def test_25_required_n_rule_is_mechanical_and_min_1000(self):
        rule=CONTRACT["development_and_confirmation"]["required_n_rule"]
        self.assertIn("max(1000",rule); self.assertIn("sigma_dev",rule); self.assertIn("0.003",rule)

    def test_26_promotion_requires_fresh_ci_below_zero(self):
        line=CONTRACT["promotion_line"]["scientific_primary"]
        self.assertIn("fresh confirmation cohort",line)
        self.assertIn("95%",line)
        self.assertIn("< 0",line)

    def test_27_promotion_not_automatic(self):
        p=CONTRACT["promotion_line"]
        self.assertFalse(p["promotion_is_automatic"])
        self.assertTrue(p["formal_activation_requires_new_authorization"])

    def test_28_no_tail_fabrication(self):
        gate=CONTRACT["data_gate"]
        self.assertTrue(gate["new_score_cells_forbidden"])
        self.assertTrue(gate["tail_extrapolation_forbidden"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
