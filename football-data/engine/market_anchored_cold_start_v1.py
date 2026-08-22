#!/usr/bin/env python3
"""Question-time market anchor for coverage-only cold-start predictions.

The module reuses the existing synchronized-market assessment and non-redundant
KL projection. It may change only a cold-start candidate matrix. It never mutates
the hash-bound formal V460 centre and never permits EV against the same prices
used to build the prediction.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

import market_coordination_runtime_v470 as market_runtime
from football_v460_engine import conditional_goal_difference_by_total, minimum_score_set
from market_coordination_runtime_basis_v470 import _market_constraints_nonredundant
from match_pipeline import _market_assessment
from platform_core import (
    PlatformError,
    derive_score_marginals,
    parse_iso_datetime,
    sha256_json,
    top_scores,
)

MARKET_ANCHORED_COLD_START = "MARKET_ANCHORED_COLD_START"
TEMPORAL_OR_SOURCE_INTEGRITY_ERRORS = {"M03", "M04"}


def _replace_matrix(output: dict[str, Any], matrix: list[dict[str, Any]]) -> None:
    marginals = derive_score_marginals(matrix)
    output.setdefault("probabilities", {})["score_matrix"] = matrix
    output["probabilities"]["one_x_two"] = marginals["1x2"]
    output["probabilities"]["total_goals"] = marginals["total_goals"]
    output["probabilities"]["btts_yes"] = marginals["btts_yes"]
    output["top_scores"] = top_scores(matrix, 10)
    output["conditional_goal_difference"] = conditional_goal_difference_by_total(matrix)
    output["score_sets"] = {
        "80": minimum_score_set(matrix, 0.80),
        "90": minimum_score_set(matrix, 0.90),
    }


def apply_market_anchor(
    calculation: dict[str, Any],
    request: dict[str, Any],
    cutoff: datetime,
    market_config: dict[str, Any],
) -> dict[str, Any]:
    snapshot = request.get("market_snapshot")
    if snapshot is None:
        return calculation
    if not isinstance(snapshot, dict):
        raise PlatformError("market_snapshot must be an object or null")
    freeze = parse_iso_datetime(request.get("freeze_time_utc"), "freeze_time_utc")
    kickoff = parse_iso_datetime(request.get("kickoff_utc"), "kickoff_utc")
    if freeze != cutoff:
        raise PlatformError("market cold-start freeze_time_utc must equal cutoff_utc")
    if kickoff <= freeze:
        raise PlatformError("kickoff_utc must be strictly after prediction freeze")

    assessment = _market_assessment(snapshot, freeze, kickoff)
    error_codes = set(assessment.get("error_codes") or [])
    integrity_errors = sorted(error_codes & TEMPORAL_OR_SOURCE_INTEGRITY_ERRORS)
    if integrity_errors:
        raise PlatformError(f"market cold-start PIT/source integrity failure: {integrity_errors}")

    if assessment.get("status") != "通过":
        output = copy.deepcopy(calculation)
        output["market_anchor_audit"] = {
            "status": "不可用",
            "reason": assessment.get("reason") or "complete synchronized 1X2/AH/OU snapshot unavailable",
            "error_codes": assessment.get("error_codes"),
            "formal_weight": 0.0,
            "probability_mutation": False,
            "fallback_state_retained": output.get("cold_start_candidate", {}).get("state"),
        }
        return output

    output = copy.deepcopy(calculation)
    matrix = output.get("probabilities", {}).get("score_matrix")
    if not isinstance(matrix, list) or not matrix:
        raise PlatformError("market cold-start requires a non-empty baseline score matrix")
    prior = [float(cell["probability"]) for cell in matrix]
    prior_sum = sum(prior)
    if abs(prior_sum - 1.0) > 1e-8:
        raise PlatformError("market cold-start baseline matrix is not probability-conserving")

    features, targets, constraints = _market_constraints_nonredundant(snapshot, matrix)
    probabilities, solver = market_runtime._kl_project(prior, features, targets)
    if (
        not solver.get("converged")
        or float(solver.get("max_constraint_residual", 1.0)) > market_runtime.TOLERANCE
    ):
        raise PlatformError(f"market cold-start KL projection failed: {solver}")
    coordinated = [
        {**cell, "probability": probability}
        for cell, probability in zip(matrix, probabilities)
    ]
    probability_sum = sum(probabilities)
    if abs(probability_sum - 1.0) > 1e-8:
        raise PlatformError("market cold-start projected matrix is not probability-conserving")

    prior_one_x_two = derive_score_marginals(matrix)["1x2"]
    _replace_matrix(output, coordinated)
    anchored_one_x_two = output["probabilities"]["one_x_two"]
    one = snapshot["one_x_two"]
    market_fair = market_runtime._three_way_no_vig(
        market_runtime._valid_odds(one.get("home")),
        market_runtime._valid_odds(one.get("draw")),
        market_runtime._valid_odds(one.get("away")),
    )
    cold = output.setdefault("cold_start_candidate", {})
    cold.update({
        "state": MARKET_ANCHORED_COLD_START,
        "confidence": market_config["confidence"],
        "coverage_only": True,
        "team_strength_evidence": False,
        "market_strength_anchor": True,
        "formal_weight": market_config["formal_weight"],
        "exact_gate": market_config["exact_gate"],
        "ev_decision": market_config["ev_decision"],
        "same_market_ev_allowed": market_config["same_market_ev_allowed"],
        "local_default_activation": market_config["local_default_activation"],
        "production_activation": market_config["production_activation"],
    })
    output["market_anchor_audit"] = {
        "status": "通过",
        "snapshot_sha256": sha256_json(snapshot),
        "assessment": assessment,
        "prior_matrix_sha256": sha256_json(matrix),
        "anchored_matrix_sha256": sha256_json(coordinated),
        "prior_one_x_two": prior_one_x_two,
        "de_vig_market_one_x_two": market_fair,
        "anchored_one_x_two": anchored_one_x_two,
        "constraints": constraints,
        "solver": solver,
        "probability_sum": probability_sum,
        "objective": market_config["projection"],
        "constraint_basis": market_config["constraint_basis"],
        "formal_weight": 0.0,
        "formal_applied": False,
        "same_market_ev_allowed": False,
        "ev_decision": "No Bet",
        "policy": "Question-time prices shape a coverage-only cold-start matrix; the same prices cannot establish EV.",
    }
    return output
