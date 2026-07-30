#!/usr/bin/env python3
"""Audit exact-constraint infeasibility for E3b-2 without relaxing constraints."""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import e3b2_min_kl_unified_matrix as e3b2  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3b2_min_kl_unified_matrix"
TOL = e3b2.TOL


def repository_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT.parent,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row.get("e3b2_status", "MISSING")) for row in records)
    by_competition: dict[str, Counter[str]] = defaultdict(Counter)
    by_season: dict[str, Counter[str]] = defaultdict(Counter)
    violation_types = Counter()
    below_gaps: list[float] = []
    above_gaps: list[float] = []
    examples = []

    for row in records:
        status = str(row.get("e3b2_status", "MISSING"))
        cid = str(row.get("competition_id"))
        season = str(row.get("season"))
        by_competition[cid][status] += 1
        by_season[season][status] += 1
        feasibility = row.get("e3b2_feasibility") or {}
        if status != "INFEASIBLE" or not feasibility:
            continue

        target = float(feasibility.get("draw_target", 0.0))
        lower = float(feasibility.get("draw_min_from_total_support", 0.0))
        upper = float(feasibility.get("draw_max_from_total_support", 1.0))
        if target < lower - TOL:
            kind = "DRAW_TARGET_BELOW_P_T0"
            gap = lower - target
            below_gaps.append(gap)
        elif target > upper + TOL:
            kind = "DRAW_TARGET_ABOVE_EVEN_TOTAL_MASS"
            gap = target - upper
            above_gaps.append(gap)
        else:
            kind = "OTHER_SUPPORT_INFEASIBILITY"
            gap = 0.0
        violation_types[kind] += 1
        if len(examples) < 100:
            examples.append({
                "match_key": row.get("match_key"),
                "competition_id": cid,
                "season": season,
                "draw_target": target,
                "p_t0_lower_bound": lower,
                "even_total_draw_upper_bound": upper,
                "violation_type": kind,
                "violation_gap": gap,
            })

    return {
        "count": len(records),
        "status_counts": dict(status_counts),
        "converged_count": int(status_counts.get("CONVERGED", 0)),
        "infeasible_count": int(status_counts.get("INFEASIBLE", 0)),
        "not_converged_count": int(status_counts.get("NOT_CONVERGED", 0)),
        "converged_rate": (
            status_counts.get("CONVERGED", 0) / len(records) if records else 0.0
        ),
        "violation_types": dict(violation_types),
        "by_competition": {key: dict(value) for key, value in by_competition.items()},
        "by_season": {key: dict(value) for key, value in by_season.items()},
        "mean_draw_deficit_below_p_t0": mean(below_gaps) if below_gaps else None,
        "max_draw_deficit_below_p_t0": max(below_gaps, default=None),
        "mean_draw_excess_above_even_mass": mean(above_gaps) if above_gaps else None,
        "max_draw_excess_above_even_mass": max(above_gaps, default=None),
        "examples": examples,
    }


def projected_subset_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    converged = [row for row in records if row.get("e3b2_status") == "CONVERGED"]
    if not converged:
        return {"count": 0}
    outcome_identity = max(
        abs(float(row["e3b2_probs"][label]) - float(row["e3b1_probs"][label]))
        for row in converged
        for label in e3b2.OUTCOMES
    )
    return {
        "count": len(converged),
        "projection_audit": e3b2.projection_audit(converged),
        "champion_outcome": e3b2.e3b1.metrics(converged, "champion_probs"),
        "e3b1_outcome": e3b2.e3b1.metrics(converged, "e3b1_probs"),
        "e3b2_outcome": e3b2.e3b1.metrics(converged, "e3b2_probs"),
        "champion_score": e3b2.score_metrics(converged, "matrix"),
        "e3b2_score": e3b2.score_metrics(converged, "e3b2_matrix"),
        "outcome_identity_max_residual": outcome_identity,
    }


