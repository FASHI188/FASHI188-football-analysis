#!/usr/bin/env python3
"""Actionable question-time runner for routine pre-match decisions.

The user's question time is the decision freeze. A probable-lineup route remains
valid for the primary answer; publication of official lineups does not by itself
make the already-frozen answer unavailable. Major confirmed changes remain an
invalidation condition and may trigger a new run when requested.

Competition-specific promoted challengers are receipt-gated. The validated
ESP/NED dynamic-strength route is ordered before OOF matrix calibration; the
existing MLS conditional-allocation promotion remains ordered after OOF. A genuine
market-coordination KL candidate is then evaluated on the final model matrix using
a non-redundant three-market constraint basis. It may run with a complete
synchronized market snapshot, but it cannot mutate the formal centre without a
competition/season LOMO promotion receipt. Read-only total-goals diagnostics run
on the final matrix. The V5 selective-direction gate runs last and may only allow
or abstain from the final 1X2 Top-1 direction; it never changes probabilities.

For a new target season, this wrapper may inject separately validated, hash-bound
next-season hyperparameters and an OOF calibrator while leaving the frozen formal
engine and calibration-module files unchanged. These bridges never roll forward
team strength and cannot bypass the same-season competition/team sample gates.
"""
from __future__ import annotations

import football_v460_engine as engine_module
import oof_matrix_calibration as calibration_module
import run_formal_prediction_live as live_runner
import run_formal_prediction_v460 as base_runner
from dynamic_strength_live_input_contract_v470 import apply_dynamic_strength_live_input_audit
from dynamic_strength_pre_oof_runtime_v470 import apply_promoted_dynamic_strength_pre_oof
from formal_ev_lomo_gate_v470 import apply_formal_ev_lomo_gate
from formal_governance_runtime_v470 import apply_formal_governance_runtime
from formal_next_season_parameter_runtime_v470 import (
    audit_rollforward_parameters,
    select_rollforward_parameters,
)
from market_coordination_runtime_basis_v470 import apply_market_coordination_runtime
from oof_next_season_runtime_v470 import load_rollforward_calibrator
from platform_core import PlatformError
from probable_lineup_runtime_v470 import apply_probable_lineup_runtime
from promoted_challenger_runtime_gate_v470 import apply_hash_bound_promoted_v470_challengers
from selective_direction_gate_v500 import apply_selective_direction_gate
from total_goals_peak_diagnostics_v470 import apply_total_goals_peak_diagnostics


