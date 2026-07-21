#!/usr/bin/env python3
"""Strict forward OOF for an AH-constrained dynamic-state blend.

For ESP and GER, a fixed dynamic-state profile is blended with the formal matrix on a
predeclared weight grid. For each target season the weight is selected using only
strictly earlier completed seasons. A non-zero weight is eligible only when prior-
season 1X2, score, total and retrospective AH criteria are jointly non-worse.

Football-Data AH lines have no original quote timestamps. This is research-only and
cannot authorize formal promotion or EV even if the challenger passes.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

import bayesian_dynamic_state_oof_v500 as base
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, load_json, score_matrix_rows
from retrospective_ah_dynamic_state_v501 import SOURCE_CODES, _line_lookup, _profile, _settlement

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
VERDICT = ROOT / "manifests" / "bayesian_dynamic_state_four_target_verdict_v501_status.json"
OUT = ROOT / "manifests" / "ah_constrained_dynamic_blend_v501_status.json"
REPORT_DIR = ROOT / "manifests" / "ah_constrained_dynamic_blend_v501"

WEIGHTS = (0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.65, 0.80, 1.0)
SEASONS = ("2022/23", "2023/24", "2024/25", "2025/26")
TARGET_SEASONS = ("2024/25", "2025/26")
MIN_SELECTION_ROWS = 500
EPS = 1e-15
BOOTSTRAP_DRAWS = 1200
BLOCK_SIZE = 20
SEED = 5012026

METRICS = (
    "one_x_two_accuracy",
    "one_x_two_brier",
    "one_x_two_rps",
    "joint_log",
    "score_top1",
    "score_top3",
    "total_top1",
    "total_top2",
    "total_rps",
)


def _normalize(matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(cell["probability"]) for cell in matrix)
    if total <= 0.0 or not math.isfinite(total):
        raise PlatformError("blend matrix has invalid mass")
    return [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "probability": float(cell["probability"]) / total,
        }
        for cell in matrix
    ]


def _geometric_blend(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    weight: float,
) -> list[dict[str, Any]]:
    if weight <= 0.0:
        return _normalize(baseline)
    if weight >= 1.0:
        return _normalize(candidate)
    base_map = {(h, a): p for h, a, p in score_matrix_rows(baseline)}
    cand_map = {(h, a): p for h, a, p in score_matrix_rows(candidate)}
    support = sorted(set(base_map) | set(cand_map))
    rows = []
    for home, away in support:
        p0 = max(EPS, float(base_map.get((home, away), 0.0)))
        p1 = max(EPS, float(cand_map.get((home, away), 0.0)))
        probability = math.exp((1.0 - weight) * math.log(p0) + weight * math.log(p1))
        rows.append({"home_goals": home, "away_goals": away, "probability": probability})
    return _normalize(rows)


def _probability_residual(matrix: list[dict[str, Any]]) -> float:
    return abs(sum(float(cell["probability"]) for cell in matrix) - 1.0)


def _simulate_season(
    competition_id: str,
    season: str,
    profile_id: str,
    code: str,
) -> dict[str, Any]:
    profile = _profile(profile_id)
    formal_report = load_json(base.REPORT_ROOT / f"{competition_id}.json")
    all_matches = base.read_processed_matches(competition_id)
    fold = base._fold_for_season(formal_report, season)
    parameters = fold.get("selected_parameters")
    if not isinstance(parameters, dict):
        raise PlatformError(f"missing formal parameters for {competition_id} {season}")
    temperature, calibration_mode = base._target_season_temperature(competition_id, season)
    prior_home, prior_away, prior_count = base._prior_league_rates(all_matches, season)
    line_lookup, source_audit = _line_lookup(season, code)

    matches = sorted(
        [match for match in all_matches if str(match.season) == season],
        key=lambda match: (match.date, match.home_team, match.away_team),
    )
    if not matches:
        raise PlatformError(f"no matches for {competition_id} {season}")

    states: dict[str, Any] = {}
    league = {
        "home_alpha": prior_home * float(profile["league_prior_matches"]),
        "home_beta": float(profile["league_prior_matches"]),
        "away_alpha": prior_away * float(profile["league_prior_matches"]),
        "away_beta": float(profile["league_prior_matches"]),
    }
    by_day: dict[str, list[Any]] = defaultdict(list)
    for match in matches:
        by_day[match.date.date().isoformat()].append(match)

    rows: dict[float, list[dict[str, Any]]] = {weight: [] for weight in WEIGHTS}
    max_probability_residual = 0.0
    max_total_residual = 0.0

    for day in sorted(by_day):
        day_matches = sorted(by_day[day], key=lambda match: (match.date, match.home_team, match.away_team))

        # Prediction phase: all outcomes from this date are withheld.
        for match in day_matches:
            key = (
                match.date.date().isoformat(),
                base.normalize_team_token(match.home_team),
                base.normalize_team_token(match.away_team),
            )
            line_item = line_lookup.get(key)
            if line_item is None:
                continue
            try:
                baseline = base._predict_from_loaded_matches(
                    all_matches,
                    match.home_team,
                    match.away_team,
                    match.date,
                    season,
                    parameters,
                )
            except PlatformError:
                continue
            if abs(temperature - 1.0) > EPS:
                baseline = temperature_scale_matrix(baseline, temperature)

            league_home = league["home_alpha"] / league["home_beta"]
            league_away = league["away_alpha"] / league["away_beta"]
            dynamic_home, dynamic_away, state_audit = base._dynamic_rates(
                states,
                match.home_team,
                match.away_team,
                match.date,
                league_home,
                league_away,
                profile,
            )
            full_candidate, tilt_audit = base._candidate_from_baseline(
                baseline,
                dynamic_home,
                dynamic_away,
                profile,
            )
            max_total_residual = max(
                max_total_residual,
                abs(float(tilt_audit["max_total_marginal_residual"])),
            )

            baseline_metrics = base._metric_row(baseline, match)
            baseline_settlement = _settlement(baseline, float(line_item["line"]), match)
            row_key = (
                f"{competition_id}:{season}:{match.date.date().isoformat()}:"
                f"{match.home_team}:{match.away_team}"
            )

            for weight in WEIGHTS:
                blended = _geometric_blend(baseline, full_candidate, weight)
                metrics = base._metric_row(blended, match)
                settlement = _settlement(blended, float(line_item["line"]), match)
                max_probability_residual = max(max_probability_residual, _probability_residual(blended))
                row = {
                    "match_key": row_key,
                    "season": season,
                    "date": match.date.date().isoformat(),
                    "weight": weight,
                    "line": float(line_item["line"]),
                    "line_field": line_item["field"],
                    "baseline_ah_payoff": baseline_settlement["selected_payoff"],
                    "candidate_ah_payoff": settlement["selected_payoff"],
                    "baseline_ah_picked_home": baseline_settlement["picked_home"],
                    "candidate_ah_picked_home": settlement["picked_home"],
                    "same_day_outcomes_withheld": True,
                    "state_audit": state_audit,
                }
                for metric in METRICS:
                    row[f"baseline_{metric}"] = baseline_metrics[metric]
                    row[f"candidate_{metric}"] = metrics[metric]
                rows[weight].append(row)

        # Update phase after all predictions for the date.
        for match in day_matches:
            league_home = league["home_alpha"] / league["home_beta"]
            league_away = league["away_alpha"] / league["away_beta"]
            base._update_states(
                states,
                match.home_team,
                match.away_team,
                match.date,
                int(match.home_goals),
                int(match.away_goals),
                league_home,
                league_away,
                profile,
            )
            league["home_alpha"] += int(match.home_goals)
            league["home_beta"] += 1.0
            league["away_alpha"] += int(match.away_goals)
            league["away_beta"] += 1.0

    return {
        "season": season,
        "profile": profile_id,
        "rows": rows,
        "source": source_audit,
        "prior_league_match_count": prior_count,
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "same_day_outcomes_withheld": True,
        "max_probability_sum_residual": max_probability_residual,
        "max_total_marginal_residual": max_total_residual,
    }


def _mean(rows: list[dict[str, Any]], key: str) -> float:
    return mean(float(row[key]) for row in rows)


def _ah_summary(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    values = [
        float(row[f"{prefix}_ah_payoff"])
        for row in rows
        if row.get(f"{prefix}_ah_payoff") is not None
    ]
    wins = sum(value > 1e-12 for value in values)
    pushes = sum(abs(value) <= 1e-12 for value in values)
    losses = sum(value < -1e-12 for value in values)
    decided = wins + losses
    return {
        "wins": wins,
        "pushes": pushes,
        "losses": losses,
        "decided": decided,
        "hit_rate_excluding_pushes": wins / decided if decided else None,
        "mean_settlement_payoff": mean(values) if values else None,
    }


def _paired_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for metric in METRICS:
        baseline = _mean(rows, f"baseline_{metric}")
        candidate = _mean(rows, f"candidate_{metric}")
        output[metric] = {
            "baseline": baseline,
            "candidate": candidate,
            "candidate_minus_baseline": candidate - baseline,
        }
    output["ah"] = {
        "baseline": _ah_summary(rows, "baseline"),
        "candidate": _ah_summary(rows, "candidate"),
    }
    base_hit = output["ah"]["baseline"]["hit_rate_excluding_pushes"]
    cand_hit = output["ah"]["candidate"]["hit_rate_excluding_pushes"]
    base_payoff = output["ah"]["baseline"]["mean_settlement_payoff"]
    cand_payoff = output["ah"]["candidate"]["mean_settlement_payoff"]
    output["ah"]["hit_rate_difference"] = (
        cand_hit - base_hit if cand_hit is not None and base_hit is not None else None
    )
    output["ah"]["mean_settlement_payoff_difference"] = (
        cand_payoff - base_payoff if cand_payoff is not None and base_payoff is not None else None
    )
    return output


def _selection_objective(rows: list[dict[str, Any]]) -> float:
    return (
        _mean(rows, "candidate_one_x_two_rps")
        + 0.25 * _mean(rows, "candidate_one_x_two_brier")
        + 3.0 * _mean(rows, "candidate_total_rps")
        + 0.02 * _mean(rows, "candidate_joint_log")
    )


def _eligible(summary: dict[str, Any], weight: float, count: int) -> tuple[bool, list[str]]:
    reasons = []
    if count < MIN_SELECTION_ROWS:
        reasons.append("insufficient_prior_rows")
    for metric in ("one_x_two_accuracy", "score_top1", "score_top3", "total_top1", "total_top2"):
        if float(summary[metric]["candidate_minus_baseline"]) < -1e-12:
            reasons.append(f"{metric}_worse")
    if float(summary["one_x_two_brier"]["candidate_minus_baseline"]) > 0.0:
        reasons.append("one_x_two_brier_worse")
    if float(summary["one_x_two_rps"]["candidate_minus_baseline"]) > 0.0:
        reasons.append("one_x_two_rps_worse")
    if float(summary["joint_log"]["candidate_minus_baseline"]) > 0.002:
        reasons.append("joint_log_worse")
    if float(summary["total_rps"]["candidate_minus_baseline"]) > 0.0005:
        reasons.append("total_rps_worse")
    ah = summary["ah"]
    if ah["hit_rate_difference"] is None or float(ah["hit_rate_difference"]) < -1e-12:
        reasons.append("ah_hit_rate_worse")
    if ah["mean_settlement_payoff_difference"] is None or float(ah["mean_settlement_payoff_difference"]) < -1e-12:
        reasons.append("ah_payoff_worse")
    if weight == 0.0:
        reasons = [reason for reason in reasons if reason == "insufficient_prior_rows"]
    return not reasons, reasons


def _blocks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: (row["season"], row["date"], row["match_key"]))
    return [ordered[index:index + BLOCK_SIZE] for index in range(0, len(ordered), BLOCK_SIZE)]


def _bootstrap(rows: list[dict[str, Any]], metric: str, seed: int) -> dict[str, Any]:
    blocks = _blocks(rows)
    point = _mean(rows, f"candidate_{metric}") - _mean(rows, f"baseline_{metric}")
    rng = random.Random(seed)
    samples = []
    for _ in range(BOOTSTRAP_DRAWS):
        sample = []
        for _ in range(len(blocks)):
            sample.extend(rng.choice(blocks))
        samples.append(_mean(sample, f"candidate_{metric}") - _mean(sample, f"baseline_{metric}"))
    samples.sort()
    return {
        "mean_difference": point,
        "ci95_lower": samples[int(0.025 * (len(samples) - 1))],
        "ci95_upper": samples[int(0.975 * (len(samples) - 1))],
        "blocks": len(blocks),
        "draws": BOOTSTRAP_DRAWS,
    }


def _domain(competition_id: str, profile_id: str, code: str) -> dict[str, Any]:
    simulations = {
        season: _simulate_season(competition_id, season, profile_id, code)
        for season in SEASONS
    }
    outer_rows: list[dict[str, Any]] = []
    folds = []

    for target in TARGET_SEASONS:
        target_index = SEASONS.index(target)
        prior_seasons = SEASONS[:target_index]
        candidates = []
        for weight in WEIGHTS:
            prior_rows = [
                row
                for season in prior_seasons
                for row in simulations[season]["rows"][weight]
            ]
            summary = _paired_summary(prior_rows) if prior_rows else None
            eligible, reasons = _eligible(summary, weight, len(prior_rows)) if summary else (False, ["no_prior_rows"])
            candidates.append({
                "weight": weight,
                "prior_row_count": len(prior_rows),
                "eligible": eligible,
                "ineligible_reasons": reasons,
                "selection_objective": _selection_objective(prior_rows) if prior_rows else None,
                "prior_summary": summary,
            })
        eligible_candidates = [item for item in candidates if item["eligible"]]
        if not eligible_candidates:
            raise PlatformError(f"no eligible weight, including baseline, for {competition_id} {target}")
        eligible_candidates.sort(key=lambda item: (float(item["selection_objective"]), float(item["weight"])))
        selected = eligible_candidates[0]
        selected_weight = float(selected["weight"])
        target_rows = simulations[target]["rows"][selected_weight]
        if not target_rows:
            raise PlatformError(f"no target rows for {competition_id} {target}")
        outer_rows.extend(target_rows)
        folds.append({
            "target_season": target,
            "prior_seasons": list(prior_seasons),
            "selected_weight": selected_weight,
            "selected_profile": profile_id,
            "prior_row_count": selected["prior_row_count"],
            "selection_objective": selected["selection_objective"],
            "weight_grid_audit": candidates,
            "outer_prediction_count": len(target_rows),
            "target_metrics": _paired_summary(target_rows),
        })

    pooled = _paired_summary(outer_rows)
    ci = {
        "one_x_two_brier": _bootstrap(outer_rows, "one_x_two_brier", SEED + 1),
        "one_x_two_rps": _bootstrap(outer_rows, "one_x_two_rps", SEED + 2),
        "joint_log": _bootstrap(outer_rows, "joint_log", SEED + 3),
        "total_rps": _bootstrap(outer_rows, "total_rps", SEED + 4),
    }
    selected_weights = [float(fold["selected_weight"]) for fold in folds]
    season_ah_hit_diffs = [
        float(fold["target_metrics"]["ah"]["hit_rate_difference"])
        for fold in folds
        if fold["target_metrics"]["ah"]["hit_rate_difference"] is not None
    ]
    season_ah_payoff_diffs = [
        float(fold["target_metrics"]["ah"]["mean_settlement_payoff_difference"])
        for fold in folds
        if fold["target_metrics"]["ah"]["mean_settlement_payoff_difference"] is not None
    ]
    max_probability_residual = max(
        float(simulations[season]["max_probability_sum_residual"])
        for season in SEASONS
    )
    max_total_residual = max(
        float(simulations[season]["max_total_marginal_residual"])
        for season in SEASONS
    )

    checks = {
        "two_forward_outer_seasons": len(folds) == 2,
        "minimum_outer_predictions_500": len(outer_rows) >= 500,
        "nonzero_weight_selected_in_both_folds": all(weight > 0.0 for weight in selected_weights),
        "one_x_two_brier_ci_improves": ci["one_x_two_brier"]["ci95_upper"] < 0.0,
        "one_x_two_rps_ci_improves": ci["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= 0.002,
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= 0.0005,
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate_minus_baseline"] >= -1e-12,
        "score_top1_nonworse": pooled["score_top1"]["candidate_minus_baseline"] >= -1e-12,
        "score_top3_nonworse": pooled["score_top3"]["candidate_minus_baseline"] >= -1e-12,
        "total_top1_nonworse": pooled["total_top1"]["candidate_minus_baseline"] >= -1e-12,
        "total_top2_nonworse": pooled["total_top2"]["candidate_minus_baseline"] >= -1e-12,
        "ah_hit_rate_nonworse_each_outer_season": bool(season_ah_hit_diffs) and min(season_ah_hit_diffs) >= -1e-12,
        "ah_payoff_nonworse_each_outer_season": bool(season_ah_payoff_diffs) and min(season_ah_payoff_diffs) >= -1e-12,
        "ah_hit_rate_nonworse_pooled": pooled["ah"]["hit_rate_difference"] is not None and pooled["ah"]["hit_rate_difference"] >= -1e-12,
        "ah_payoff_nonworse_pooled": pooled["ah"]["mean_settlement_payoff_difference"] is not None and pooled["ah"]["mean_settlement_payoff_difference"] >= -1e-12,
        "probability_conservation": max_probability_residual <= 1e-10,
        "same_day_outcomes_withheld": True,
        "formal_pit_handicap_evidence_available": False,
    }
    research_checks = {key: value for key, value in checks.items() if key != "formal_pit_handicap_evidence_available"}
    research_pass = all(research_checks.values())

    return {
        "schema_version": "V5.0.1-ah-constrained-dynamic-blend-domain-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": competition_id,
        "status": "RESEARCH_CANDIDATE_FORMAL_PIT_BLOCKED" if research_pass else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "profile": profile_id,
        "weight_grid": list(WEIGHTS),
        "folds": folds,
        "outer_prediction_count": len(outer_rows),
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "selected_weights": selected_weights,
        "minimum_outer_season_ah_hit_rate_difference": min(season_ah_hit_diffs) if season_ah_hit_diffs else None,
        "minimum_outer_season_ah_payoff_difference": min(season_ah_payoff_diffs) if season_ah_payoff_diffs else None,
        "max_probability_sum_residual": max_probability_residual,
        "max_total_marginal_residual": max_total_residual,
        "checks": checks,
        "same_day_outcomes_withheld": True,
        "market_evidence_classification": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
        "original_quote_timestamp_available": False,
        "formal_promotion_authorized": False,
        "policy": "Research-only nested OOF. Formal promotion remains prohibited without timestamped point-in-time handicap evidence and a future CURRENT-compliant receipt.",
    }


def main() -> int:
    adjudication = load_json(ADJUDICATION)
    verdict = load_json(VERDICT)
    if adjudication.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("adjudication is not same-day-safe")
    if verdict.get("status") != "PASS_NO_FORMAL_PROMOTION":
        raise PlatformError("four-target verdict missing or invalid")

    requested = [competition_id for competition_id in ("ESP_LaLiga", "GER_Bundesliga")]
    reports = {}
    failures = {}
    research_candidates = []
    for competition_id in requested:
        try:
            item = (adjudication.get("adjudications") or {}).get(competition_id) or {}
            profile_id = str(item.get("frozen_shadow_profile") or "")
            if not profile_id:
                raise PlatformError(f"frozen profile missing for {competition_id}")
            report = _domain(competition_id, profile_id, SOURCE_CODES[competition_id])
            reports[competition_id] = report
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
            if report["status"] == "RESEARCH_CANDIDATE_FORMAL_PIT_BLOCKED":
                research_candidates.append(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.0.1-ah-constrained-dynamic-blend-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(requested) and not failures else "PARTIAL",
        "requested_domains": requested,
        "completed_domains": sorted(reports),
        "research_candidates_formal_pit_blocked": research_candidates,
        "rejected_keep_formal_weight_0": sorted(set(reports) - set(research_candidates)),
        "failures": failures,
        "reports": {
            competition_id: {
                "status": report["status"],
                "selected_weights": report["selected_weights"],
                "outer_prediction_count": report["outer_prediction_count"],
                "pooled_metrics": report["pooled_metrics"],
                "checks": report["checks"],
            }
            for competition_id, report in reports.items()
        },
        "formal_weight_change": False,
        "probability_change": False,
        "automatic_promotion": False,
        "formal_promotion_authorized": False,
        "policy": "AH-constrained research only. No result alters the formal V5 matrix without timestamped PIT handicap evidence and a new CURRENT-compliant promotion receipt.",
    }
    atomic_write_json(OUT, payload)
    print({
        "status": payload["status"],
        "candidates": research_candidates,
        "rejected": payload["rejected_keep_formal_weight_0"],
        "failures": failures,
    })
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
