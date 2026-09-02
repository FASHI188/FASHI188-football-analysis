from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("fusion_v2", HERE / "historical_xg_fusion_v2.py")
assert SPEC and SPEC.loader
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class FusionV2ContractTests(unittest.TestCase):
    def test_weight_grid_exact(self):
        self.assertEqual(M.WEIGHT_GRID, (0.25, 0.50, 0.75))
        self.assertEqual(M.canon_sha(list(M.WEIGHT_GRID)), "cebb08590ef3c9c3b467b9a4cb32734ea0e3964a3452d8ae9cd3ea258cafadd3")

    def test_top1_gate_not_relaxed(self):
        self.assertEqual(M.TOP1_FLOOR_DELTA, -0.0015)
        self.assertEqual(M.LL_GAIN_MIN, 0.001)

    def test_fusion_formula(self):
        v1 = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
        xg = {"p_home": 0.4, "p_draw": 0.35, "p_away": 0.25}
        q = M.normalize_mix(v1, xg, 0.5, False)
        self.assertAlmostEqual(q["p_home"], 0.45, places=15)
        self.assertAlmostEqual(q["p_draw"], 0.325, places=15)
        self.assertAlmostEqual(q["p_away"], 0.225, places=15)
        self.assertAlmostEqual(sum(q.values()), 1.0, places=15)

    def test_fallback_is_exact_v1(self):
        v1 = {"p_home": 0.5000000000000001, "p_draw": 0.3, "p_away": 0.1999999999999999}
        q = M.normalize_mix(v1, dict(v1), 0.75, True)
        self.assertEqual(q, v1)

    def test_fallback_mismatch_rejected(self):
        v1 = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
        xg = {"p_home": 0.5001, "p_draw": 0.2999, "p_away": 0.2}
        with self.assertRaises(M.FusionError):
            M.normalize_mix(v1, xg, 0.75, True)

    def test_invalid_weight_rejected(self):
        v1 = {"p_home": 0.5, "p_draw": 0.3, "p_away": 0.2}
        with self.assertRaises(M.FusionError):
            M.normalize_mix(v1, v1, 0.6, False)

    def test_metric_convention(self):
        p = {"p_home": 0.6, "p_draw": 0.25, "p_away": 0.15}
        c = M.one_match_contrib(p, 2, 0)
        self.assertGreater(c["logloss"], 0)
        self.assertEqual(c["correct"], 1.0)

    def test_data_identity_contract(self):
        d = json.loads((HERE / "data" / "XG_FUSION_V2_DATA_IDENTITY.json").read_text())
        self.assertEqual(d["base_parent"]["status"], "HISTORICAL_XG_CHALLENGER_REJECTED")
        self.assertEqual(d["base_parent"]["old_2023_confirmation_policy"], "POST_VIEW_DIAGNOSTIC_ONLY_NOT_FOR_WEIGHT_SELECTION_OR_PROMOTION")
        self.assertEqual(d["new_historical_confirmation"]["n"], 1752)
        self.assertEqual(d["new_historical_confirmation"]["old_fixture_overlap_n"], 0)
        self.assertFalse(d["new_historical_confirmation"]["prospective_queue"])
        self.assertEqual(d["fusion_contract"]["weight_grid"], [0.25, 0.5, 0.75])

    def test_frozen_xg_parameters(self):
        d = json.loads((HERE / "data" / "XG_FUSION_V2_DATA_IDENTITY.json").read_text())
        self.assertEqual(d["frozen_xg_parameters"], M.EXPECTED_XG_PARAMS)

    def test_terminal_pass_label(self):
        self.assertEqual(M.PASS_STATUS, "HISTORICAL_XG_FUSION_V2_CANDIDATE_PASSED_PENDING_CODEX_RECHECK")
        self.assertEqual(M.REJECT_STATUS, "HISTORICAL_XG_FUSION_V2_REJECTED")


if __name__ == "__main__":
    unittest.main()
