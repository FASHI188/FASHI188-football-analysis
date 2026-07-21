#!/usr/bin/env python3
"""Nested forward OOF for a two-axis dynamic projection.

The dynamic-state signal is decomposed into:
- total_scale: influence on direct total-goals mean P(T);
- share_scale: influence on within-total home/away allocation P(D|T).

Each target season selects the pair using only earlier seasons. Any pair that degrades
retrospective AH hit rate/payoff or another target on prior seasons is ineligible.
Historical AH lines remain retrospective-only and cannot authorize formal promotion.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import platform_core
import validate_ah_constrained_dynamic_blend_v501 as common
from oof_matrix_calibration import temperature_scale_matrix
from platform_core import PlatformError, atomic_write_json, load_json

base = common.base
base.normalize_team_token = platform_core.normalize_team_token

ROOT = Path(__file__).resolve().parents[1]
ADJUDICATION = ROOT / "manifests" / "bayesian_dynamic_state_adjudication_v501_status.json"
BLEND_RESULT = ROOT / "manifests" / "ah_constrained_dynamic_blend_v501_status.json"
OUT = ROOT / "manifests" / "two_axis_dynamic_projection_v501_status.json"
REPORT_DIR = ROOT / "manifests" / "two_axis_dynamic_projection_v501"

TOTAL_SCALES = (0.0, 0.25, 0.50, 0.75, 1.0)
SHARE_SCALES = (0.0, 0.25, 0.50, 0.75, 1.0)
CONFIGS = tuple((total_scale, share_scale) for total_scale in TOTAL_SCALES for share_scale in SHARE_SCALES)
SEASONS = common.SEASONS
TARGET_SEASONS = common.TARGET_SEASONS
MIN_SELECTION_ROWS = common.MIN_SELECTION_ROWS
EPS = common.EPS


def _config_id(total_scale: float, share_scale: float) -> str:
    return f"T{total_scale:.2f}_D{share_scale:.2f}"


def _project(
    baseline: list[dict[str, Any]],
    dynamic_home: float,
    dynamic_away: float,
    profile: dict[str, Any],
    total_scale: float,
    share_scale: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if total_scale <= 0.0 and share_scale <= 0.0:
        return common._normalize(baseline), {
            "total_scale": total_scale,
            "share_scale": share_scale,
            "probability_sum_residual": common._probability_residual(baseline),
            "max_total_marginal_residual": 0.0,
            "baseline_identity": True,
        }

    base_home, base_away, base_total = base._matrix_means(baseline)
    dynamic_total = dynamic_home + dynamic_away
    total_weight = float(profile["total_weight"]) * total_scale
    share_weight = float(profile["share_weight"]) * share_scale

    target_total = math.exp(
        (1.0 - total_weight) * math.log(max(EPS, base_total))
        + total_weight * math.log(max(EPS, dynamic_total))
    )
    base_share = base_home / max(EPS, base_total)
    dynamic_share = dynamic_home / max(EPS, dynamic_total)
    target_share = base._logistic(
        (1.0 - share_weight) * base._logit(base_share)
        + share_weight * base._logit(dynamic_share)
    )

    total_tilted, total_audit = base._total_tilt(baseline, target_total)
    final, share_audit = base._home_share_tilt_preserve_totals(
        total_tilted,
        target_total * target_share,
    )
    audit = {
        "total_scale": total_scale,
        "share_scale": share_scale,
        "effective_total_weight": total_weight,
        "effective_share_weight": share_weight,
        "baseline_total_mean": base_total,
        "dynamic_total_mean": dynamic_total,
        "target_total_mean": target_total,
        "baseline_home_share": base_share,
        "dynamic_home_share": dynamic_share,
        "target_home_share": target_share,
        **total_audit,
        **share_audit,
        "probability_sum_residual": common._probability_residual(final),
    }
    return final, audit


def _simulate_season(
    competition_id: str,
    season: str,
    profile_id: str,
    code: str,
) -> dict[str, Any]:
    profile = common._profile(profile_id)
    formal_report = load_json(base.REPORT_ROOT / f"{competition_id}.json")
    all_matches = base.read_processed_matches(competition_id)
    fold = base._fold_for_season(formal_report, season)
    parameters = fold.get("selected_parameters")
    if not isinstance(parameters, dict):
        raise PlatformError(f"missing formal parameters for {competition_id} {season}")
    temperature, calibration_mode = base._target_season_temperature(competition_id, season)
    prior_home, prior_away, prior_count = base._prior_league_rates(all_matches, season)
    line_lookup, source_audit = common._line_lookup(season, code)

    matches = sorted(
        [match for match in all_matches if str(match.season) == season],
        key=lambda match: (match.date, match.home_team, match.away_team),
    )
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

    rows: dict[str, list[dict[str, Any]]] = {
        _config_id(total_scale, share_scale): []
        for total_scale, share_scale in CONFIGS
    }
    max_probability_residual = 0.0
    max_total_residual = 0.0

    for day in sorted(by_day):
        day_matches = sorted(by_day[day], key=lambda match: (match.date, match.home_team, match.away_team))
        for match in day_matches:
            key = (
                match.date.date().isoformat(),
                platform_core.normalize_team_token(match.home_team),
                platform_core.normalize_team_token(match.away_team),
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
            baseline_metrics = base._metric_row(baseline, match)
            baseline_settlement = common._settlement(baseline, float(line_item["line"]), match)
            match_key = (
                f"{competition_id}:{season}:{match.date.date().isoformat()}:"
                f"{match.home_team}:{match.away_team}"
            )

            for total_scale, share_scale in CONFIGS:
                config_id = _config_id(total_scale, share_scale)
                candidate, audit = _project(
                    baseline,
                    dynamic_home,
                    dynamic_away,
                    profile,
                    total_scale,
                    share_scale,
                )
                metrics = base._metric_row(candidate, match)
                settlement = common._settlement(candidate, float(line_item["line"]), match)
                max_probability_residual = max(
                    max_probability_residual,
                    float(audit["probability_sum_residual"]),
                )
                max_total_residual = max(
                    max_total_residual,
                    abs(float(audit.get("max_total_marginal_residual", 0.0))),
                )
                row = {
                    "match_key": match_key,
                    "season": season,
                    "date": match.date.date().isoformat(),
                    "config_id": config_id,
                    "total_scale": total_scale,
                    "share_scale": share_scale,
                    "line": float(line_item["line"]),
                    "baseline_ah_payoff": baseline_settlement["selected_payoff"],
                    "candidate_ah_payoff": settlement["selected_payoff"],
                    "baseline_ah_picked_home": baseline_settlement["picked_home"],
                    "candidate_ah_picked_home": settlement["picked_home"],
                    "same_day_outcomes_withheld": True,
                    "state_audit": state_audit,
                }
                for metric in common.METRICS:
                    row[f"baseline_{metric}"] = baseline_metrics[metric]
                    row[f"candidate_{metric}"] = metrics[metric]
                rows[config_id].append(row)

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
        "rows": rows,
        "profile": profile_id,
        "source": source_audit,
        "prior_league_match_count": prior_count,
        "oof_calibration": {"temperature": temperature, "mode": calibration_mode},
        "same_day_outcomes_withheld": True,
        "max_probability_sum_residual": max_probability_residual,
        "max_total_marginal_residual": max_total_residual,
    }


def _eligible(summary: dict[str, Any], config_id: str, count: int) -> tuple[bool, list[str]]:
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
    if summary["ah"]["hit_rate_difference"] is None or float(summary["ah"]["hit_rate_difference"]) < -1e-12:
        reasons.append("ah_hit_rate_worse")
    if summary["ah"]["mean_settlement_payoff_difference"] is None or float(summary["ah"]["mean_settlement_payoff_difference"]) < -1e-12:
        reasons.append("ah_payoff_worse")
    if config_id == _config_id(0.0, 0.0):
        reasons = [reason for reason in reasons if reason == "insufficient_prior_rows"]
    return not reasons, reasons


def _domain(competition_id: str, profile_id: str, code: str) -> dict[str, Any]:
    simulations = {
        season: _simulate_season(competition_id, season, profile_id, code)
        for season in SEASONS
    }
    folds = []
    outer_rows = []

    for target in TARGET_SEASONS:
        target_index = SEASONS.index(target)
        prior_seasons = SEASONS[:target_index]
        candidates = []
        for total_scale, share_scale in CONFIGS:
            config_id = _config_id(total_scale, share_scale)
            prior_rows = [
                row
                for season in prior_seasons
                for row in simulations[season]["rows"][config_id]
            ]
            summary = common._paired_summary(prior_rows) if prior_rows else None
            eligible, reasons = _eligible(summary, config_id, len(prior_rows)) if summary else (False, ["no_prior_rows"])
            candidates.append({
                "config_id": config_id,
                "total_scale": total_scale,
                "share_scale": share_scale,
                "prior_row_count": len(prior_rows),
                "eligible": eligible,
                "ineligible_reasons": reasons,
                "selection_objective": common._selection_objective(prior_rows) if prior_rows else None,
                "prior_summary": summary,
            })
        eligible_candidates = [item for item in candidates if item["eligible"]]
        if not eligible_candidates:
            raise PlatformError(f"no eligible configuration for {competition_id} {target}")
        eligible_candidates.sort(
            key=lambda item: (
                float(item["selection_objective"]),
                float(item["share_scale"]),
                float(item["total_scale"]),
            )
        )
        selected = eligible_candidates[0]
        config_id = selected["config_id"]
        target_rows = simulations[target]["rows"][config_id]
        outer_rows.extend(target_rows)
        folds.append({
            "target_season": target,
            "prior_seasons": list(prior_seasons),
            "selected_config": config_id,
            "selected_total_scale": selected["total_scale"],
            "selected_share_scale": selected["share_scale"],
            "selected_profile": profile_id,
            "prior_row_count": selected["prior_row_count"],
            "selection_objective": selected["selection_objective"],
            "configuration_grid_audit": candidates,
            "outer_prediction_count": len(target_rows),
            "target_metrics": common._paired_summary(target_rows),
        })

    pooled = common._paired_summary(outer_rows)
    ci = {
        "one_x_two_brier": common._bootstrap(outer_rows, "one_x_two_brier", common.SEED + 201),
        "one_x_two_rps": common._bootstrap(outer_rows, "one_x_two_rps", common.SEED + 202),
        "joint_log": common._bootstrap(outer_rows, "joint_log", common.SEED + 203),
        "total_rps": common._bootstrap(outer_rows, "total_rps", common.SEED + 204),
    }
    selected_configs = [fold["selected_config"] for fold in folds]
    nonbaseline = [config_id != _config_id(0.0, 0.0) for config_id in selected_configs]
    season_hit_diffs = [float(fold["target_metrics"]["ah"]["hit_rate_difference"]) for fold in folds]
    season_payoff_diffs = [float(fold["target_metrics"]["ah"]["mean_settlement_payoff_difference"]) for fold in folds]
    max_probability_residual = max(float(item["max_probability_sum_residual"]) for item in simulations.values())
    max_total_residual = max(float(item["max_total_marginal_residual"]) for item in simulations.values())

    checks = {
        "two_forward_outer_seasons": len(folds) == 2,
        "minimum_outer_predictions_500": len(outer_rows) >= 500,
        "nonbaseline_configuration_selected_in_both_folds": all(nonbaseline),
        "one_x_two_brier_ci_improves": ci["one_x_two_brier"]["ci95_upper"] < 0.0,
        "one_x_two_rps_ci_improves": ci["one_x_two_rps"]["ci95_upper"] < 0.0,
        "joint_log_ci_noninferior": ci["joint_log"]["ci95_upper"] <= 0.002,
        "total_rps_ci_noninferior": ci["total_rps"]["ci95_upper"] <= 0.0005,
        "one_x_two_accuracy_nonworse": pooled["one_x_two_accuracy"]["candidate_minus_baseline"] >= -1e-12,
        "score_top1_nonworse": pooled["score_top1"]["candidate_minus_baseline"] >= -1e-12,
        "score_top3_nonworse": pooled["score_top3"]["candidate_minus_baseline"] >= -1e-12,
        "total_top1_nonworse": pooled["total_top1"]["candidate_minus_baseline"] >= -1e-12,
        "total_top2_nonworse": pooled["total_top2"]["candidate_minus_baseline"] >= -1e-12,
        "ah_hit_rate_nonworse_each_outer_season": min(season_hit_diffs) >= -1e-12,
        "ah_payoff_nonworse_each_outer_season": min(season_payoff_diffs) >= -1e-12,
        "ah_hit_rate_nonworse_pooled": pooled["ah"]["hit_rate_difference"] >= -1e-12,
        "ah_payoff_nonworse_pooled": pooled["ah"]["mean_settlement_payoff_difference"] >= -1e-12,
        "probability_conservation": max_probability_residual <= 1e-10,
        "same_day_outcomes_withheld": True,
        "formal_pit_handicap_evidence_available": False,
    }
    research_checks = {key: value for key, value in checks.items() if key != "formal_pit_handicap_evidence_available"}
    research_pass = all(research_checks.values())

    return {
        "schema_version": "V5.0.1-two-axis-dynamic-projection-domain-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "competition_id": competition_id,
        "status": "RESEARCH_CANDIDATE_FORMAL_PIT_BLOCKED" if research_pass else "REJECT_KEEP_FORMAL_WEIGHT_0",
        "formal_weight": 0,
        "probability_change": False,
        "automatic_promotion": False,
        "profile": profile_id,
        "total_scale_grid": list(TOTAL_SCALES),
        "share_scale_grid": list(SHARE_SCALES),
        "folds": folds,
        "outer_prediction_count": len(outer_rows),
        "pooled_metrics": pooled,
        "paired_block_bootstrap": ci,
        "selected_configurations": selected_configs,
        "max_probability_sum_residual": max_probability_residual,
        "max_total_marginal_residual": max_total_residual,
        "checks": checks,
        "same_day_outcomes_withheld": True,
        "market_evidence_classification": "RETROSPECTIVE_MARKET_REFERENCE_ONLY",
        "original_quote_timestamp_available": False,
        "formal_promotion_authorized": False,
        "policy": "Two-axis nested OOF research only. Formal promotion remains prohibited without timestamped PIT handicap evidence and a CURRENT-compliant receipt.",
    }


def main() -> int:
    adjudication = load_json(ADJUDICATION)
    blend_result = load_json(BLEND_RESULT)
    if adjudication.get("same_day_outcomes_withheld") is not True:
        raise PlatformError("adjudication is not same-day-safe")
    if blend_result.get("status") != "PASS":
        raise PlatformError("single-axis blend result missing")

    requested = ("ESP_LaLiga", "GER_Bundesliga")
    reports = {}
    failures = {}
    candidates = []
    for competition_id in requested:
        try:
            item = (adjudication.get("adjudications") or {}).get(competition_id) or {}
            profile_id = str(item.get("frozen_shadow_profile") or "")
            report = _domain(competition_id, profile_id, common.SOURCE_CODES[competition_id])
            reports[competition_id] = report
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(REPORT_DIR / f"{competition_id}.json", report)
            if report["status"] == "RESEARCH_CANDIDATE_FORMAL_PIT_BLOCKED":
                candidates.append(competition_id)
        except Exception as exc:
            failures[competition_id] = f"{type(exc).__name__}: {exc}"

    payload = {
        "schema_version": "V5.0.1-two-axis-dynamic-projection-aggregate-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if len(reports) == len(requested) and not failures else "PARTIAL",
        "requested_domains": list(requested),
        "completed_domains": sorted(reports),
        "research_candidates_formal_pit_blocked": candidates,
        "rejected_keep_formal_weight_0": sorted(set(reports) - set(candidates)),
        "failures": failures,
        "reports": {
            competition_id: {
                "status": report["status"],
                "selected_configurations": report["selected_configurations"],
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
        "policy": "Research only. Formal V5 probabilities remain unchanged.",
    }
    atomic_write_json(OUT, payload)
    print({"status": payload["status"], "candidates": candidates, "rejected": payload["rejected_keep_formal_weight_0"], "failures": failures})
    return 0 if payload["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
