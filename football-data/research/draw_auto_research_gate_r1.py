#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

DEFAULT_GATE = {
    "schema_version": "DRAW-CHALLENGER-GATE-R1.4",
    "minimum_delta_draw_f1": 0.01,
    "minimum_delta_macro_f1": 0.0,
    "minimum_delta_accuracy": -0.005,
    "maximum_delta_log_loss": 0.0,
    "maximum_delta_brier": 0.0,
    "maximum_delta_rps": 0.0,
    "maximum_delta_draw_ece": 0.005,
    "minimum_leagues_nonnegative_draw_f1": 10,
    "minimum_leagues_nonnegative_rps": 10,
    "maximum_worst_league_draw_f1_drop": 0.05,
    "required_outer_folds": 51,
    "require_unique_prediction_fingerprint": True,
}


def evaluate_challenger_gate(result: dict[str, Any], gate: dict[str, Any] | None = None) -> dict[str, Any]:
    g = gate or DEFAULT_GATE
    delta = result.get("pooled_delta") or {}
    leagues = result.get("league_results") or {}
    gates = result.get("safety_gates") or {}
    league_draw = [float(item["delta"]["Draw F1"]) for item in leagues.values()]
    league_rps = [float(item["delta"]["RPS"]) for item in leagues.values()]
    checks = {
        "fold_completeness": result.get("fold_count") == int(g["required_outer_folds"]),
        "numerical_safety": bool(gates.get("all_fits_converged")) and bool(gates.get("probability_gates_pass")),
        "no_preprocessing_leakage": gates.get("evaluation_rows_used_for_preprocessing_decisions") == 0,
        "unique_prediction_fingerprint": bool(result.get("prediction_fingerprint_unique", False)) if g.get("require_unique_prediction_fingerprint") else True,
        "draw_f1_improvement": float(delta.get("Draw F1", -999)) >= float(g["minimum_delta_draw_f1"]),
        "macro_f1_noninferiority": float(delta.get("Macro-F1", -999)) >= float(g["minimum_delta_macro_f1"]),
        "accuracy_noninferiority": float(delta.get("Accuracy", -999)) >= float(g["minimum_delta_accuracy"]),
        "log_loss_quality": float(delta.get("Log Loss", 999)) <= float(g["maximum_delta_log_loss"]),
        "brier_quality": float(delta.get("Brier", 999)) <= float(g["maximum_delta_brier"]),
        "rps_quality": float(delta.get("RPS", 999)) <= float(g["maximum_delta_rps"]),
        "draw_ece_quality": float(delta.get("Draw ECE", 999)) <= float(g["maximum_delta_draw_ece"]),
        "league_draw_stability": sum(value >= 0.0 for value in league_draw) >= int(g["minimum_leagues_nonnegative_draw_f1"]),
        "league_rps_stability": sum(value <= 0.0 for value in league_rps) >= int(g["minimum_leagues_nonnegative_rps"]),
        "worst_league_draw_control": bool(league_draw) and min(league_draw) >= -float(g["maximum_worst_league_draw_f1_drop"]),
    }
    return {
        "schema_version": g["schema_version"],
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "thresholds": g,
        "league_nonnegative_draw_f1": sum(value >= 0.0 for value in league_draw),
        "league_nonnegative_rps": sum(value <= 0.0 for value in league_rps),
        "worst_league_draw_f1_delta": min(league_draw) if league_draw else None,
    }
