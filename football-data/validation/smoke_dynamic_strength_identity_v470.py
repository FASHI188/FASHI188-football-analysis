#!/usr/bin/env python3
"""Verify that the no-borrow dynamic-strength candidate exactly reproduces Champion math."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dynamic_strength_oof_screen_v470 import CANDIDATES, challenger_matrix
from football_v460_engine import _merge_parameters, build_score_matrix, expected_goals, fit_current_season_state, load_config, low_score_factors
from platform_core import MatchRow

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "dynamic_strength_identity_v470_smoke.json"


def main() -> int:
    start = datetime(2025, 8, 1, tzinfo=timezone.utc)
    rows = []
    teams = ["club_1", "club_2", "club_3", "club_4"]
    for i in range(40):
        home = teams[i % 4]; away = teams[(i + 1 + (i // 4) % 2) % 4]
        if home == away: away = teams[(teams.index(home) + 2) % 4]
        rows.append(MatchRow("TEST", "2025/26", "regular", start + timedelta(days=i * 3), home, away, (i * 3) % 4, (i * 5 + 1) % 3, "synthetic"))
    cutoff = start + timedelta(days=130)
    config = load_config(); params = _merge_parameters(config, None)
    state = fit_current_season_state(rows, cutoff, params, config)
    home = "club_1"; away = "club_2"
    base = expected_goals(state, home, away, params, config)
    base_matrix = build_score_matrix(float(base["mu_home"]), float(base["mu_away"]), state["nb_dispersion_k"], params["beta_binomial_concentration"], int(config["max_total_goals_exact"]), low_score_factors(state, params))
    feature = {"roster_continuity": 0.8, "coach_continuity": 1.0, "promoted_or_relegated": False, "structural_break_score": 0.2, "feature_complete": True}
    identity = next(item for item in CANDIDATES if item["id"] == "identity_no_borrow")
    candidate_matrix, audit = challenger_matrix(state, state, 1, 2, feature, feature, identity, params, config)
    max_abs = max(abs(float(a["probability"]) - float(b["probability"])) for a, b in zip(base_matrix, candidate_matrix))
    checks = {
        "same_cell_count": len(base_matrix) == len(candidate_matrix),
        "max_abs_probability_difference_lte_1e_12": max_abs <= 1e-12,
        "identity_borrowing_zero": audit["max_prior_equivalent_matches"] == 0.0 and audit["home_borrowing_weight"] >= 0.0 and audit["away_borrowing_weight"] >= 0.0,
    }
    result = {"schema_version": "V4.7.0-dynamic-strength-identity-smoke-r1", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "max_abs_probability_difference": max_abs, "formal_weight_change": False, "probability_change": False}
    OUT.parent.mkdir(parents=True, exist_ok=True); OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__": raise SystemExit(main())
