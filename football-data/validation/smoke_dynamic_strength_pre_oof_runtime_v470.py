#!/usr/bin/env python3
"""Prove runtime formula parity with the validated ESP/NED research implementations."""
from __future__ import annotations

import json
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dynamic_strength_allocation_only_oof_v470 import allocation_only_matrix
from dynamic_strength_challenger_v470 import commensurability_score
from dynamic_strength_oof_screen_v470 import CANDIDATES, challenger_matrix
from dynamic_strength_pre_oof_runtime_v470 import (
    apply_promoted_dynamic_strength_pre_oof,
    build_dynamic_strength_matrix,
)
from football_v460_engine import _merge_parameters, expected_goals, fit_current_season_state, load_config
from platform_core import MatchRow, ROOT

OUT = ROOT / "manifests" / "dynamic_strength_pre_oof_runtime_v470_smoke.json"


def rows(season: str, start: datetime, count: int) -> list[MatchRow]:
    teams = ["club_1", "club_2", "club_3", "club_4"]
    output = []
    for i in range(count):
        home = teams[i % 4]
        away = teams[(i + 1 + (i // 4) % 2) % 4]
        if home == away:
            away = teams[(teams.index(home) + 2) % 4]
        output.append(MatchRow(
            "TEST", season, "regular", start + timedelta(days=i * 3), home, away,
            (i * 3 + (0 if season == "2025/26" else 1)) % 4,
            (i * 5 + 1) % 3,
            "synthetic",
        ))
    return output


def max_diff(a, b):
    amap = {(int(x["home_goals"]), int(x["away_goals"])): float(x["probability"]) for x in a}
    bmap = {(int(x["home_goals"]), int(x["away_goals"])): float(x["probability"]) for x in b}
    if set(amap) != set(bmap):
        raise RuntimeError("matrix cell sets differ")
    return max(abs(amap[key] - bmap[key]) for key in amap)


def run_smoke() -> dict:
    cfg = load_config()
    params = _merge_parameters(cfg, {
        "half_life_days": 180.0,
        "team_prior_matches": 8.0,
        "nb_default_k": 16.0,
        "beta_binomial_concentration": 24.0,
        "low_score_shrinkage": 0.15,
        "direct_total_signal_weight": 1.0,
    })
    prior_rows = rows("2025/26", datetime(2025, 8, 1, tzinfo=timezone.utc), 80)
    current_rows = rows("2026/27", datetime(2026, 8, 1, tzinfo=timezone.utc), 48)
    prior_cutoff = max(x.date for x in prior_rows) + timedelta(days=1)
    current_cutoff = max(x.date for x in current_rows) + timedelta(days=1)
    prior_state = fit_current_season_state(prior_rows, prior_cutoff, params, cfg)
    current_state = fit_current_season_state(current_rows, current_cutoff, params, cfg)
    candidate = next(x for x in CANDIDATES if x["id"] == "adaptive_6")
    home_feat = {
        "roster_continuity": 0.82,
        "coach_continuity": 1.0,
        "promoted_or_relegated": False,
        "structural_break_score": 0.10,
        "feature_complete": True,
    }
    away_feat = {
        "roster_continuity": 0.71,
        "coach_continuity": 0.0,
        "promoted_or_relegated": False,
        "structural_break_score": 0.18,
        "feature_complete": True,
    }
    keys = ("roster_continuity", "coach_continuity", "promoted_or_relegated", "structural_break_score")
    hw = commensurability_score(**{k: home_feat[k] for k in keys}, coefficients=candidate["coefficients"])
    aw = commensurability_score(**{k: away_feat[k] for k in keys}, coefficients=candidate["coefficients"])

    research_full, _ = challenger_matrix(current_state, prior_state, 1, 2, home_feat, away_feat, candidate, params, cfg)
    runtime_full, runtime_full_audit = build_dynamic_strength_matrix(
        mode="full_dynamic_strength",
        current_state=current_state,
        prior_state=prior_state,
        home_team="club_1",
        away_team="club_2",
        home_borrowing_weight=hw,
        away_borrowing_weight=aw,
        max_prior_equivalent_matches=candidate["max_prior_equivalent_matches"],
        params=params,
        config=cfg,
    )
    means = expected_goals(current_state, "club_1", "club_2", params, cfg)
    research_alloc, _ = allocation_only_matrix(current_state, prior_state, 1, 2, home_feat, away_feat, candidate, params, cfg, float(means["mu_total"]))
    runtime_alloc, runtime_alloc_audit = build_dynamic_strength_matrix(
        mode="allocation_only_preserve_direct_total",
        current_state=current_state,
        prior_state=prior_state,
        home_team="club_1",
        away_team="club_2",
        home_borrowing_weight=hw,
        away_borrowing_weight=aw,
        max_prior_equivalent_matches=candidate["max_prior_equivalent_matches"],
        params=params,
        config=cfg,
        champion_mu_total=float(means["mu_total"]),
    )

    full_diff = max_diff(research_full, runtime_full)
    alloc_diff = max_diff(research_alloc, runtime_alloc)
    original_calc = {
        "probabilities": {"score_matrix": runtime_full},
        "model_audit": {"parameters": params, "team_sample": {"mu_total": means["mu_total"]}},
        "derived_markets": {},
    }
    dormant = apply_promoted_dynamic_strength_pre_oof(
        {
            "match_identity": {"competition_id": "ESP_LaLiga", "season": "2026/27", "home_team": "club_1", "away_team": "club_2", "freeze_time_utc": "2026-09-01T12:00:00Z"},
            "dynamic_strength_live_input_audit": {"status": "通过", "candidate_id": "adaptive_6", "candidate_mode": "full_dynamic_strength"},
        },
        original_calc,
    )
    dormant_diff = max_diff(original_calc["probabilities"]["score_matrix"], dormant["probabilities"]["score_matrix"])
    checks = {
        "full_dynamic_formula_matches_research_lte_1e_12": full_diff <= 1e-12,
        "allocation_only_formula_matches_research_lte_1e_12": alloc_diff <= 1e-12,
        "allocation_only_preserves_champion_mu_total": abs(runtime_alloc_audit["mu_total"] - float(means["mu_total"])) <= 1e-12,
        "no_promotion_receipt_keeps_matrix_unchanged": dormant_diff <= 1e-12,
        "no_promotion_receipt_status_not_enabled": dormant.get("dynamic_strength_pre_oof_audit", {}).get("status") == "未启用",
        "full_probability_conserved": abs(sum(float(x["probability"]) for x in runtime_full) - 1.0) <= 1e-12,
        "allocation_probability_conserved": abs(sum(float(x["probability"]) for x in runtime_alloc) - 1.0) <= 1e-12,
    }
    return {
        "schema_version": "V4.7.0-dynamic-strength-pre-oof-runtime-smoke-r2",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "full_dynamic_max_abs_probability_difference": full_diff,
        "allocation_only_max_abs_probability_difference": alloc_diff,
        "dormant_no_receipt_max_abs_probability_difference": dormant_diff,
        "full_runtime_audit": runtime_full_audit,
        "allocation_runtime_audit": runtime_alloc_audit,
        "formal_weight_change": False,
        "probability_change": False,
    }


def main() -> int:
    try:
        result = run_smoke()
    except Exception as exc:
        result = {
            "schema_version": "V4.7.0-dynamic-strength-pre-oof-runtime-smoke-r2",
            "status": "FAIL",
            "failure_class": "ENGINEERING_OR_TEST_EXCEPTION",
            "reason": str(exc),
            "traceback_tail": traceback.format_exc().splitlines()[-16:],
            "formal_weight_change": False,
            "probability_change": False,
        }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
