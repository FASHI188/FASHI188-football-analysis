#!/usr/bin/env python3
"""V4.6.4 staged actionable wrapper.

Adds question-time evidence intake flags and total-goal peak/plateau reporting
without changing the frozen V4.6.x probability engine.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import run_formal_prediction_actionable as current_runner
from audit_receipt_utils_v464 import total_peak_diagnostics


def _arg_value(flag: str) -> str | None:
    if flag not in sys.argv:
        return None
    index = sys.argv.index(flag)
    return sys.argv[index + 1] if index + 1 < len(sys.argv) else None


def _postprocess() -> None:
    context_path = _arg_value("--context-output")
    calculation_path = _arg_value("--calculation-output")

    if context_path and Path(context_path).exists():
        path = Path(context_path)
        context = json.loads(path.read_text(encoding="utf-8"))
        gates = context.setdefault("gates", {})
        market_status = context.get("market_assessment", {}).get("status")
        lineup_status = context.get("lineup_assessment", {}).get("status")
        gates["question_time_market_freeze_policy"] = (
            "unplayed=user question-time prices; historical replay=last verifiable complete pre-kickoff snapshot"
        )
        gates["external_market_acquisition_required"] = market_status in {None, "不可用"}
        gates["probable_lineup_acquisition_required"] = lineup_status in {None, "不可用"}
        gates["probable_lineup_policy"] = (
            "do not wait for official XI; use supported probable XI from current-season verified XI history plus current roster, injuries, suspensions and team news"
        )
        context["runtime_hardening_revision"] = "V4.6.4-staged-total-plateau-report-r2"
        path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if calculation_path and Path(calculation_path).exists():
        path = Path(calculation_path)
        calculation = json.loads(path.read_text(encoding="utf-8"))
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

        calculation["runtime_hardening_revision"] = "V4.6.4-staged-total-plateau-report-r2"
        path.write_text(json.dumps(calculation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    rc = current_runner.main()
    if rc == 0:
        _postprocess()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