def markdown(report: dict[str, Any]) -> str:
    full = report["full_oos"]
    fixed = report["b100"]
    lines = [
        "# E3b-2 Exact-Constraint Infeasibility Audit",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Audit status: **{report['audit_status']}**",
        f"- Exact projection gate: **{report['exact_projection_gate']}**",
        f"- Full OOS: {full['count']}",
        f"- Converged: {full['converged_count']} ({full['converged_rate']:.4%})",
        f"- Infeasible: {full['infeasible_count']}",
        f"- Not converged: {full['not_converged_count']}",
        f"- B100 infeasible: {fixed['infeasible_count']}/{fixed['count']}",
        "",
        "## Mathematical cause",
        "",
        f"- Violation types: `{json.dumps(full['violation_types'], ensure_ascii=False)}`",
        f"- Mean draw deficit below P(T=0): {full['mean_draw_deficit_below_p_t0']}",
        f"- Max draw deficit below P(T=0): {full['max_draw_deficit_below_p_t0']}",
        f"- Mean draw excess above even-total mass: {full['mean_draw_excess_above_even_mass']}",
        f"- Max draw excess above even-total mass: {full['max_draw_excess_above_even_mass']}",
        "",
        "T=0 has only one legal score cell, 0:0. Therefore any direct draw target below "
        "P(T=0) is incompatible with preserving the total-goal marginal exactly.",
        "",
        "## By competition",
        "",
        "| Competition | Total | Converged | Infeasible | Not converged |",
        "|---|---:|---:|---:|---:|",
    ]
    for cid in b100.BIG5:
        counts = full["by_competition"].get(cid, {})
        total = sum(int(value) for value in counts.values())
        lines.append(
            f"| {b100.BIG5[cid]} | {total} | {int(counts.get('CONVERGED', 0))} | "
            f"{int(counts.get('INFEASIBLE', 0))} | {int(counts.get('NOT_CONVERGED', 0))} |"
        )
    subset = report["feasible_subset"]
    if subset.get("count"):
        pa = subset["projection_audit"]
        lines.extend((
            "",
            "## Feasible-subset diagnostics",
            "",
            f"- Count: {subset['count']}",
            f"- Max probability residual: {pa['max_probability_residual']:.3e}",
            f"- Max total residual: {pa['max_total_marginal_residual']:.3e}",
            f"- Max outcome residual: {pa['max_outcome_marginal_residual']:.3e}",
            f"- Outcome identity residual: {subset['outcome_identity_max_residual']:.3e}",
            f"- Mean / max KL: {pa['mean_kl_q_prior']:.6f} / {pa['max_kl_q_prior']:.6f}",
            "",
        ))
    lines.extend((
        "## Fixed verdict",
        "",
        "- Exact E3b-2 hard gate fails if any match is infeasible.",
        "- No clipping, manual draw uplift, hidden slack, or silent fallback is allowed.",
        "- Feasible-subset results are diagnostic only and have formal_weight=0.",
        "- E3b-1 remains not promoted.",
        "",
    ))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(OUT))
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    evaluated, lineage = e3b2.build_records()
    projected = [e3b2.project_record(row) for row in evaluated]
    full_summary = summarize(projected)

    by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in projected:
        by_competition[row["competition_id"]].append(row)
    b100_rows, selection = e3a.fixed_b100(by_competition)
    b100_summary = summarize(b100_rows)

    exact_pass = (
        full_summary["infeasible_count"] == 0
        and full_summary["not_converged_count"] == 0
        and b100_summary["count"] == b100.TARGET_PER_LEAGUE * len(b100.BIG5)
        and b100_summary["infeasible_count"] == 0
        and b100_summary["not_converged_count"] == 0
    )
    report = {
        "schema_version": "1.0",
        "audit_status": "PASS",
        "exact_projection_gate": "PASS" if exact_pass else "FAIL",
        "repository_head": repository_head(),
        "objective": "min_q KL(q || Champion prior)",
        "constraints": [
            "sum(q)=1",
            "preserve every Champion total-goal marginal",
            "match E3b-1 H/D/A marginal exactly",
            "preserve prior zero support",
        ],
        "constraint_relaxation": False,
        "full_oos": full_summary,
        "b100": {
            **b100_summary,
            "selection": selection,
        },
        "feasible_subset": projected_subset_metrics(projected),
        "lineage": lineage,
        "promotion": {
            "automatic_promotion": False,
            "formal_weight": 0,
            "status": "CHALLENGE_LAYER_ONLY",
            "e3b1_promoted": False,
        },
        "formal_mutation": {
            "model": 0,
            "data": 0,
            "config": 0,
            "current": 0,
            "formal_weight": 0,
        },
    }
    (output_dir / "e3b2_exact_constraint_infeasibility_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "e3b2_exact_constraint_infeasibility_audit.md").write_text(
        markdown(report), encoding="utf-8"
    )
    if args.print_summary:
        print(json.dumps({
            "audit_status": report["audit_status"],
            "exact_projection_gate": report["exact_projection_gate"],
            "repository_head": report["repository_head"],
            "full_oos": {
                key: report["full_oos"][key]
                for key in (
                    "count",
                    "converged_count",
                    "infeasible_count",
                    "not_converged_count",
                    "converged_rate",
                    "violation_types",
                    "mean_draw_deficit_below_p_t0",
                    "max_draw_deficit_below_p_t0",
                )
            },
            "b100": {
                "count": report["b100"]["count"],
                "infeasible_count": report["b100"]["infeasible_count"],
                "violation_types": report["b100"]["violation_types"],
            },
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
