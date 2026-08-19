#!/usr/bin/env python3
from __future__ import annotations

import json
import math

import numpy as np

import evaluate_c077a_high_tail_shared_dgiven_t as c077a
import evaluate_c077b_independent_confirmation as c077b


def main() -> int:
    parser = c077b.score_parser_self_test()
    assert parser["status"] == "PASS"
    assert parser["synthetic_cases"] == 3

    p = c077a.binomial_vector(7, 0.5)
    assert p.shape == (8,)
    assert np.isfinite(p).all()
    assert np.all(p >= 0)
    assert abs(float(p.sum()) - 1.0) <= 1e-12
    mapping_failures, residual = c077a.structural_audit(7, p)
    assert mapping_failures == 0
    assert residual <= 1e-12

    s = c077a.score_row(3, p)
    assert all(math.isfinite(float(s[k])) for k in ["logloss", "brier", "rps", "top1"])
    assert s["logloss"] > 0
    assert s["brier"] >= 0
    assert s["rps"] >= 0
    assert s["top1"] in {0.0, 1.0}

    perfect = np.zeros(8, dtype=float)
    perfect[3] = 1.0
    ps = c077a.score_row(3, perfect)
    assert abs(ps["logloss"]) <= 1e-15
    assert abs(ps["brier"]) <= 1e-15
    assert abs(ps["rps"]) <= 1e-15
    assert ps["top1"] == 1.0

    base = c077a.baseline_vector(7, {})
    assert len(base) == 8
    assert np.allclose(base, np.full(8, 1 / 8))
    assert abs(float(base.sum()) - 1.0) <= 1e-12

    result = {
        "schema_version": "C077B_PREFLIGHT_UNIT_TESTS_V1",
        "status": "PASS",
        "score_parser": parser,
        "metric_tests": {
            "binomial_probability_conservation": "PASS",
            "structural_mapping": "PASS",
            "finite_proper_scores": "PASS",
            "perfect_prediction_zero_loss": "PASS",
            "unseen_T_uniform_baseline": "PASS"
        },
        "confirmation_numeric_labels_opened": False,
        "confirmation_total_goals_computed": False,
        "confirmation_tail_membership_computed": False,
        "confirmation_model_scored": False
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
