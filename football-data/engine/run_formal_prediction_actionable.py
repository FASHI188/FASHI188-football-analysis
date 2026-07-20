#!/usr/bin/env python3
"""Actionable question-time runner for routine pre-match decisions.

The user's question time is the decision freeze. A probable-lineup route remains
valid for the primary answer; publication of official lineups does not by itself
make the already-frozen answer unavailable. Major confirmed changes remain an
invalidation condition and may trigger a new run when requested.

V4.7 competition-specific promoted challengers are applied only through the
hash-bound runtime activation gate after the existing OOF matrix calibration.
The final total-goals peak diagnostic is read-only and runs last.
Formal EV and market coordination are separately fail-closed behind a
competition-specific LOMO/OOS receipt. A lineup status label is not enough to
receive lineup confidence credit: an official XI or executable probable-XI
projection must actually be present. Final rule authority is normalized from the
active governance manifest while preserving the underlying implementation version.
"""
from __future__ import annotations

import run_formal_prediction_live as live_runner
import run_formal_prediction_v460 as base_runner
from formal_ev_lomo_gate_v470 import apply_formal_ev_lomo_gate
from formal_governance_runtime_v470 import apply_formal_governance_runtime
from probable_lineup_runtime_v470 import apply_probable_lineup_runtime
from promoted_challenger_runtime_gate_v470 import apply_hash_bound_promoted_v470_challengers
from total_goals_peak_diagnostics_v470 import apply_total_goals_peak_diagnostics


def main() -> int:
    original_prepare = base_runner.prepare_match_context
    original_calibration = base_runner.apply_oof_matrix_calibration

    def actionable_prepare(match_input):
        context = original_prepare(match_input)
        # The base formal wrapper requires explicit season and writes it after
        # prepare(). Put the same value into the actionable context before
        # season-bound evidence gates run.
        if str(match_input.get("season") or "").strip():
            context.setdefault("match_identity", {})["season"] = str(match_input["season"]).strip()
        context = apply_formal_ev_lomo_gate(context)
        context = apply_probable_lineup_runtime(match_input, context)

        lineup_audit = context.get("probable_lineup_v470_audit") or {}
        lineup_runtime_status = str(lineup_audit.get("status") or "不可用")
        # The formal core uses lineup_assessment.status for confidence grading.
        # Feed it the audited runtime result, not the user's bare status label.
        context.setdefault("lineup_assessment", {})["status"] = (
            lineup_runtime_status if lineup_runtime_status in {"通过", "部分通过"} else "不可用"
        )
        lineup_detail_available = bool((context.get("lineup_projection") or {}).get("starting_xi"))

        context.setdefault("gates", {})["new_freeze_required_on_official_lineup_or_major_market_move"] = False
        context["gates"]["question_time_decision_freeze_locked"] = True
        context["gates"]["probable_lineup_allowed"] = lineup_detail_available
        context["gates"]["refreeze_policy"] = (
            "Do not wait for official lineups or closing odds. Re-run only on user request or a major confirmed "
            "change before the user's action deadline."
        )
        return context

    def calibrated_then_promoted(context, calculation):
        calibrated = original_calibration(context, calculation)
        promoted = apply_hash_bound_promoted_v470_challengers(context, calibrated)
        diagnosed = apply_total_goals_peak_diagnostics(promoted)
        return apply_formal_governance_runtime(diagnosed)

    base_runner.prepare_match_context = actionable_prepare
    base_runner.apply_oof_matrix_calibration = calibrated_then_promoted
    try:
        return live_runner.main()
    finally:
        base_runner.prepare_match_context = original_prepare
        base_runner.apply_oof_matrix_calibration = original_calibration


if __name__ == "__main__":
    raise SystemExit(main())
