#!/usr/bin/env python3
"""Prepare, validate, freeze and audit a formal single-match calculation.

The script never invents probabilities. A formal calculation artifact must be
produced under the active project CURRENT rule and then supplied to `validate`
and `freeze`.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from datetime import timezone
from pathlib import Path
from typing import Any

from platform_core import (
    EPSILON,
    MARGINAL_TOLERANCE,
    ROOT,
    PlatformError,
    atomic_write_json,
    compare_marginals,
    derive_score_marginals,
    expected_value,
    load_json,
    load_registry,
    log_score,
    multiclass_brier,
    normalize_probability,
    normalize_team_token,
    parse_iso_datetime,
    ranked_probability_score,
    registry_map,
    settle_home_handicap,
    settle_over_total,
    sha256_file,
    sha256_json,
    top_scores,
    utc_now,
    validate_probability_vector,
)

TEAM_STRENGTH_ROOT = ROOT / "team_strengths"
FREEZE_ROOT = ROOT / "prediction_freezes"
AUDIT_ROOT = ROOT / "postmatch_audits"
VALID_STATES = {"通过", "部分通过", "警告", "失败", "未启用", "不可用", "降级", "弃权", "不适用"}
FIXED_TOTAL_UNAVAILABLE = "总进球分布不可用。"
FIXED_SCORE_UNAVAILABLE = "精确比分不可用。"


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlatformError(f"{field} must be a non-empty string")
    return value.strip()


def _market_assessment(snapshot: Any, freeze_time, kickoff) -> dict[str, Any]:
    if not snapshot:
        return {
            "status": "不可用",
            "error_codes": ["M01"],
            "reason": "No question-time market snapshot was supplied.",
            "complete_1x2": False,
            "complete_asian_handicap": False,
            "complete_total_goals": False,
            "synchronized": False,
            "tradable_prices": False,
            "ev_gate": False,
        }
    if not isinstance(snapshot, dict):
        raise PlatformError("market_snapshot must be an object or null")
    observed_at = parse_iso_datetime(snapshot.get("observed_at_utc"), "market_snapshot.observed_at_utc")
    errors: list[str] = []
    if observed_at > freeze_time:
        errors.append("M03")
    if observed_at >= kickoff:
        errors.append("M03")

    sources = snapshot.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("M02")
        source_times = []
        tradable = False
    else:
        source_times = []
        tradable = True
        source_names = set()
        source_groups = set()
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise PlatformError(f"market_snapshot.sources[{index}] must be an object")
            name = _require_nonempty_string(source.get("name"), f"market_snapshot.sources[{index}].name")
            source_names.add(name)
            group = source.get("group") or name
            source_groups.add(group)
            timestamp = parse_iso_datetime(source.get("observed_at_utc"), f"market_snapshot.sources[{index}].observed_at_utc")
            source_times.append(timestamp)
            if timestamp > freeze_time or timestamp >= kickoff:
                errors.append("M03")
            if source.get("tradable") is False:
                tradable = False
        if len(source_names) > 1 and len(source_groups) == 1:
            errors.append("M04")

    synchronized = False
    max_skew_seconds = None
    if source_times:
        max_skew_seconds = int((max(source_times) - min(source_times)).total_seconds())
        synchronized = max_skew_seconds <= 15 * 60
        if not synchronized:
            errors.append("M03")

    def complete_prices(key: str, fields: tuple[str, ...]) -> bool:
        market = snapshot.get(key)
        if not isinstance(market, dict):
            return False
        try:
            values = [float(market[field]) for field in fields]
        except (KeyError, TypeError, ValueError):
            return False
        return all(math.isfinite(value) and value > 1.0 for value in values)

    complete_1x2 = complete_prices("one_x_two", ("home", "draw", "away"))
    complete_ah = complete_prices("asian_handicap", ("home", "away")) and isinstance(snapshot.get("asian_handicap", {}).get("line"), (int, float))
    complete_ou = complete_prices("total_goals", ("over", "under")) and isinstance(snapshot.get("total_goals", {}).get("line"), (int, float))
    if not complete_1x2 or not complete_ah or not complete_ou:
        errors.append("M01")
    errors = sorted(set(errors))
    ev_gate = complete_1x2 and complete_ah and complete_ou and synchronized and tradable and not errors
    return {
        "status": "通过" if ev_gate else "降级",
        "error_codes": errors,
        "observed_at_utc": observed_at.isoformat(),
        "source_count": len(sources) if isinstance(sources, list) else 0,
        "max_source_skew_seconds": max_skew_seconds,
        "complete_1x2": complete_1x2,
        "complete_asian_handicap": complete_ah,
        "complete_total_goals": complete_ou,
        "synchronized": synchronized,
        "tradable_prices": tradable,
        "ev_gate": ev_gate,
    }


def _lineup_assessment(evidence: Any, freeze_time, kickoff) -> dict[str, Any]:
    if not evidence:
        return {"status": "不可用", "error_codes": ["D01"], "evidence_status": "unavailable"}
    if not isinstance(evidence, dict):
        raise PlatformError("lineup_evidence must be an object or null")
    status = evidence.get("status")
    if status not in {"official", "probable", "unavailable"}:
        raise PlatformError("lineup_evidence.status must be official, probable or unavailable")
    errors: list[str] = []
    observed = None
    if evidence.get("observed_at_utc"):
        observed = parse_iso_datetime(evidence["observed_at_utc"], "lineup_evidence.observed_at_utc")
        if observed > freeze_time or observed >= kickoff:
            errors.append("D01")
    if status == "official" and not evidence.get("sources"):
        errors.append("D01")
    if errors:
        module_status = "不可用"
    elif status == "official":
        module_status = "通过"
    elif status == "probable":
        module_status = "部分通过"
    else:
        module_status = "不可用"
        errors.append("D01")
    return {
        "status": module_status,
        "error_codes": sorted(set(errors)),
        "evidence_status": status,
        "observed_at_utc": observed.isoformat() if observed else None,
        "source_count": len(evidence.get("sources", [])) if isinstance(evidence.get("sources"), list) else 0,
    }


def _find_team(snapshot: dict[str, Any], name: str) -> dict[str, Any] | None:
    token = normalize_team_token(name)
    exact = [team for team in snapshot.get("teams", []) if team.get("normalized_token") == token]
    if len(exact) == 1:
        return exact[0]
    return None


def _team_assessment(competition: dict[str, Any], home_team: str, away_team: str, kickoff) -> dict[str, Any]:
    path = TEAM_STRENGTH_ROOT / competition["competition_id"] / "latest.json"
    if not path.exists():
        return {
            "status": "不可用",
            "error_codes": ["D01"],
            "reason": f"Team feature snapshot missing: {path.relative_to(ROOT)}",
        }
    snapshot = load_json(path)
    home = _find_team(snapshot, home_team)
    away = _find_team(snapshot, away_team)
    errors = []
    if home is None or away is None:
        errors.append("D01")
    data_as_of = snapshot.get("data_as_of")
    current_status = competition.get("current_season_status", "")
    special_unavailable = (
        competition["competition_id"] == "JPN_J1"
        and kickoff.year >= 2026
        and "unavailable" in current_status
    )
    if special_unavailable:
        errors.append("D01")
    return {
        "status": "通过" if not errors else "降级",
        "error_codes": sorted(set(errors)),
        "data_as_of": data_as_of,
        "feature_status": snapshot.get("feature_status"),
        "home": home,
        "away": away,
        "special_current_competition_unavailable": special_unavailable,
        "snapshot_sha256": sha256_file(path),
    }


def prepare_match_context(match_input: dict[str, Any]) -> dict[str, Any]:
    registry = registry_map()
    competition_id = _require_nonempty_string(match_input.get("competition_id"), "competition_id")
    if competition_id not in registry:
        raise PlatformError(f"I01 unknown competition_id: {competition_id}")
    home_team = _require_nonempty_string(match_input.get("home_team"), "home_team")
    away_team = _require_nonempty_string(match_input.get("away_team"), "away_team")
    if normalize_team_token(home_team) == normalize_team_token(away_team):
        raise PlatformError("I02 home_team and away_team resolve to the same identity")
    kickoff = parse_iso_datetime(match_input.get("kickoff_utc"), "kickoff_utc")
    freeze_time = parse_iso_datetime(match_input.get("freeze_time_utc"), "freeze_time_utc")
    if freeze_time >= kickoff:
        raise PlatformError("I03 freeze_time_utc must be before kickoff_utc")
    if match_input.get("settlement") != "90_minutes_including_stoppage":
        raise PlatformError("I03 settlement must be 90_minutes_including_stoppage")
    if match_input.get("two_legged") and not match_input.get("first_leg_state"):
        raise PlatformError("I03 two_legged match requires first_leg_state")

    competition = registry[competition_id]
    market = _market_assessment(match_input.get("market_snapshot"), freeze_time, kickoff)
    lineup = _lineup_assessment(match_input.get("lineup_evidence"), freeze_time, kickoff)
    teams = _team_assessment(competition, home_team, away_team, kickoff)
    errors = sorted(set(market["error_codes"] + lineup["error_codes"] + teams.get("error_codes", [])))
    payload = {
        "schema_version": "1.0",
        "prepared_at_utc": utc_now(),
        "match_identity": {
            "competition_id": competition_id,
            "competition_name_zh": competition["name_zh"],
            "home_team": home_team,
            "away_team": away_team,
            "kickoff_utc": kickoff.isoformat(),
            "freeze_time_utc": freeze_time.isoformat(),
            "settlement": "90_minutes_including_stoppage",
            "competition_round": match_input.get("competition_round"),
            "venue": match_input.get("venue"),
            "neutral_venue": bool(match_input.get("neutral_venue", False)),
            "two_legged": bool(match_input.get("two_legged", False)),
            "first_leg_state": match_input.get("first_leg_state"),
        },
        "competition_registry": competition,
        "team_features": teams,
        "market_assessment": market,
        "lineup_assessment": lineup,
        "task_state": match_input.get("task_state"),
        "data_freshness_evidence": match_input.get("data_freshness_evidence"),
        "original_market_snapshot": match_input.get("market_snapshot"),
        "module_states": {
            "competition_identity_and_time": "通过",
            "competition_profile": "通过" if competition.get("profile_status", "").startswith("available") else "降级",
            "team_dynamic_features": teams["status"],
            "synchronized_market": market["status"],
            "lineup_and_task": lineup["status"],
            "direct_total_goals": "未启用",
            "conditional_goal_difference": "未启用",
            "unified_score_matrix": "未启用",
            "market_coordination": "未启用",
            "price_ev_no_bet": "未启用" if market["ev_gate"] else "降级",
        },
        "gates": {
            "formal_calculation_may_start": teams["status"] != "不可用",
            "ev_may_be_calculated": market["ev_gate"],
            "exact_score_may_be_published": False,
            "new_freeze_required_on_official_lineup_or_major_market_move": lineup["status"] != "通过",
        },
        "error_codes": errors,
        "hard_limitations": [
            "GitHub team features are descriptive only and cannot independently create formal probabilities.",
            "Formal total-goal and conditional goal-difference tracks must be executed under the active CURRENT rule.",
            "Exact score remains unavailable until one audited joint score matrix passes all marginal and convergence checks.",
        ],
    }
    hash_payload = dict(payload)
    hash_payload.pop("prepared_at_utc", None)
    payload["context_hash"] = sha256_json(hash_payload)
    return payload


def _require_module_state(states: dict[str, Any], key: str) -> str:
    state = states.get(key)
    if state not in VALID_STATES:
        raise PlatformError(f"invalid or missing module state {key}: {state!r}")
    return state


def _derive_line_market(matrix: Any, line: float, settlement_fn) -> dict[str, float]:
    totals = {"win": 0.0, "push": 0.0, "loss": 0.0}
    from platform_core import score_matrix_rows
    for home, away, probability in score_matrix_rows(matrix):
        outcome = settlement_fn(home, away, line)
        for key in totals:
            totals[key] += outcome[key] * probability
    return totals


def validate_calculation_output(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if calculation.get("freeze_context_hash") != context.get("context_hash"):
        errors.append("C01 context hash mismatch")
    states = calculation.get("module_states")
    if not isinstance(states, dict):
        raise PlatformError("calculation_output.module_states must be an object")
    direct_state = _require_module_state(states, "direct_total_goals")
    goal_diff_state = _require_module_state(states, "conditional_goal_difference")
    matrix_state = _require_module_state(states, "unified_score_matrix")
    market_coordination_state = _require_module_state(states, "market_coordination")
    oof_state = states.get("oof_matrix_calibration")
    if oof_state is not None:
        oof_state = _require_module_state(states, "oof_matrix_calibration")

    probabilities = calculation.get("probabilities") or {}
    one_x_two = None
    total_goals = None
    score_matrix = probabilities.get("score_matrix")
    marginals = None
    if probabilities.get("one_x_two") is not None:
        try:
            one_x_two = validate_probability_vector(
                probabilities["one_x_two"], ("home", "draw", "away"), field="probabilities.one_x_two"
            )
        except PlatformError as exc:
            errors.append(f"U01 {exc}")
    if probabilities.get("total_goals") is not None:
        try:
            total_goals = validate_probability_vector(
                probabilities["total_goals"], ("0", "1", "2", "3", "4", "5", "6", "7+"), field="probabilities.total_goals"
            )
        except PlatformError as exc:
            errors.append(f"U01 {exc}")

    matrix_audit: dict[str, Any] = {"available": score_matrix is not None, "passed": False}
    if score_matrix is not None:
        try:
            marginals = derive_score_marginals(score_matrix)
            sum_residual = marginals["probability_sum"] - 1.0
            matrix_audit["probability_sum"] = marginals["probability_sum"]
            matrix_audit["probability_sum_residual"] = sum_residual
            if abs(sum_residual) > 1e-6:
                errors.append(f"U01 score matrix probabilities sum to {marginals['probability_sum']:.12f}")
            if one_x_two is not None:
                comparison = compare_marginals(marginals["1x2"], one_x_two)
                matrix_audit["one_x_two_marginal"] = comparison
                if not comparison["passed"]:
                    errors.append("U01 score matrix 1X2 marginal mismatch")
            if total_goals is not None:
                comparison = compare_marginals(marginals["total_goals"], total_goals)
                matrix_audit["total_goals_marginal"] = comparison
                if not comparison["passed"]:
                    errors.append("U01 score matrix total-goals marginal mismatch")
            if probabilities.get("btts_yes") is not None:
                btts = normalize_probability(probabilities["btts_yes"], "probabilities.btts_yes")
                residual = marginals["btts_yes"] - btts
                matrix_audit["btts_residual"] = residual
                if abs(residual) > MARGINAL_TOLERANCE:
                    errors.append("U01 score matrix BTTS marginal mismatch")

            derived = calculation.get("derived_markets") or {}
            if derived.get("home_handicap"):
                item = derived["home_handicap"]
                line = float(item["line"])
                observed = _derive_line_market(score_matrix, line, settle_home_handicap)
                expected = {key: float(item[key]) for key in ("win", "push", "loss")}
                comparison = compare_marginals(observed, expected)
                matrix_audit["home_handicap_settlement"] = {"line": line, **comparison}
                if not comparison["passed"]:
                    errors.append("U01 Asian handicap settlement mismatch")
            if derived.get("over_total"):
                item = derived["over_total"]
                line = float(item["line"])
                observed = _derive_line_market(score_matrix, line, settle_over_total)
                expected = {key: float(item[key]) for key in ("win", "push", "loss")}
                comparison = compare_marginals(observed, expected)
                matrix_audit["over_total_settlement"] = {"line": line, **comparison}
                if not comparison["passed"]:
                    errors.append("U01 total-goals settlement mismatch")

            ranking = top_scores(score_matrix, 5)
            matrix_audit["top_scores"] = ranking
            conclusions = calculation.get("conclusions") or {}
            textual_top = conclusions.get("top_score")
            if textual_top and ranking and textual_top != ranking[0]["score"]:
                errors.append("U01 textual top score does not match matrix Top-1")
            matrix_audit["top1_top2_gap"] = (
                ranking[0]["probability"] - ranking[1]["probability"] if len(ranking) > 1 else ranking[0]["probability"]
            )
            matrix_audit["top3_cumulative"] = sum(item["probability"] for item in ranking[:3])
            matrix_audit["passed"] = not any(error.startswith("U01") for error in errors)
        except (PlatformError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"U01 {exc}")

    conclusions = calculation.get("conclusions") or {}
    total_text = conclusions.get("total_goals_text")
    score_text = conclusions.get("score_text")
    total_available = direct_state == "通过" and total_goals is not None
    exact_available = (
        direct_state == "通过"
        and goal_diff_state == "通过"
        and matrix_state == "通过"
        and score_matrix is not None
        and matrix_audit.get("passed")
    )
    if not total_available and total_text != FIXED_TOTAL_UNAVAILABLE:
        errors.append(f"C01 total-goals unavailable text must be exactly: {FIXED_TOTAL_UNAVAILABLE}")
    if not exact_available and score_text != FIXED_SCORE_UNAVAILABLE:
        errors.append(f"C01 score unavailable text must be exactly: {FIXED_SCORE_UNAVAILABLE}")
    if exact_available and conclusions.get("top_score") is None:
        errors.append("C01 exact score available but conclusions.top_score is missing")

    optimization = calculation.get("optimization_audit")
    optimization_audit = {"required": market_coordination_state == "通过", "passed": True}
    if market_coordination_state == "通过":
        required = (
            "prior_description",
            "market_constraints",
            "objective",
            "constraint_form",
            "converged",
            "iterations",
            "termination_reason",
            "max_constraint_residual",
            "probability_sum",
        )
        if not isinstance(optimization, dict) or any(key not in optimization for key in required):
            errors.append("C01 market coordination marked through without a complete optimization audit")
            optimization_audit["passed"] = False
        else:
            if optimization.get("converged") is not True:
                errors.append("C01 optimization did not converge")
                optimization_audit["passed"] = False
            if float(optimization.get("max_constraint_residual", 1.0)) > 1e-5:
                errors.append("C01 market-constraint residual exceeds tolerance")
                optimization_audit["passed"] = False
            if abs(float(optimization.get("probability_sum", 0.0)) - 1.0) > 1e-6:
                errors.append("C01 optimization probability sum is not one")
                optimization_audit["passed"] = False

    calibration_validation = {"required": oof_state == "通过", "passed": True}
    if oof_state == "通过":
        calibration_audit = calculation.get("calibration_audit")
        required = (
            "status", "artifact_sha256", "calibration_code_sha256", "engine_sha256",
            "source_report_hash_verified", "calibration_report_hash_verified",
            "target_season", "training_max_date", "probability_sum_residual",
        )
        if not isinstance(calibration_audit, dict) or any(key not in calibration_audit for key in required):
            errors.append("C01 OOF calibration marked through without a complete audit")
            calibration_validation["passed"] = False
        else:
            if calibration_audit.get("status") != "通过":
                errors.append("C01 OOF calibration audit status is not through")
                calibration_validation["passed"] = False
            if calibration_audit.get("source_report_hash_verified") is not True or calibration_audit.get("calibration_report_hash_verified") is not True:
                errors.append("C01 OOF calibration artifact freshness verification failed")
                calibration_validation["passed"] = False
            if abs(float(calibration_audit.get("probability_sum_residual", 1.0))) > 1e-10:
                errors.append("C01 OOF calibrated probability sum residual exceeds tolerance")
                calibration_validation["passed"] = False

    price_checks = []
    for index, item in enumerate(calculation.get("price_analysis") or []):
        try:
            p = normalize_probability(item["model_probability"], f"price_analysis[{index}].model_probability")
            odds = float(item["decimal_odds"])
            computed_ev = expected_value(p, odds)
            supplied_ev = float(item["ev"])
            residual = computed_ev - supplied_ev
            passed = abs(residual) <= 1e-6
            if not passed:
                errors.append(f"C01 price_analysis[{index}] EV mismatch")
            if not context.get("gates", {}).get("ev_may_be_calculated"):
                errors.append("M01 EV supplied without a complete tradable synchronized market")
            price_checks.append({"index": index, "computed_ev": computed_ev, "supplied_ev": supplied_ev, "residual": residual, "passed": passed})
        except (KeyError, TypeError, ValueError, PlatformError) as exc:
            errors.append(f"C01 invalid price_analysis[{index}]: {exc}")

    errors = sorted(set(errors))
    status = "通过" if not errors else "失败"
    report = {
        "schema_version": "1.0",
        "validated_at_utc": utc_now(),
        "status": status,
        "context_hash": context.get("context_hash"),
        "calculation_sha256": sha256_json(calculation),
        "errors": errors,
        "warnings": warnings,
        "module_states": states,
        "gates": {
            "result_probabilities_available": one_x_two is not None and not any("one_x_two" in error for error in errors),
            "total_goals_available": total_available and status == "通过",
            "exact_score_available": exact_available and status == "通过",
            "ev_available": context.get("gates", {}).get("ev_may_be_calculated", False) and status == "通过",
        },
        "matrix_audit": matrix_audit,
        "optimization_audit": optimization_audit,
        "oof_calibration_audit": calibration_validation,
        "price_checks": price_checks,
    }
    return report


def _safe_freeze_id(context: dict[str, Any]) -> str:
    identity = context["match_identity"]
    kickoff = parse_iso_datetime(identity["kickoff_utc"], "kickoff_utc")
    base = "_".join(
        [
            identity["competition_id"],
            kickoff.strftime("%Y%m%dT%H%MZ"),
            normalize_team_token(identity["home_team"])[:24] or "home",
            normalize_team_token(identity["away_team"])[:24] or "away",
            context["context_hash"][:10],
        ]
    )
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", base)


def freeze_prediction(
    context: dict[str, Any],
    calculation: dict[str, Any],
    validation: dict[str, Any],
    output_root: Path = FREEZE_ROOT,
) -> tuple[Path, dict[str, Any]]:
    if validation.get("status") != "通过":
        raise PlatformError("prediction cannot be frozen because validation status is not 通过")
    if validation.get("context_hash") != context.get("context_hash"):
        raise PlatformError("validation context hash mismatch")
    if validation.get("calculation_sha256") != sha256_json(calculation):
        raise PlatformError("validation calculation hash mismatch")
    identity = context["match_identity"]
    freeze_id = _safe_freeze_id(context)
    year = parse_iso_datetime(identity["kickoff_utc"], "kickoff_utc").year
    path = output_root / str(year) / identity["competition_id"] / f"{freeze_id}.json"
    if path.exists():
        raise PlatformError(f"immutable prediction freeze already exists: {path}")
    payload_without_hashes = {
        "schema_version": "1.0",
        "freeze_id": freeze_id,
        "created_at_utc": utc_now(),
        "repository_commit": os.environ.get("GITHUB_SHA"),
        "match_context": context,
        "calculation_output": calculation,
        "validation_report": validation,
    }
    hashes = {
        "match_context_sha256": sha256_json(context),
        "calculation_output_sha256": sha256_json(calculation),
        "validation_report_sha256": sha256_json(validation),
        "freeze_payload_sha256": sha256_json(payload_without_hashes),
    }
    payload = {**payload_without_hashes, "hashes": hashes}
    atomic_write_json(path, payload)
    return path, payload


def audit_prediction(freeze: dict[str, Any], result: dict[str, Any], output_root: Path = AUDIT_ROOT) -> tuple[Path, dict[str, Any]]:
    hashes = freeze.get("hashes") or {}
    payload_without_hashes = {key: value for key, value in freeze.items() if key != "hashes"}
    integrity = {
        "match_context": hashes.get("match_context_sha256") == sha256_json(freeze.get("match_context")),
        "calculation_output": hashes.get("calculation_output_sha256") == sha256_json(freeze.get("calculation_output")),
        "validation_report": hashes.get("validation_report_sha256") == sha256_json(freeze.get("validation_report")),
        "freeze_payload": hashes.get("freeze_payload_sha256") == sha256_json(payload_without_hashes),
    }
    if not all(integrity.values()):
        raise PlatformError(f"freeze payload hash integrity failed before postmatch audit: {integrity}")
    try:
        home_goals = int(result["home_goals"])
        away_goals = int(result["away_goals"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PlatformError("result must contain integer home_goals and away_goals") from exc
    if home_goals < 0 or away_goals < 0:
        raise PlatformError("result goals cannot be negative")
    freeze_id = freeze.get("freeze_id")
    if not freeze_id:
        raise PlatformError("freeze_id missing")
    calculation = freeze["calculation_output"]
    probabilities = calculation.get("probabilities") or {}
    actual_outcome = "home" if home_goals > away_goals else "draw" if home_goals == away_goals else "away"
    scores: dict[str, Any] = {}

    if probabilities.get("one_x_two"):
        one_x_two = validate_probability_vector(probabilities["one_x_two"], ("home", "draw", "away"), field="one_x_two")
        order = ["home", "draw", "away"]
        actual_index = order.index(actual_outcome)
        scores["one_x_two"] = {
            "actual": actual_outcome,
            "actual_probability": one_x_two[actual_outcome],
            "log_score": log_score(one_x_two[actual_outcome]),
            "brier_score": multiclass_brier(one_x_two, actual_outcome),
            "rps": ranked_probability_score([one_x_two[key] for key in order], actual_index),
            "top1_hit": max(one_x_two, key=one_x_two.get) == actual_outcome,
        }

    actual_total = home_goals + away_goals
    total_key = str(actual_total) if actual_total <= 6 else "7+"
    if probabilities.get("total_goals"):
        keys = ("0", "1", "2", "3", "4", "5", "6", "7+")
        totals = validate_probability_vector(probabilities["total_goals"], keys, field="total_goals")
        scores["total_goals"] = {
            "actual": total_key,
            "actual_probability": totals[total_key],
            "log_score": log_score(totals[total_key]),
            "rps": ranked_probability_score([totals[key] for key in keys], list(keys).index(total_key)),
            "top1_hit": max(totals, key=totals.get) == total_key,
            "top2_hit": total_key in sorted(totals, key=totals.get, reverse=True)[:2],
        }

    if probabilities.get("score_matrix"):
        marginals = derive_score_marginals(probabilities["score_matrix"])
        score_key = f"{home_goals}-{away_goals}"
        score_probability = marginals["score_probabilities"].get(score_key, 0.0)
        ranking = top_scores(probabilities["score_matrix"], 5)
        ranked_scores = [item["score"] for item in ranking]
        scores["exact_score"] = {
            "actual": score_key,
            "actual_probability": score_probability,
            "log_score": log_score(score_probability),
            "top1_hit": score_key in ranked_scores[:1],
            "top3_hit": score_key in ranked_scores[:3],
            "top5_hit": score_key in ranked_scores[:5],
            "predicted_top5": ranking,
        }

    identity = freeze["match_context"]["match_identity"]
    year = parse_iso_datetime(identity["kickoff_utc"], "kickoff_utc").year
    audit_id = f"audit_{freeze_id}"
    path = output_root / str(year) / identity["competition_id"] / f"{audit_id}.json"
    if path.exists():
        raise PlatformError(f"immutable postmatch audit already exists: {path}")
    source_freeze_hash = sha256_json({key: value for key, value in freeze.items() if key != "hashes"})
    payload = {
        "schema_version": "1.0",
        "audit_id": audit_id,
        "freeze_id": freeze_id,
        "audited_at_utc": utc_now(),
        "source_freeze_sha256": source_freeze_hash,
        "result": {
            "home_goals": home_goals,
            "away_goals": away_goals,
            "completed_at_utc": result.get("completed_at_utc"),
            "source": result.get("source"),
        },
        "scores": scores,
        "discipline": {
            "pre_match_probabilities_modified": False,
            "post_match_information_backfilled": False,
        },
    }
    atomic_write_json(path, payload)
    return path, payload


def _load_object(path: str) -> dict[str, Any]:
    value = load_json(Path(path))
    if not isinstance(value, dict):
        raise PlatformError(f"JSON file must contain an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--input", required=True)
    prepare.add_argument("--output", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--context", required=True)
    validate.add_argument("--calculation", required=True)
    validate.add_argument("--output", required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--context", required=True)
    freeze.add_argument("--calculation", required=True)
    freeze.add_argument("--validation", required=True)
    freeze.add_argument("--output-root")

    audit = subparsers.add_parser("audit")
    audit.add_argument("--freeze", required=True)
    audit.add_argument("--result", required=True)
    audit.add_argument("--output-root")

    args = parser.parse_args()
    try:
        if args.command == "prepare":
            output = prepare_match_context(_load_object(args.input))
            atomic_write_json(Path(args.output), output)
            print(json.dumps({"status": "ok", "context_hash": output["context_hash"], "output": args.output}, ensure_ascii=False))
        elif args.command == "validate":
            report = validate_calculation_output(_load_object(args.context), _load_object(args.calculation))
            atomic_write_json(Path(args.output), report)
            print(json.dumps({"status": report["status"], "output": args.output}, ensure_ascii=False))
            return 0 if report["status"] == "通过" else 2
        elif args.command == "freeze":
            root = Path(args.output_root) if args.output_root else FREEZE_ROOT
            path, _ = freeze_prediction(
                _load_object(args.context), _load_object(args.calculation), _load_object(args.validation), root
            )
            print(json.dumps({"status": "ok", "freeze": str(path)}, ensure_ascii=False))
        elif args.command == "audit":
            root = Path(args.output_root) if args.output_root else AUDIT_ROOT
            path, _ = audit_prediction(_load_object(args.freeze), _load_object(args.result), root)
            print(json.dumps({"status": "ok", "audit": str(path)}, ensure_ascii=False))
    except PlatformError as exc:
        print(f"ERROR: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
