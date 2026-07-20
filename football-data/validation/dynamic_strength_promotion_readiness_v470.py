#!/usr/bin/env python3
"""Competition/season promotion-readiness gate for validated V4.7 dynamic strength.

This gate never promotes automatically. It converts completed current-season data
and the audited validation/runtime chain into a deterministic readiness state. The
formal engine still applies per-match venue sample gates after any future promotion.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from platform_core import ROOT, read_processed_matches, sha256_file

OUT = ROOT / "manifests" / "dynamic_strength_promotion_readiness_v470_status.json"
LIVE_READINESS = ROOT / "manifests" / "dynamic_strength_live_activation_readiness_v470_status.json"
PRE_OOF_SMOKE = ROOT / "manifests" / "dynamic_strength_pre_oof_runtime_v470_smoke.json"
RUNTIME = ROOT / "engine" / "dynamic_strength_pre_oof_runtime_v470.py"
RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
TARGETS = {
    "ESP_LaLiga": {"target_season": "2026/27", "mode": "full_dynamic_strength"},
    "NED_Eredivisie": {"target_season": "2026/27", "mode": "allocation_only_preserve_direct_total"},
}
MIN_COMPETITION_MATCHES = 30
MIN_RELEVANT_VENUE_MATCHES = 2


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    live = load(LIVE_READINESS)
    smoke = load(PRE_OOF_SMOKE)
    reports = {}
    for cid, spec in TARGETS.items():
        target = spec["target_season"]
        rows = [row for row in read_processed_matches(cid) if str(row.season) == target]
        home_counts = Counter(str(row.home_team) for row in rows)
        away_counts = Counter(str(row.away_team) for row in rows)
        teams = sorted(set(home_counts) | set(away_counts))
        team_samples = {
            team: {"home_matches": int(home_counts[team]), "away_matches": int(away_counts[team])}
            for team in teams
        }
        live_report = (live.get("reports") or {}).get(cid) or {}
        live_checks = live_report.get("checks") or {}
        chain_checks = {
            "historical_final_chain_passed": bool(live_checks.get("historical_final_chain_passed")),
            "next_season_candidate_frozen": bool(live_checks.get("next_season_candidate_frozen_from_completed_prior_season")),
            "parameter_rollforward_passed": bool(live_checks.get("next_season_parameter_rollforward_passed")),
            "parameter_runtime_smoke_passed": bool(live_checks.get("next_season_parameter_runtime_smoke_passed")),
            "oof_rollforward_passed": bool(live_checks.get("next_season_oof_rollforward_passed")),
            "oof_runtime_smoke_passed": bool(live_checks.get("next_season_oof_runtime_smoke_passed")),
            "live_input_contract_passed": bool(live_checks.get("live_input_contract_smoke_passed")) and bool(live_checks.get("live_input_contract_supports_competition")),
            "pre_oof_runtime_formula_parity_passed": smoke.get("status") == "PASS" and bool(live_checks.get("pre_oof_dynamic_strength_formula_parity_passed")),
            "pre_oof_runtime_dormant_guard_passed": bool(live_checks.get("pre_oof_dynamic_strength_dormant_without_receipt")),
            "pre_oof_runtime_wired": bool(live_checks.get("actionable_runtime_dynamic_strength_effect_wired")),
        }
        competition_sample_ready = len(rows) >= MIN_COMPETITION_MATCHES
        teams_with_home2 = sorted(team for team in teams if home_counts[team] >= MIN_RELEVANT_VENUE_MATCHES)
        teams_with_away2 = sorted(team for team in teams if away_counts[team] >= MIN_RELEVANT_VENUE_MATCHES)
        chain_ready = all(chain_checks.values())
        ready = chain_ready and competition_sample_ready
        reports[cid] = {
            "competition_id": cid,
            "target_season": target,
            "mode": spec["mode"],
            "status": "READY_FOR_COMPETITION_SEASON_PROMOTION_REVIEW" if ready else "PROMOTION_READINESS_BLOCKED",
            "automatic_promotion": False,
            "formal_weight": 0,
            "probability_change": False,
            "completed_current_season_matches": len(rows),
            "minimum_competition_history_matches": MIN_COMPETITION_MATCHES,
            "competition_sample_ready": competition_sample_ready,
            "minimum_relevant_venue_matches_per_match": MIN_RELEVANT_VENUE_MATCHES,
            "teams_with_at_least_2_home_matches": teams_with_home2,
            "teams_with_at_least_2_away_matches": teams_with_away2,
            "team_samples": team_samples,
            "chain_checks": chain_checks,
            "chain_ready": chain_ready,
            "blockers": [
                *(["current_season_competition_matches_below_30"] if not competition_sample_ready else []),
                *[key for key, value in chain_checks.items() if not value],
            ],
            "runtime_sha256": sha256_file(RUNTIME),
            "actionable_runner_sha256": sha256_file(RUNNER),
            "policy": (
                "Readiness does not create a promotion. A separate competition/season hash-bound promotion receipt "
                "must still be generated after review. Any individual match must also satisfy the formal engine's "
                "home-team home-sample and away-team away-sample gates."
            ),
        }
    out = {
        "schema_version": "V4.7.0-dynamic-strength-promotion-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "ready_for_review": [cid for cid, report in reports.items() if report["status"] == "READY_FOR_COMPETITION_SEASON_PROMOTION_REVIEW"],
        "blocked": [cid for cid, report in reports.items() if report["status"] == "PROMOTION_READINESS_BLOCKED"],
        "automatic_promotion": False,
        "formal_weight_change": False,
        "probability_change": False,
        "reports": reports,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