def main() -> int:
    original_prepare = base_runner.prepare_match_context
    original_calculation = base_runner.calculation_from_context
    original_calibration = base_runner.apply_oof_matrix_calibration
    original_parameter_selector = engine_module._select_point_in_time_parameters
    original_calibrator_loader = calibration_module.load_oof_matrix_calibrator

    def parameter_selector_with_rollforward(artifact, target_season):
        try:
            return original_parameter_selector(artifact, target_season)
        except PlatformError:
            return select_rollforward_parameters(artifact, target_season)

    def actionable_prepare(match_input):
        context = original_prepare(match_input)
        if str(match_input.get("season") or "").strip():
            context.setdefault("match_identity", {})["season"] = str(match_input["season"]).strip()
        context = apply_formal_ev_lomo_gate(context)
        context = apply_probable_lineup_runtime(match_input, context)
        context = apply_dynamic_strength_live_input_audit(
            context, match_input.get("dynamic_strength_evidence")
        )

        identity = context.get("match_identity") or {}
        competition_id = str(identity.get("competition_id") or "")
        target_season = str(identity.get("season") or "")
        parameter_rollforward_audit = audit_rollforward_parameters(competition_id, target_season)
        context["formal_next_season_parameter_rollforward_audit"] = parameter_rollforward_audit

        lineup_audit = context.get("probable_lineup_v470_audit") or {}
        lineup_runtime_status = str(lineup_audit.get("status") or "不可用")
        context.setdefault("lineup_assessment", {})["status"] = (
            lineup_runtime_status if lineup_runtime_status in {"通过", "部分通过"} else "不可用"
        )
        lineup_detail_available = bool((context.get("lineup_projection") or {}).get("starting_xi"))

        context.setdefault("gates", {})["new_freeze_required_on_official_lineup_or_major_market_move"] = False
        context["gates"]["question_time_decision_freeze_locked"] = True
        context["gates"]["probable_lineup_allowed"] = lineup_detail_available
        context["gates"]["dynamic_strength_live_input_passed"] = (
            (context.get("dynamic_strength_live_input_audit") or {}).get("status") == "通过"
        )
        context["gates"]["dynamic_strength_probability_effect_enabled"] = False
        context["gates"]["dynamic_strength_pre_oof_runtime_wired"] = True
        context["gates"]["next_season_parameter_rollforward_available"] = (
            parameter_rollforward_audit.get("status") == "通过"
        )
        context["gates"]["refreeze_policy"] = (
            "Do not wait for official lineups or closing odds. Re-run only on user request or a major confirmed "
            "change before the user's action deadline."
        )
        return context

    def champion_then_dynamic_strength(context):
        calculation = original_calculation(context)
        transformed = apply_promoted_dynamic_strength_pre_oof(context, calculation)
        audit = transformed.get("dynamic_strength_pre_oof_audit") or {}
        context.setdefault("gates", {})["dynamic_strength_probability_effect_enabled"] = (
            audit.get("status") == "通过" and float(audit.get("formal_weight", 0.0)) > 0.0
        )
        return transformed

    def calibrated_then_promoted(context, calculation):
        identity = context.get("match_identity") or {}
        competition_id = str(identity.get("competition_id") or "")
        target_season = str(identity.get("season") or "")
        freeze_time_utc = str(identity.get("freeze_time_utc") or "")

        parameter_audit = context.get("formal_next_season_parameter_rollforward_audit") or {}
        if parameter_audit.get("status") == "通过":
            calculation.setdefault("model_audit", {})["parameter_source"] = (
                f"hash_bound_next_season_rollforward:{target_season}"
            )
            calculation["formal_next_season_parameter_rollforward_audit"] = parameter_audit

        canonical = original_calibrator_loader(competition_id) if competition_id else None
        canonical_has_target = False
        if canonical is not None:
            _, canonical_artifact = canonical
            season_map = canonical_artifact.get("season_calibrators")
            canonical_has_target = isinstance(season_map, dict) and isinstance(season_map.get(target_season), dict)

        oof_rollforward_audit = {
            "status": "不适用" if canonical_has_target else "不可用",
            "competition_id": competition_id,
            "target_season": target_season,
            "probability_mutation": False,
            "reason": "canonical target-season calibrator available" if canonical_has_target else "rollforward not used",
        }

        if canonical_has_target:
            calibrated = original_calibration(context, calculation)
        else:
            try:
                rollforward_path, augmented_artifact, oof_rollforward_audit = load_rollforward_calibrator(
                    competition_id,
                    target_season,
                    freeze_time_utc,
                )

                def temporary_loader(requested_competition_id):
                    if requested_competition_id == competition_id:
                        return rollforward_path, augmented_artifact
                    return original_calibrator_loader(requested_competition_id)

                calibration_module.load_oof_matrix_calibrator = temporary_loader
                try:
                    calibrated = original_calibration(context, calculation)
                finally:
                    calibration_module.load_oof_matrix_calibrator = original_calibrator_loader
            except PlatformError as exc:
                oof_rollforward_audit = {
                    "status": "不可用",
                    "competition_id": competition_id,
                    "target_season": target_season,
                    "probability_mutation": False,
                    "reason": str(exc),
                }
                calibrated = original_calibration(context, calculation)

        calibrated["oof_next_season_rollforward_audit"] = oof_rollforward_audit
        promoted = apply_hash_bound_promoted_v470_challengers(context, calibrated)
        coordinated = apply_market_coordination_runtime(context, promoted)
        diagnosed = apply_total_goals_peak_diagnostics(coordinated)
        governed = apply_formal_governance_runtime(diagnosed)
        return apply_selective_direction_gate(context, governed)

    base_runner.prepare_match_context = actionable_prepare
    base_runner.calculation_from_context = champion_then_dynamic_strength
    base_runner.apply_oof_matrix_calibration = calibrated_then_promoted
    engine_module._select_point_in_time_parameters = parameter_selector_with_rollforward
    try:
        return live_runner.main()
    finally:
        base_runner.prepare_match_context = original_prepare
        base_runner.calculation_from_context = original_calculation
        base_runner.apply_oof_matrix_calibration = original_calibration
        engine_module._select_point_in_time_parameters = original_parameter_selector
        calibration_module.load_oof_matrix_calibrator = original_calibrator_loader


if __name__ == "__main__":
    raise SystemExit(main())
