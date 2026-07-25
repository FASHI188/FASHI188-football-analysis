#!/usr/bin/env python3
"""V6.24.1 fixed-seed Random100 with baseline direct-total preservation.

This is a diagnostic successor to V6.24.0. It reuses the same fixed seed and
full strict-PIT chronology, but the regime layer may alter only the relevant
venue attack/defence composition. The direct total-goal sufficient statistic
and exposure stay exactly at the baseline for each team/venue.
"""
from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_team_regime_state_random100_v6240 as base  # noqa: E402
from football_v460_engine import (  # noqa: E402
    _merge_parameters,
    current_season_history,
    fit_current_season_state,
    load_config,
    low_score_factors,
)
from platform_core import PlatformError, load_json, read_processed_matches  # noqa: E402
from v624_regime_ledger import RegimeLedger  # noqa: E402
from v624_regime_state_adapter import (  # noqa: E402
    build_post_settlement_proposal,
    build_regime_snapshot,
    settle_regime_day,
)
from v6241_total_preserving_regime import (  # noqa: E402
    audit_total_preservation,
    mix_team_record_total_preserving,
)
from v6_team_regime_state_runner_v6240 import (  # noqa: E402
    EXPERT_HALF_LIVES,
    _build_regime_signals,
    _matrix_from_state,
    _state_with_team_records,
    _team_key,
)

OUT = ROOT / "manifests" / "v6_team_regime_state_random100_v6241_status.json"
SCHEMA = "V6.24.1-team-regime-state-total-preserving-random100-r1"


def _collect_competition(competition_id: str) -> tuple[list[dict[str, Any]], dict[str, int]]:
    config = load_config()
    season = base._requested_last_complete_season(competition_id)
    report = load_json(base.REPORT_ROOT / f"{competition_id}.json")
    fold = base._fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {competition_id} {season}")
    formal_params = _merge_parameters(config, selected)
    all_matches = sorted(read_processed_matches(competition_id), key=lambda m: (m.date, m.home_team, m.away_team))
    target = [m for m in all_matches if str(m.season) == season]
    if not target:
        raise PlatformError(f"no target-season matches for {competition_id} {season}")

    by_day: dict[Any, list[Any]] = defaultdict(list)
    for match in target:
        by_day[match.date.date()].append(match)

    ledger = RegimeLedger()
    rows: list[dict[str, Any]] = []
    skips = Counter()

    for day in sorted(by_day):
        day_matches = sorted(by_day[day], key=lambda m: (m.home_team, m.away_team))
        day_team_counts = Counter()
        for match in day_matches:
            day_team_counts[_team_key(match.home_team)] += 1
            day_team_counts[_team_key(match.away_team)] += 1
            try:
                history_season, history = current_season_history(all_matches, match.date, season)
                if history_season != season:
                    raise PlatformError("history season mismatch")
                baseline_state = fit_current_season_state(history, match.date, formal_params, config)
                baseline_factors = low_score_factors(baseline_state, formal_params)
                baseline_matrix = _matrix_from_state(
                    baseline_state, match.home_team, match.away_team, formal_params, config, baseline_factors
                )

                home_key = _team_key(match.home_team)
                away_key = _team_key(match.away_team)
                if home_key not in baseline_state["team"] or away_key not in baseline_state["team"]:
                    raise PlatformError("team missing from baseline state")

                home_records: list[dict[str, Any]] = []
                away_records: list[dict[str, Any]] = []
                for half_life in EXPERT_HALF_LIVES:
                    expert_params = dict(formal_params)
                    expert_params["half_life_days"] = half_life
                    expert_state = fit_current_season_state(history, match.date, expert_params, config)
                    home_records.append(expert_state["team"][home_key])
                    away_records.append(expert_state["team"][away_key])

                home_snapshot = build_regime_snapshot(home_key, ledger, _build_regime_signals(history, home_key))
                away_snapshot = build_regime_snapshot(away_key, ledger, _build_regime_signals(history, away_key))

                mixed_home = mix_team_record_total_preserving(
                    home_records,
                    [float(x) for x in home_snapshot["weight_vector"]],
                    baseline_state["team"][home_key],
                    float(home_snapshot["blend_strength"]),
                    role="home",
                )
                mixed_away = mix_team_record_total_preserving(
                    away_records,
                    [float(x) for x in away_snapshot["weight_vector"]],
                    baseline_state["team"][away_key],
                    float(away_snapshot["blend_strength"]),
                    role="away",
                )
                home_audit = audit_total_preservation(baseline_state["team"][home_key], mixed_home, role="home")
                away_audit = audit_total_preservation(baseline_state["team"][away_key], mixed_away, role="away")
                if not home_audit["passed"] or not away_audit["passed"]:
                    raise PlatformError("V6.24.1 direct-total preservation audit failed")

                challenger_state = _state_with_team_records(
                    baseline_state, home_key, mixed_home, away_key, mixed_away
                )
                challenger_matrix = _matrix_from_state(
                    challenger_state, match.home_team, match.away_team, formal_params, config, baseline_factors
                )

                rows.append({
                    "competition_id": competition_id,
                    "season": season,
                    "date": match.date.isoformat(),
                    "home_team": match.home_team,
                    "away_team": match.away_team,
                    "home_goals": int(match.home_goals),
                    "away_goals": int(match.away_goals),
                    "baseline_matrix": baseline_matrix,
                    "challenger_matrix": challenger_matrix,
                    "home_regime": home_snapshot["regime"],
                    "away_regime": away_snapshot["regime"],
                    "home_blend_strength": float(home_snapshot["blend_strength"]),
                    "away_blend_strength": float(away_snapshot["blend_strength"]),
                })
            except PlatformError as exc:
                skips[str(exc)] += 1

        post_history = [m for m in target if m.date.date() <= day]
        proposals: list[dict[str, Any]] = []
        for team_key, count in sorted(day_team_counts.items()):
            proposals.append(build_post_settlement_proposal(
                team_key,
                ledger,
                _build_regime_signals(post_history, team_key),
                day.isoformat(),
                settled_increment=int(count),
            ))
        settle_regime_day(ledger, proposals)

    return rows, dict(skips)


def main() -> int:
    # Reuse the audited V6.24.0 sampling/scoring harness, replacing only the
    # collection layer and output identity. This guarantees the same seed,
    # scoring rules and baseline/candidate match pairing.
    base._collect_competition = _collect_competition
    base.OUT = OUT
    base.SCHEMA = SCHEMA
    rc = base.main()

    # Enrich the receipt with the structural V6.24.1 contract.
    payload = load_json(OUT)
    payload["classification"] = "RESEARCH_CHALLENGER_V6241_TOTAL_PRESERVING_FIXED_SEED_RANDOM100_FORMAL_WEIGHT_0"
    payload["total_goal_contract"] = {
        "baseline_direct_total_track_preserved": True,
        "venue_total_sufficient_statistics_preserved": True,
        "venue_exposures_preserved": True,
        "regime_changes_conditional_allocation_only": True,
        "single_joint_score_matrix": True,
    }
    OUT.write_text(base.json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
