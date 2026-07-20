#!/usr/bin/env python3
"""V4.6.4 staged actionable wrapper.

Adds question-time evidence intake flags, total-goal peak/plateau reporting,
a unified price/EV/No-Bet state machine, runtime CURRENT receipt stamping, and
safe repair of the legacy 26+ tail-only matrix downgrade without changing the
frozen V4.6.x probability-engine hash.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import run_formal_prediction_actionable as current_runner
from audit_receipt_utils_v464 import total_peak_diagnostics
from decision_state_policy_v477 import apply_price_ev_state

RUNTIME_RULE_ANCHOR = Path(__file__).resolve().parents[1] / "config" / "runtime_rule_anchor_v477.json"


def _arg_value(flag: str) -> str | None:
    if flag not in sys.argv:
        return None
    index = sys.argv.index(flag)
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _load_runtime_rule_anchor() -> dict:
    if not RUNTIME_RULE_ANCHOR.exists():
        return {}
    try:
        value = json.loads(RUNTIME_RULE_ANCHOR.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _repair_legacy_tail_only_matrix_downgrade(calculation: dict) -> None:
    """Undo only the known legacy downgrade caused solely by a represented 26+ tail.

    The frozen engine builds an explicit tail bucket at max_total+1 and then used
    `tail_probability <= 1e-6` as the matrix runtime-state gate. A non-zero tail is
    not itself an audit failure when the tail cells are present and total
    probability is conserved. This wrapper repairs that reporting state only; it
    does not alter any probability.
    """
    states = calculation.setdefault("module_states", {})
    if states.get("unified_score_matrix") != "降级":
        return

    model_audit = calculation.get("model_audit") or {}
    audit = model_audit.get("audit") or {}
    probabilities = calculation.get("probabilities") or {}
    matrix = probabilities.get("score_matrix")
    try:
        probability_sum = float(audit.get("probability_sum"))
        tail_total = int(audit.get("tail_aggregation_total"))
        tail_probability = float(audit.get("tail_aggregation_probability"))
    except (TypeError, ValueError):
        return

    if not isinstance(matrix, list) or not matrix:
        return
    if not math.isfinite(probability_sum) or abs(probability_sum - 1.0) > 1e-8:
        return
    if tail_total < 7 or not math.isfinite(tail_probability) or tail_probability < 0.0:
        return

    represented_tail = 0.0
    ranked: list[tuple[str, float]] = []
    for cell in matrix:
        if not isinstance(cell, dict):
            return
        try:
            home = int(cell["home_goals"])
            away = int(cell["away_goals"])
            probability = float(cell["probability"])
        except (KeyError, TypeError, ValueError):
            return
        if not math.isfinite(probability) or probability < 0.0:
            return
        if home + away == tail_total:
            represented_tail += probability
        ranked.append((f"{home}-{away}", probability))

    if abs(represented_tail - tail_probability) > 1e-8:
        return

    states["unified_score_matrix"] = "通过"
    calculation.setdefault("runtime_repairs", {})["legacy_tail_only_matrix_downgrade"] = {
        "status": "通过",
        "policy": "non-zero represented 26+ tail does not downgrade an otherwise probability-conserving matrix",
        "tail_total": tail_total,
        "tail_probability": tail_probability,
        "represented_tail_probability": represented_tail,
        "probability_sum": probability_sum,
        "probability_changed": False,
    }

    ranked.sort(key=lambda item: (-item[1], item[0]))
    conclusions = calculation.setdefault("conclusions", {})
    if ranked:
        conclusions["top_score"] = ranked[0][0]
        conclusions["second_score"] = ranked[1][0] if len(ranked) > 1 else None
        conclusions["top3_cumulative"] = sum(item[1] for item in ranked[:3])
        conclusions["top1_top2_gap"] = ranked[0][1] - ranked[1][1] if len(ranked) > 1 else None
        conclusions["score_text"] = f"模型中心比分 {ranked[0][0]}；EXACT独立门控未通过。"
        conclusions["score_label"] = "模型中心比分"


def _postprocess() -> None:
    context_path = _arg_value("--context-output")
    calculation_path = _arg_value("--calculation-output")
    context = None
    rule_anchor = _load_runtime_rule_anchor()

    if context_path and Path(context_path).exists():
        path = Path(context_path)
        context = json.loads(path.read_text(encoding="utf-8"))
        gates = context.setdefault("gates", {})
        market_assessment = context.get("market_assessment", {})
        market_status = market_assessment.get("status")
        lineup_status = context.get("lineup_assessment", {}).get("status")
        gates["question_time_market_freeze_policy"] = (
            "unplayed=user question-time prices; historical replay=last verifiable complete pre-kickoff snapshot"
        )
        gates["external_market_acquisition_required"] = market_status in {None, "不可用"}
        gates["probable_lineup_acquisition_required"] = lineup_status in {None, "不可用"}
        gates["probable_lineup_policy"] = (
            "do not wait for official XI; use supported probable XI from current-season verified XI history plus current roster, injuries, suspensions and team news"
        )
        # Formal execution EV remains closed until a competition-specific LOMO /
        # price-execution validation receipt explicitly opens this gate.
        gates.setdefault("formal_ev_execution_validated", False)

        # Pre-calculation state describes data availability only. No Bet is a later
        # decision outcome and must never be used as a runtime state here.
        context_states = context.setdefault("module_states", {})
        if market_status in {None, "不可用"}:
            context_states["price_ev_no_bet"] = "不可用"
        elif market_assessment.get("ev_gate"):
            context_states["price_ev_no_bet"] = "未启用"
        else:
            context_states["price_ev_no_bet"] = "降级"

        if rule_anchor:
            context["runtime_rule_anchor"] = {
                "formal_rule_version": rule_anchor.get("formal_rule_version"),
                "formal_rule_file": rule_anchor.get("formal_rule_file"),
                "authority": rule_anchor.get("authority"),
            }

        context["runtime_hardening_revision"] = "V4.6.4-staged-runtime-hardening-r5"
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if calculation_path and Path(calculation_path).exists():
        path = Path(calculation_path)
        calculation = json.loads(path.read_text(encoding="utf-8"))

        # Repair the known legacy tail-state reporting bug before downstream price
        # state is derived. This changes only runtime state / receipt fields.
        _repair_legacy_tail_only_matrix_downgrade(calculation)

        totals = calculation.get("probabilities", {}).get("total_goals")
        if isinstance(totals, dict):
            peak = total_peak_diagnostics(totals)
            calculation.setdefault("model_audit", {})["total_peak_diagnostics"] = peak
            conclusions = calculation.setdefault("conclusions", {})
            conclusions["total_goals_peak_strength"] = peak["strength"]
            conclusions["total_goals_primary_secondary_gap"] = peak["gap"]
            conclusions["total_goals_single_point_status"] = peak["single_point_status"]
            conclusions["total_goals_reporting_mode"] = peak["reporting_mode"]
            conclusions["total_goals_display_primary"] = peak["primary"]
            conclusions["total_goals_display_secondary"] = peak["secondary"]
            conclusions["total_goals_plateau_label"] = peak.get("plateau_label")

            if peak["reporting_mode"] == "plateau":
                label = peak.get("plateau_label") or "Top-2平台"
                conclusions["total_goals_text"] = (
                    f"模型总进球Top-1：{peak['primary']}球（{peak['primary_probability']:.2%}，{peak['strength']}）；"
                    f"次选{peak['secondary']}球（{peak['secondary_probability']:.2%}）；"
                    f"{label}；第一第二差距仅{peak['gap']:.2%}；"
                    "保留Top-1，但不得表述为高置信单一总进球；完整0—7+分布保留。"
                )
            else:
                conclusions["total_goals_text"] = (
                    f"模型总进球Top-1：{peak['primary']}球（{peak['strength']}）；"
                    f"次选{peak['secondary']}球；差距{peak['gap']:.2%}；完整0—7+分布保留。"
                )

        if context is None and context_path and Path(context_path).exists():
            context = json.loads(Path(context_path).read_text(encoding="utf-8"))
        if isinstance(context, dict):
            calculation = apply_price_ev_state(context, calculation)

        if rule_anchor:
            calculation["rule_version"] = rule_anchor.get("formal_rule_version")
            calculation["formal_rule_file"] = rule_anchor.get("formal_rule_file")
            calculation["runtime_rule_anchor_authority"] = rule_anchor.get("authority")

        calculation["runtime_hardening_revision"] = "V4.6.4-staged-runtime-hardening-r5"
        path.write_text(json.dumps(calculation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    rc = current_runner.main()
    if rc == 0:
        _postprocess()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
