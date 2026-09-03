#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
MOD = HERE / "evidence_dominance.py"
spec = importlib.util.spec_from_file_location("evidence_dominance_test_target", str(MOD))
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
assert spec.loader is not None
spec.loader.exec_module(module)


class EvidenceDominanceTests(unittest.TestCase):
    def test_norm_odds_probability_mass(self):
        p = module.norm_odds([2.0, 4.0, 5.0])
        self.assertAlmostEqual(sum(p), 1.0, places=15)
        self.assertTrue(all(x > 0 for x in p))

    def test_top1_tie_break_is_deterministic(self):
        self.assertEqual(module.top1([0.4, 0.4, 0.2]), 0)
        self.assertEqual(module.top1([0.2, 0.4, 0.4]), 1)

    def test_same_top1_never_proposes(self):
        ok, rec = module.evidence_dominates([0.50, 0.30, 0.20], [0.45, 0.35, 0.20])
        self.assertFalse(ok)
        self.assertFalse(rec["proposal"])

    def test_exact_boundary_equality_is_dominant(self):
        # Opening incumbent H leads target A by 0.10; closing A leads H by exactly 0.10.
        ok, rec = module.evidence_dominates([0.45, 0.20, 0.35], [0.35, 0.20, 0.45])
        self.assertTrue(rec["proposal"])
        self.assertAlmostEqual(rec["opening_margin"], 0.10, places=15)
        self.assertAlmostEqual(rec["closing_reversal_margin"], 0.10, places=15)
        self.assertTrue(ok)

    def test_small_reversal_is_not_dominant(self):
        ok, rec = module.evidence_dominates([0.50, 0.28, 0.22], [0.34, 0.31, 0.35])
        self.assertTrue(rec["proposal"])
        self.assertGreater(rec["opening_margin"], rec["closing_reversal_margin"])
        self.assertFalse(ok)

    def test_strong_reversal_is_dominant(self):
        ok, rec = module.evidence_dominates([0.44, 0.31, 0.25], [0.28, 0.30, 0.42])
        self.assertTrue(rec["proposal"])
        self.assertLessEqual(rec["opening_margin"], rec["closing_reversal_margin"])
        self.assertTrue(ok)

    def test_rule_has_no_result_label_argument(self):
        # The gate consumes only opening and closing probabilities. The same call remains
        # identical regardless of any hypothetical outcome because no outcome is passed.
        a = module.evidence_dominates([0.46, 0.29, 0.25], [0.30, 0.28, 0.42])
        b = module.evidence_dominates([0.46, 0.29, 0.25], [0.30, 0.28, 0.42])
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
