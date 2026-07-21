#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import bayesian_dynamic_state_oof_v500 as base
from bayesian_dynamic_state_oof_v501_same_day_safe import simulate_season_same_day_safe
from platform_core import atomic_write_json

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "manifests" / "bayesian_dynamic_state_same_day_safe_v501_smoke.json"


@dataclass
class Match:
    season: str
    date: datetime
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int


def main() -> int:
    same_day = datetime(2025, 8, 1, 12, 0, tzinfo=timezone.utc)
    matches = [
        Match("2025/26", same_day, "Alpha", "Beta", 5, 0),
        Match("2025/26", same_day.replace(hour=18), "Gamma", "Delta", 0, 5),
    ]
    seen_update_counts: list[int] = []
    profiles = [{
        "id": "smoke",
        "process_noise_per_day": 0.0,
        "half_life_days": 365.0,
        "prior_variance": 0.1,
        "total_weight": 0.0,
        "share_weight": 0.0,
        "league_prior_matches": 10.0,
    }]

    originals = {
        "PROFILES": base.PROFILES,
        "_fold_for_season": base._fold_for_season,
        "_prior_league_rates": base._prior_league_rates,
        "_target_season_temperature": base._target_season_temperature,
        "_predict_from_loaded_matches": base._predict_from_loaded_matches,
        "_metric_row": base._metric_row,
        "_dynamic_rates": base._dynamic_rates,
        "_candidate_from_baseline": base._candidate_from_baseline,
        "_update_states": base._update_states,
    }

    try:
        base.PROFILES = profiles
        base._fold_for_season = lambda report, season: {"selected_parameters": {"smoke": True}}
        base._prior_league_rates = lambda all_matches, season: (1.5, 1.2, 100)
        base._target_season_temperature = lambda cid, season: (1.0, "smoke")
        base._predict_from_loaded_matches = lambda *args, **kwargs: [
            {"home_goals": 0, "away_goals": 0, "probability": 1.0}
        ]
        base._metric_row = lambda matrix, match: {
            "one_x_two_accuracy": 0.0,
            "one_x_two_brier": 0.0,
            "one_x_two_rps": 0.0,
            "joint_log": 0.0,
            "score_top1": 0.0,
            "score_top3": 0.0,
            "total_top1": 0.0,
            "total_top2": 0.0,
            "total_rps": 0.0,
            "probability_sum_residual": 0.0,
        }

        def fake_dynamic(states, home, away, date, league_home, league_away, profile):
            seen_update_counts.append(int(states.get("_updates", 0)))
            return 1.5, 1.2, {"updates_visible_at_prediction": int(states.get("_updates", 0))}

        def fake_candidate(baseline, dynamic_home, dynamic_away, profile):
            return baseline, {
                "probability_sum_residual": 0.0,
                "max_total_marginal_residual": 0.0,
            }

        def fake_update(states, *args, **kwargs):
            states["_updates"] = int(states.get("_updates", 0)) + 1
            return {}

        base._dynamic_rates = fake_dynamic
        base._candidate_from_baseline = fake_candidate
        base._update_states = fake_update

        result = simulate_season_same_day_safe("SMOKE", "2025/26", matches, {})
        final_updates = result["profiles"]["smoke"]
        checks = {
            "two_predictions_created": len(final_updates) == 2,
            "no_same_day_update_visible_to_first_prediction": seen_update_counts[0] == 0,
            "no_same_day_update_visible_to_second_prediction": seen_update_counts[1] == 0,
            "same_day_withheld_flag_true": result.get("same_day_outcomes_withheld") is True,
        }
        payload = {
            "schema_version": "V5.0.1-bayesian-dynamic-state-same-day-safe-smoke-r1",
            "status": "PASS" if all(checks.values()) else "FAIL",
            "seen_update_counts_at_prediction": seen_update_counts,
            "checks": checks,
            "formal_weight_change": False,
            "probability_change": False,
        }
        atomic_write_json(OUT, payload)
        print(payload)
        return 0 if payload["status"] == "PASS" else 1
    finally:
        for name, value in originals.items():
            setattr(base, name, value)


if __name__ == "__main__":
    raise SystemExit(main())
