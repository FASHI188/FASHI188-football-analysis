#!/usr/bin/env python3
"""V5.0.2 player-specific lineup residual-signal rolling OOF diagnostic.

Unlike the rejected lineup-continuity diagnostic, this challenger learns
player-specific residual values from prior matches after removing the frozen
formal matrix expectation. It remains deliberately simple and auditable:

- player margin targets are +/- half of the match goal-margin residual;
- player total targets are half of the match total-goals residual;
- ratings are competition/season-local and shrink toward zero;
- the probable XI at each target freeze is inferred only from prior observed
  same-season lineups;
- a prior match updates lineup history and player ratings only after its
  conservative source_observed_at timestamp becomes available;
- the target match actual XI is never used as an input.

Profiles are selected only from earlier completed seasons. The last two seasons
are untouched outer folds. A zero-effect profile is always included. This stage
only tests expected-margin/expected-total residual signal; it never mutates a
score matrix or formal probability and cannot promote without later four-target
single-matrix OOF including handicap evidence.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    REPORT_ROOT,
    _fold_for_season,
    _predict_from_loaded_matches,
    _target_season_temperature,
)
from oof_matrix_calibration import temperature_scale_matrix  # noqa: E402
from platform_core import (  # noqa: E402
    PlatformError,
    atomic_write_json,
    load_json,
    read_processed_matches,
    score_matrix_rows,
)

LINK_ROOT = ROOT / "player_xi_links"
IDENTITY_STATUS = ROOT / "manifests" / "lineup_match_identity_v502_status.json"
OUT = ROOT / "manifests" / "player_xi_residual_signal_oof_v502_status.json"
REPORT_DIR = ROOT / "manifests" / "player_xi_residual_signal_oof_v502"

PILOT_DOMAINS = ("ESP_LaLiga", "GER_Bundesliga")
LOOKBACK = 8
DECAY = 0.78
MIN_TEAM_HISTORY = 3
MARGIN_CLIP = 0.75
TOTAL_CLIP = 0.75
BOOTSTRAP_DRAWS = 1200
BLOCK_SIZE = 20
SEED = 5023026
TOTAL_MSE_NONINFERIORITY = 0.02

PROFILES = [
    {
        "id": "baseline_zero",
        "prior_strength": 1.0,
        "margin_scale": 0.0,
        "total_scale": 0.0,
    },
    {
        "id": "player_margin_p5_s050",
        "prior_strength": 5.0,
        "margin_scale": 0.50,
        "total_scale": 0.0,
    },
    {
        "id": "player_margin_p10_s100",
        "prior_strength": 10.0,
        "margin_scale": 1.00,
        "total_scale": 0.0,
    },
    {
        "id": "player_two_axis_p5_s050",
        "prior_strength": 5.0,
        "margin_scale": 0.50,
        "total_scale": 0.50,
    },
    {
        "id": "player_two_axis_p10_s100",
        "prior_strength": 10.0,
        "margin_scale": 1.00,
        "total_scale": 1.00,
    },
    {
        "id": "player_two_axis_p20_s100",
        "prior_strength": 20.0,
        "margin_scale": 1.00,
        "total_scale": 1.00,
    },
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def season_year(season: str) -> int:
    token = str(season).strip()
    if len(token) < 4 or not token[:4].isdigit():
        raise PlatformError(f"cannot parse season: {season!r}")
    return int(token[:4])


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise PlatformError(f"missing JSONL: {path}")
    rows = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise PlatformError(f"invalid JSONL {path}:{line_number}: {exc}") from exc
    return rows


def matrix_means(matrix: list[dict[str, Any]]) -> tuple[float, float, float]:
    home = 0.0
    away = 0.0
    for h, a, probability in score_matrix_rows(matrix):
        home += h * probability
        away += a * probability
    return home, away, home + away


def link_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row["season"]),
        str(row["date"]),
        str(row["home_team"]),
        str(row["away_team"]),
    )


def load_link_map(competition_id: str) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows = read_jsonl(LINK_ROOT / competition_id / "fixture_lineup_links.jsonl")
    mapping: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = link_key(row)
        if key in mapping:
            raise PlatformError(f"duplicate identity link: {key}")
        mapping[key] = row
    return mapping


def probable_probabilities(history: list[tuple[str, ...]]) -> dict[str, float] | None:
    history = history[-LOOKBACK:]
    if len(history) < MIN_TEAM_HISTORY:
        return None
    raw: dict[str, float] = defaultdict(float)
    total_weight = 0.0
    for age, starters in enumerate(reversed(history)):
        weight = DECAY ** age
        total_weight += weight
        for player in starters:
            raw[player] += weight
    probabilities = {
        player: value / max(total_weight, 1e-12)
        for player, value in raw.items()
    }
    if len(probabilities) < 11:
        return None
    return probabilities


def expected_player_rating(
    probabilities: dict[str, float],
    state_sum: dict[str, float],
    state_count: dict[str, int],
    prior_strength: float,
) -> float:
    ranking = sorted(probabilities, key=lambda player: (-probabilities[player], player))[:18]
    numerator = 0.0
    denominator = 0.0
    for player in ranking:
        probability = float(probabilities[player])
        rating = float(state_sum.get(player, 0.0)) / (
            float(prior_strength) + float(state_count.get(player, 0))
        )
        numerator += probability * rating
        denominator += probability
    return numerator / max(denominator, 1e-12)


def base_records(competition_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    identity = load_json(IDENTITY_STATUS)
    if competition_id not in identity.get("passed_domains", []):
        raise PlatformError(f"identity bridge not passed: {competition_id}")
    links = load_link_map(competition_id)
    report = load_json(REPORT_ROOT / f"{competition_id}.json")
    all_matches = read_processed_matches(competition_id)
    seasons_with_folds = {
        str(fold.get("outer_season"))
        for fold in report.get("folds", [])
        if fold.get("outer_season")
    }
    records = []
    skip_reasons: dict[str, int] = defaultdict(int)
    by_season: dict[str, int] = defaultdict(int)
    linked_by_season: dict[str, int] = defaultdict(int)

    for match in sorted(all_matches, key=lambda item: (item.date, item.home_team, item.away_team)):
        season = str(match.season)
        if season not in seasons_with_folds:
            continue
        fold = _fold_for_season(report, season)
        parameters = fold.get("selected_parameters")
        if not isinstance(parameters, dict):
            skip_reasons["missing_frozen_parameters"] += 1
            continue
        try:
            matrix = _predict_from_loaded_matches(
                all_matches,
                match.home_team,
                match.away_team,
                match.date,
                season,
                parameters,
            )
        except PlatformError:
            skip_reasons["formal_sample_gate"] += 1
            continue
        temperature, calibration_mode = _target_season_temperature(competition_id, season)
        if abs(temperature - 1.0) > 1e-15:
            matrix = temperature_scale_matrix(matrix, temperature)
        base_home, base_away, base_total = matrix_means(matrix)
        key = (
            season,
            match.date.date().isoformat(),
            match.home_team,
            match.away_team,
        )
        link = links.get(key)
        if link is not None:
            linked_by_season[season] += 1
        records.append({
            "competition_id": competition_id,
            "season": season,
            "date": match.date,
            "date_text": match.date.date().isoformat(),
            "match_key": f"{competition_id}:{season}:{match.date.date().isoformat()}:{match.home_team}:{match.away_team}",
            "home_team": match.home_team,
            "away_team": match.away_team,
            "actual_home": int(match.home_goals),
            "actual_away": int(match.away_goals),
            "base_margin": base_home - base_away,
            "base_total": base_total,
            "margin_residual": float(match.home_goals - match.away_goals) - (base_home - base_away),
            "total_residual": float(match.home_goals + match.away_goals) - base_total,
            "link": link,
            "oof_temperature": temperature,
            "oof_calibration_mode": calibration_mode,
        })
        by_season[season] += 1
    return records, {
        "base_record_count": len(records),
        "base_records_by_season": dict(sorted(by_season.items(), key=lambda item: season_year(item[0]))),
        "linked_base_records_by_season": dict(sorted(linked_by_season.items(), key=lambda item: season_year(item[0]))),
        "skip_reasons": dict(skip_reasons),
    }


def simulate_profile(
    season_records: list[dict[str, Any]],
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    lineup_history: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    margin_sum: dict[str, float] = defaultdict(float)
    margin_count: dict[str, int] = defaultdict(int)
    total_sum: dict[str, float] = defaultdict(float)
    total_count: dict[str, int] = defaultdict(int)
    pending: list[tuple[datetime, int, dict[str, Any]]] = []
    sequence = 0
    scored = []

    def apply_event(event: dict[str, Any]) -> None:
        home_starters = tuple(str(item) for item in event["home_starters"])
        away_starters = tuple(str(item) for item in event["away_starters"])
        lineup_history[event["home_team"]].append(home_starters)
        lineup_history[event["away_team"]].append(away_starters)
        margin_target = float(event["margin_residual"]) / 2.0
        total_target = float(event["total_residual"]) / 2.0
        for player in home_starters:
            margin_sum[player] += margin_target
            margin_count[player] += 1
            total_sum[player] += total_target
            total_count[player] += 1
        for player in away_starters:
            margin_sum[player] -= margin_target
            margin_count[player] += 1
            total_sum[player] += total_target
            total_count[player] += 1

    for record in sorted(season_records, key=lambda item: (item["date"], item["home_team"], item["away_team"])):
        cutoff = record["date"]
        while pending and pending[0][0] < cutoff:
            _, _, event = heapq.heappop(pending)
            apply_event(event)

        home_probabilities = probable_probabilities(lineup_history[record["home_team"]])
        away_probabilities = probable_probabilities(lineup_history[record["away_team"]])
        if home_probabilities is not None and away_probabilities is not None:
            prior_strength = float(profile["prior_strength"])
            home_margin_rating = expected_player_rating(
                home_probabilities, margin_sum, margin_count, prior_strength
            )
            away_margin_rating = expected_player_rating(
                away_probabilities, margin_sum, margin_count, prior_strength
            )
            home_total_rating = expected_player_rating(
                home_probabilities, total_sum, total_count, prior_strength
            )
            away_total_rating = expected_player_rating(
                away_probabilities, total_sum, total_count, prior_strength
            )
            margin_signal = home_margin_rating - away_margin_rating
            total_signal = home_total_rating + away_total_rating
            margin_adjustment = max(
                -MARGIN_CLIP,
                min(MARGIN_CLIP, float(profile["margin_scale"]) * margin_signal),
            )
            total_adjustment = max(
                -TOTAL_CLIP,
                min(TOTAL_CLIP, float(profile["total_scale"]) * total_signal),
            )
            margin_error = float(record["margin_residual"])
            total_error = float(record["total_residual"])
            scored.append({
                "match_key": record["match_key"],
                "season": record["season"],
                "date": record["date_text"],
                "profile_id": profile["id"],
                "home_margin_rating": home_margin_rating,
                "away_margin_rating": away_margin_rating,
                "home_total_rating": home_total_rating,
                "away_total_rating": away_total_rating,
                "margin_signal": margin_signal,
                "total_signal": total_signal,
                "margin_adjustment": margin_adjustment,
                "total_adjustment": total_adjustment,
                "baseline_margin_squared_error": margin_error ** 2,
                "candidate_margin_squared_error": (margin_error - margin_adjustment) ** 2,
                "baseline_margin_absolute_error": abs(margin_error),
                "candidate_margin_absolute_error": abs(margin_error - margin_adjustment),
                "baseline_total_squared_error": total_error ** 2,
                "candidate_total_squared_error": (total_error - total_adjustment) ** 2,
                "baseline_total_absolute_error": abs(total_error),
                "candidate_total_absolute_error": abs(total_error - total_adjustment),
            })

        link = record.get("link")
        if link is not None:
            available_at = max(
                parse_time(link["home_source_observed_at_utc"]),
                parse_time(link["away_source_observed_at_utc"]),
            )
            sequence += 1
            heapq.heappush(pending, (
                available_at,
                sequence,
                {
                    "home_team": record["home_team"],
                    "away_team": record["away_team"],
                    "home_starters": link["home_starters"],
                    "away_starters": link["away_starters"],
                    "margin_residual": record["margin_residual"],
                    "total_residual": record["total_residual"],
                },
            ))
    return scored


def metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise PlatformError("cannot summarize empty rows")
    keys = (
        "baseline_margin_squared_error",
        "candidate_margin_squared_error",
        "baseline_margin_absolute_error",
        "candidate_margin_absolute_error",
        "baseline_total_squared_error",
        "candidate_total_squared_error",
        "baseline_total_absolute_error",
        "candidate_total_absolute_error",
    )
    out = {key: mean(float(row[key]) for row in rows) for key in keys}
    out["margin_mse_difference"] = out["candidate_margin_squared_error"] - out["baseline_margin_squared_error"]
    out["margin_mae_difference"] = out["candidate_margin_absolute_error"] - out["baseline_margin_absolute_error"]
    out["total_mse_difference"] = out["candidate_total_squared_error"] - out["baseline_total_squared_error"]
    out["total_mae_difference"] = out["candidate_total_absolute_error"] - out["baseline_total_absolute_error"]
    out["row_count"] = len(rows)
    return out


def selection_objective(rows: list[dict[str, Any]]) -> tuple[float, float, float, bool]:
    summary = metric_summary(rows)
    margin_ratio = summary["candidate_margin_squared_error"] / max(summary["baseline_margin_squared_error"], 1e-12)
    total_ratio = summary["candidate_total_squared_error"] / max(summary["baseline_total_squared_error"], 1e-12)
    eligible = margin_ratio <= 1.01 and total_ratio <= 1.01
    return margin_ratio + 0.5 * total_ratio, margin_ratio, total_ratio, eligible


def blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (season_year(row["season"]), row["date"], row["match_key"]))
    return [ordered[index:index + BLOCK_SIZE] for index in range(0, len(ordered), BLOCK_SIZE)]


def bootstrap_difference(
    rows: list[dict[str, Any]],
    candidate_key: str,
    baseline_key: str,
    seed: int,
) -> dict[str, Any]:
    grouped = blocks(rows)
    point = mean(float(row[candidate_key]) - float(row[baseline_key]) for row in rows)
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_DRAWS):
        sampled = []
        for _ in range(len(grouped)):
            sampled.extend(rng.choice(grouped))
        samples.append(mean(float(row[candidate_key]) - float(row[baseline_key]) for row in sampled))
    samples.sort()
    return {
        "mean_difference": point,
        "ci95_lower": samples[int(0.025 * (len(samples) - 1))],
        "ci95_upper": samples[int(0.975 * (len(samples) - 1))],
        "blocks": len(grouped),
        "draws": BOOTSTRAP_DRAWS,
    }


def validate_domain(competition_id: str) -> dict[str, Any]:
    records, data_audit = base_records(competition_id)
    records_by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        records_by_season[record["season"]].append(record)
    seasons = sorted(records_by_season, key=season_year)
    if len(seasons) < 4:
        raise PlatformError(f"need at least four seasons, got {seasons}")
    simulations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for season in seasons:
        simulations[season] = {
            profile["id"]: simulate_profile(records_by_season[season], profile)
            for profile in PROFILES
        }

    outer_targets = seasons[-2:]
    folds = []
    outer_rows: list[dict[str, Any]] = []
    selected_profiles = []
    for target in outer_targets:
        target_index = seasons.index(target)
        prior_seasons = seasons[:target_index]
        ledger = []
        for profile in PROFILES:
            rows = [
                row
                for season in prior_seasons
                for row in simulations[season][profile["id"]]
            ]
            if not rows:
                continue
            objective, margin_ratio, total_ratio, eligible = selection_objective(rows)
            ledger.append({
                "profile_id": profile["id"],
                "selection_rows": len(rows),
                "objective": objective,
                "margin_mse_ratio": margin_ratio,
                "total_mse_ratio": total_ratio,
                "eligible": eligible,
            })
        eligible = [item for item in ledger if item["eligible"]]
        if not eligible:
            raise PlatformError(f"no eligible profile for {competition_id} {target}")
        eligible.sort(key=lambda item: (item["objective"], item["profile_id"]))
        selected = eligible[0]
        selected_profiles.append(selected["profile_id"])
        target_rows = simulations[target][selected["profile_id"]]
        outer_rows.extend(target_rows)
        folds.append({
            "target_season": target,
            "prior_seasons": prior_seasons,
            "selected_profile": selected["profile_id"],
            "selection": selected,
            "selection_ledger": ledger,
            "outer_rows": len(target_rows),
            "metrics": metric_summary(target_rows),
        })

    pooled = metric_summary(outer_rows)
    margin_bootstrap = bootstrap_difference(
        outer_rows,
        "candidate_margin_squared_error",
        "baseline_margin_squared_error",
        SEED + sum(ord(ch) for ch in competition_id),
    )
    total_bootstrap = bootstrap_difference(
        outer_rows,
        "candidate_total_squared_error",
        "baseline_total_squared_error",
        SEED + 1000 + sum(ord(ch) for ch in competition_id),
    )
    checks = {
        "two_outer_seasons": len(folds) == 2,
        "minimum_outer_rows_500": len(outer_rows) >= 500,
        "nonbaseline_selected_both_outer_folds": all(
            profile != "baseline_zero" for profile in selected_profiles
        ),
        "margin_mse_ci_improves": margin_bootstrap["ci95_upper"] < 0.0,
        "total_mse_ci_noninferior": total_bootstrap["ci95_upper"] <= TOTAL_MSE_NONINFERIORITY,
        "margin_mse_nonworse_each_outer_season": all(
            fold["metrics"]["margin_mse_difference"] <= 0.0 for fold in folds
        ),
        "total_mse_nonworse_each_outer_season": all(
            fold["metrics"]["total_mse_difference"] <= TOTAL_MSE_NONINFERIORITY for fold in folds
        ),
        "target_actual_xi_used_as_input": False,
        "updates_delayed_until_source_observed_at": True,
        "same_season_state_reset": True,
        "identity_bridge_passed": True,
    }
    signal_pass = all(checks.values())
    return {
        "schema_version": "V5.0.2-player-xi-residual-signal-domain-r1",
        "generated_at_utc": utc_now(),
        "competition_id": competition_id,
        "status": (
            "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW"
            if signal_pass
            else "REJECT_KEEP_FORMAL_WEIGHT_0"
        ),
        "profiles": PROFILES,
        "data_audit": data_audit,
        "seasons": seasons,
        "outer_targets": outer_targets,
        "outer_prediction_count": len(outer_rows),
        "selected_profiles": selected_profiles,
        "folds": folds,
        "pooled_metrics": pooled,
        "paired_block_bootstrap": {
            "margin_mse": margin_bootstrap,
            "total_mse": total_bootstrap,
        },
        "checks": checks,
        "availability_evidence_status": "UNAVAILABLE_NOT_USED",
        "handicap_target_status": "UNAVAILABLE_NO_COMPLETE_POINT_IN_TIME_FROZEN_HANDICAP_LINES_IN_CURRENT_REPLAY",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Player-XI residual signal diagnostic only. Passing requires later unified-matrix OOF; rejection freezes this player signal at weight 0.",
    }


def run(*, write: bool) -> dict[str, Any]:
    reports: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for competition_id in PILOT_DOMAINS:
        try:
            report = validate_domain(competition_id)
            reports[competition_id] = report
            if write:
                atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
        except Exception as exc:
            failures[competition_id] = str(exc)
    passed = [
        cid for cid, report in reports.items()
        if report["status"] == "PLAYER_SIGNAL_PASS_MATRIX_PROJECTION_REVIEW"
    ]
    rejected = [
        cid for cid, report in reports.items()
        if report["status"] == "REJECT_KEEP_FORMAL_WEIGHT_0"
    ]
    manifest = {
        "schema_version": "V5.0.2-player-xi-residual-signal-aggregate-r1",
        "generated_at_utc": utc_now(),
        "status": "PASS" if not failures else "FAIL",
        "requested_domains": list(PILOT_DOMAINS),
        "completed_domains": sorted(reports),
        "signal_pass_domains": sorted(passed),
        "rejected_keep_formal_weight_0": sorted(rejected),
        "execution_failures": failures,
        "reports": {
            cid: {
                "status": report["status"],
                "outer_prediction_count": report["outer_prediction_count"],
                "selected_profiles": report["selected_profiles"],
                "pooled_metrics": report["pooled_metrics"],
                "paired_block_bootstrap": report["paired_block_bootstrap"],
                "checks": report["checks"],
            }
            for cid, report in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "policy": "Player-specific XI residual signal OOF only. Formal V5 probabilities remain unchanged.",
    }
    if write:
        atomic_write_json(OUT, manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    manifest = run(write=not args.check_only)
    if args.print_summary:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
