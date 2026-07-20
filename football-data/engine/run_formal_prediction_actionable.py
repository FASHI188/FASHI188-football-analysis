#!/usr/bin/env python3
"""Actionable question-time runner for routine pre-match decisions.

The user's question time is the decision freeze. A probable-lineup route remains
valid for the primary answer; publication of official lineups does not by itself
make the already-frozen answer unavailable. Major confirmed changes remain an
invalidation condition and may trigger a new run when requested.

V4.7 competition-specific promoted challengers are applied only through the
hash-bound runtime activation gate after the existing OOF matrix calibration.
"""
from __future__ import annotations

import run_formal_prediction_live as live_runner
import run_formal_prediction_v460 as base_runner
from promoted_challenger_runtime_gate_v470 import apply_hash_bound_promoted_v470_challengers


def main() -> int:
    original_prepare = base_runner.prepare_match_context
    original_calibration = base_runner.apply_oof_matrix_calibration

    def actionable_prepare(match_input):
        context = original_prepare(match_input)
        lineup_status = context.get("lineup_assessment", {}).get("status")
        context.setdefault("gates", {})["new_freeze_required_on_official_lineup_or_major_market_move"] = False
        context["gates"]["question_time_decision_freeze_locked"] = True
        context["gates"]["probable_lineup_allowed"] = lineup_status in {"通过", "部分通过"}
        context["gates"]["refreeze_policy"] = (
            "Do not wait for official lineups or closing odds. Re-run only on user request or a major confirmed "
            "change before the user's action deadline."
        )
        return context

    def calibrated_then_promoted(context, calculation):
        calibrated = original_calibration(context, calculation)
        return apply_hash_bound_promoted_v470_challengers(context, calibrated)

    base_runner.prepare_match_context = actionable_prepare
    base_runner.apply_oof_matrix_calibration = calibrated_then_promoted
    try:
        return live_runner.main()
    finally:
        base_runner.prepare_match_context = original_prepare
        base_runner.apply_oof_matrix_calibration = original_calibration


if __name__ == "__main__":
    raise SystemExit(main())
