#!/usr/bin/env python3
"""Acceptance validation for Live Prematch Runtime R1.

No target match result is read. The Community Shield case is a pure prematch
engineering pressure test over already-frozen strength-reference history.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from live_prematch_runtime_r1 import run_live_prematch  # noqa: E402
from platform_core import PlatformError  # noqa: E402

FIXTURE = ROOT / "research" / "live_prematch_runtime_r1_arsenal_mancity_20260816.json"
OUT = ROOT / "research" / "artifacts" / "live_prematch_runtime_r1" / "validation_status.json"
TOL = 2e-10


def _matrix_map(result):
    return {
        (int(c["home_goals"]), int(c["away_goals"])): float(c["probability"])
        for c in result["probabilities"]["score_matrix"]
    }


def _prob_sum(result):
    return sum(float(c["probability"]) for c in result["probabilities"]["score_matrix"])


def main() -> int:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    checks = {}
    diagnostics = {}

    community = run_live_prematch(base)
    checks["community_shield_operational_output"] = community.get("status") == "PASS"
    checks["community_shield_event_identity_preserved"] = (
        community["event_identity"]["event_competition_id"] == "ENG_CommunityShield"
        and community["event_identity"]["strength_reference_competition_id"] == "ENG_PremierLeague"
        and community["audit"]["event_domain_not_relabelled_as_strength_domain"] is True
    )
    checks["community_shield_uses_cold_start_bridge"] = (
        community["route"]["selected"] == "CROSS_SEASON_COLD_START_BRIDGE"
        and community["route"]["same_season_history_matches"] == 0
    )
    checks["community_probability_conservation"] = abs(_prob_sum(community) - 1.0) <= TOL
    checks["community_formal_weight_zero"] = community.get("formal_weight") == 0
    diagnostics["community_shield"] = {
        "route": community["route"],
        "one_x_two": community["probabilities"]["one_x_two"],
        "total_goals": community["probabilities"]["total_goals"],
        "top_scores": community["conclusions"]["top_scores"],
        "parameter_source": community["audit"]["parameter_source"],
    }

    swapped = copy.deepcopy(base)
    swapped["home_team"], swapped["away_team"] = base["away_team"], base["home_team"]
    swapped["strength_home_team"], swapped["strength_away_team"] = (
        base["strength_away_team"], base["strength_home_team"]
    )
    swapped_result = run_live_prematch(swapped)
    p = community["probabilities"]["one_x_two"]
    q = swapped_result["probabilities"]["one_x_two"]
    one_residual = max(abs(p["home"] - q["away"]), abs(p["draw"] - q["draw"]), abs(p["away"] - q["home"]))
    m1 = _matrix_map(community)
    m2 = _matrix_map(swapped_result)
    matrix_residual = max(abs(v - m2.get((a, h), 0.0)) for (h, a), v in m1.items())
    checks["neutral_1x2_swap_invariant"] = one_residual <= TOL
    checks["neutral_score_matrix_swap_invariant"] = matrix_residual <= TOL
    diagnostics["neutral_symmetry"] = {
        "one_x_two_max_residual": one_residual,
        "score_matrix_max_residual": matrix_residual,
    }

    pl_cold = copy.deepcopy(base)
    pl_cold.update({
        "event_competition_id": "ENG_PremierLeague",
        "home_team": "Arsenal",
        "away_team": "Manchester City",
        "strength_home_team": "Arsenal",
        "strength_away_team": "Man City",
        "neutral_venue": False,
        "venue": "Emirates Stadium",
        "evidence": [],
    })
    pl_cold_result = run_live_prematch(pl_cold)
    checks["premier_league_mw1_zero_history_bridge"] = (
        pl_cold_result["route"]["selected"] == "CROSS_SEASON_COLD_START_BRIDGE"
        and pl_cold_result["route"]["same_season_history_matches"] == 0
        and pl_cold_result["status"] == "PASS"
    )
    diagnostics["premier_league_cold_start"] = {
        "route": pl_cold_result["route"],
        "one_x_two": pl_cold_result["probabilities"]["one_x_two"],
    }

    in_season = {
        "event_competition_id": "ENG_PremierLeague",
        "strength_reference_competition_id": "ENG_PremierLeague",
        "season": "2025/26",
        "home_team": "Arsenal",
        "away_team": "Manchester City",
        "strength_home_team": "Arsenal",
        "strength_away_team": "Man City",
        "kickoff_utc": "2026-03-02T20:00:00Z",
        "freeze_time_utc": "2026-03-01T20:00:00Z",
        "neutral_venue": False,
        "venue": "Emirates Stadium",
        "evidence": []
    }
    in_season_result = run_live_prematch(in_season)
    checks["normal_inseason_routes_same_season"] = (
        in_season_result["route"]["selected"] == "SAME_SEASON_NORMAL_SHADOW"
        and in_season_result["route"]["same_season_history_matches"] >= 30
        and in_season_result["route"]["cold_start_bridge_used"] is False
    )
    diagnostics["in_season"] = {
        "route": in_season_result["route"],
        "one_x_two": in_season_result["probabilities"]["one_x_two"],
    }

    bad = copy.deepcopy(base)
    bad["evidence"] = list(base["evidence"]) + [{
        "kind": "forbidden_post_freeze",
        "source_name": "test",
        "source_url": "https://example.invalid/post-freeze",
        "observed_at_utc": "2026-08-16T13:55:01Z"
    }]
    rejected = False
    rejection_text = None
    try:
        run_live_prematch(bad)
    except PlatformError as exc:
        rejected = "post-freeze evidence rejected" in str(exc)
        rejection_text = str(exc)
    checks["post_freeze_evidence_hard_fails"] = rejected
    diagnostics["post_freeze_rejection"] = rejection_text

    checks["no_formal_mutation_claim"] = all(
        x.get("audit", {}).get("formal_current_mutated") is False and x.get("formal_weight") == 0
        for x in (community, swapped_result, pl_cold_result, in_season_result)
    )

    passed = all(checks.values())
    report = {
        "schema_version": "live-prematch-runtime-r1-validation",
        "status": "PASS" if passed else "FAIL",
        "classification": "ENGINEERING_ACCEPTANCE_ZERO_TARGET_LABEL",
        "checks": checks,
        "diagnostics": diagnostics,
        "target_match_result_read": False,
        "formal_current_changed": False,
        "formal_weight_changed": False,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
