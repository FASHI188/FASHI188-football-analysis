#!/usr/bin/env python3
"""E3b-2 research: minimum-KL/IPF projection into one unified score matrix.

For each match, use the Champion unified score matrix as the prior, preserve its
complete total-goal marginal, and impose the E3b-1 H/D/A marginal. The unique
I-projection is computed by iterative proportional fitting over total-goal and
outcome partitions. This is research-only and has formal_weight=0.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

HERE = Path(__file__).resolve().parent
FD = HERE.parent
for path in (FD / "engine", FD / "validation", HERE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import big5_high_completeness_b100 as b100  # noqa: E402
import market_joint_direct_outcome_e3b1 as e3b1  # noqa: E402
import matrix_draw_gate_e3a as e3a  # noqa: E402
from platform_core import ROOT  # noqa: E402

OUT = ROOT.parent / "artifacts/research/e3b2_min_kl_unified_matrix"
OUTCOMES = ("home", "draw", "away")
EPS = 1e-15
TOL = 1e-11
MAX_ITER = 2000


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


def outcome(home: int, away: int) -> str:
    return "home" if home > away else "draw" if home == away else "away"


def normalize_probabilities(values: dict[str, float]) -> dict[str, float]:
    total = sum(float(values[name]) for name in OUTCOMES)
    if total <= 0:
        raise RuntimeError("outcome target has no mass")
    return {name: float(values[name]) / total for name in OUTCOMES}


def matrix_groups(cells: list[dict[str, Any]]) -> tuple[dict[int, list[int]], dict[str, list[int]]]:
    by_total: dict[int, list[int]] = defaultdict(list)
    by_outcome: dict[str, list[int]] = defaultdict(list)
    for index, cell in enumerate(cells):
        home = int(cell["home_goals"])
        away = int(cell["away_goals"])
        by_total[home + away].append(index)
        by_outcome[outcome(home, away)].append(index)
    return dict(by_total), dict(by_outcome)


def necessary_feasibility(
    total_targets: dict[int, float],
    outcome_targets: dict[str, float],
) -> dict[str, Any]:
    draw_min = float(total_targets.get(0, 0.0))
    draw_max = sum(
        probability for total, probability in total_targets.items()
        if total % 2 == 0
    )
    draw = float(outcome_targets["draw"])
    feasible = draw_min - TOL <= draw <= draw_max + TOL
    return {
        "status": "PASS" if feasible else "FAIL",
        "draw_target": draw,
        "draw_min_from_total_support": draw_min,
        "draw_max_from_total_support": draw_max,
    }


def project_record(record: dict[str, Any]) -> dict[str, Any]:
    cells = [
        {
            "home_goals": int(cell["home_goals"]),
            "away_goals": int(cell["away_goals"]),
            "probability": float(cell["probability"]),
        }
        for cell in record["matrix"]
    ]
    prior = [max(0.0, float(cell["probability"])) for cell in cells]
    prior_sum = sum(prior)
    if prior_sum <= 0:
        return {**record, "e3b2_status": "INFEASIBLE", "e3b2_error": "prior matrix has no mass"}
    prior = [value / prior_sum for value in prior]
    for cell, probability in zip(cells, prior):
        cell["probability"] = probability

    by_total, by_outcome = matrix_groups(cells)
    total_targets = {
        total: sum(prior[index] for index in indices)
        for total, indices in by_total.items()
    }
    outcome_targets = normalize_probabilities(record["e3b1_probs"])
    feasibility = necessary_feasibility(total_targets, outcome_targets)
    if feasibility["status"] != "PASS":
        return {
            **record,
            "e3b2_status": "INFEASIBLE",
            "e3b2_error": "outcome target violates total-support draw bounds",
            "e3b2_feasibility": feasibility,
        }

    for total, indices in by_total.items():
        if total_targets[total] > TOL and not any(prior[index] > 0 for index in indices):
            return {
                **record,
                "e3b2_status": "INFEASIBLE",
                "e3b2_error": f"missing positive prior support for total={total}",
                "e3b2_feasibility": feasibility,
            }
    for label, indices in by_outcome.items():
        if outcome_targets[label] > TOL and not any(prior[index] > 0 for index in indices):
            return {
                **record,
                "e3b2_status": "INFEASIBLE",
                "e3b2_error": f"missing positive prior support for outcome={label}",
                "e3b2_feasibility": feasibility,
            }

    projected = list(prior)
    converged = False
    iterations = 0
    max_total_residual = math.inf
    max_outcome_residual = math.inf

    for iterations in range(1, MAX_ITER + 1):
        for total, indices in by_total.items():
            current = sum(projected[index] for index in indices)
            target = total_targets[total]
            if target <= TOL:
                for index in indices:
                    projected[index] = 0.0
            elif current <= 0:
                break
            else:
                scale = target / current
                for index in indices:
                    projected[index] *= scale
        else:
            for label, indices in by_outcome.items():
                current = sum(projected[index] for index in indices)
                target = outcome_targets[label]
                if target <= TOL:
                    for index in indices:
                        projected[index] = 0.0
                elif current <= 0:
                    break
                else:
                    scale = target / current
                    for index in indices:
                        projected[index] *= scale
            else:
                total_after = {
                    total: sum(projected[index] for index in indices)
                    for total, indices in by_total.items()
                }
                outcome_after = {
                    label: sum(projected[index] for index in indices)
                    for label, indices in by_outcome.items()
                }
                max_total_residual = max(
                    abs(total_after[total] - total_targets[total])
                    for total in by_total
                )
                max_outcome_residual = max(
                    abs(outcome_after[label] - outcome_targets[label])
                    for label in OUTCOMES
                )
                probability_residual = abs(sum(projected) - 1.0)
                if max(max_total_residual, max_outcome_residual, probability_residual) <= TOL:
                    converged = True
                    break
                continue
        break

    if not converged:
        return {
            **record,
            "e3b2_status": "NOT_CONVERGED",
            "e3b2_error": "IPF did not satisfy both marginal families",
            "e3b2_iterations": iterations,
            "e3b2_max_total_residual": max_total_residual,
            "e3b2_max_outcome_residual": max_outcome_residual,
            "e3b2_feasibility": feasibility,
        }

    matrix = []
    kl = 0.0
    support_preserved = True
    for cell, p, q in zip(cells, prior, projected):
        if p <= 0 and q > TOL:
            support_preserved = False
        if q > 0:
            if p <= 0:
                kl = math.inf
            else:
                kl += q * math.log(q / p)
        matrix.append({
            "home_goals": cell["home_goals"],
            "away_goals": cell["away_goals"],
            "probability": q,
        })

    projected_outcomes = {
        label: sum(projected[index] for index in by_outcome[label])
        for label in OUTCOMES
    }
    total_after = {
        total: sum(projected[index] for index in indices)
        for total, indices in by_total.items()
    }
    tail_before = sum(
        probability for total, probability in total_targets.items() if total >= 7
    )
    tail_after = sum(
        probability for total, probability in total_after.items() if total >= 7
    )
    btts_before = sum(
        prior[index]
        for index, cell in enumerate(cells)
        if int(cell["home_goals"]) > 0 and int(cell["away_goals"]) > 0
    )
    btts_after = sum(
        projected[index]
        for index, cell in enumerate(cells)
        if int(cell["home_goals"]) > 0 and int(cell["away_goals"]) > 0
    )

    return {
        **record,
        "e3b2_status": "CONVERGED",
        "e3b2_probs": projected_outcomes,
        "e3b2_matrix": matrix,
        "e3b2_iterations": iterations,
        "e3b2_kl_q_prior": kl,
        "e3b2_probability_residual": abs(sum(projected) - 1.0),
        "e3b2_max_total_residual": max_total_residual,
        "e3b2_max_outcome_residual": max_outcome_residual,
        "e3b2_support_preserved": support_preserved,
        "e3b2_feasibility": feasibility,
        "e3b2_tail_before": tail_before,
        "e3b2_tail_after": tail_after,
        "e3b2_btts_before": btts_before,
        "e3b2_btts_after": btts_after,
    }


def score_metrics(records: list[dict[str, Any]], matrix_field: str) -> dict[str, Any]:
    if not records:
        return {"count": 0}
    top1_hits = 0
    top3_hits = 0
    score_logloss = []
    btts_brier = []
    btts_logloss = []
    for record in records:
        matrix = record[matrix_field]
        ordered = sorted(
            matrix,
            key=lambda cell: (
                -float(cell["probability"]),
                int(cell["home_goals"]) + int(cell["away_goals"]),
                int(cell["home_goals"]),
                int(cell["away_goals"]),
            ),
        )
        actual = tuple(int(part) for part in str(record["actual_score"]).split("-", 1))
        top_scores = [
            (int(cell["home_goals"]), int(cell["away_goals"]))
            for cell in ordered[:3]
        ]
        top1_hits += int(bool(top_scores) and actual == top_scores[0])
        top3_hits += int(actual in top_scores)
        actual_probability = sum(
            float(cell["probability"])
            for cell in matrix
            if (int(cell["home_goals"]), int(cell["away_goals"])) == actual
        )
        score_logloss.append(-math.log(max(EPS, actual_probability)))
        btts_probability = sum(
            float(cell["probability"])
            for cell in matrix
            if int(cell["home_goals"]) > 0 and int(cell["away_goals"]) > 0
        )
        btts_actual = actual[0] > 0 and actual[1] > 0
        btts_brier.append((btts_probability - float(btts_actual)) ** 2)
        btts_logloss.append(
            -math.log(max(EPS, btts_probability if btts_actual else 1.0 - btts_probability))
        )
    return {
        "count": len(records),
        "exact_score_top1_accuracy": top1_hits / len(records),
        "exact_score_top3_coverage": top3_hits / len(records),
        "exact_score_logloss": mean(score_logloss),
        "btts_brier": mean(btts_brier),
        "btts_logloss": mean(btts_logloss),
    }


def deltas(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, float]:
    common = set(candidate) & set(baseline)
    return {
        key: float(candidate[key]) - float(baseline[key])
        for key in sorted(common)
        if key != "count" and isinstance(candidate[key], (int, float))
    }


def projection_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    converged = [record for record in records if record.get("e3b2_status") == "CONVERGED"]
    failures = [record for record in records if record.get("e3b2_status") != "CONVERGED"]
    iterations = [int(record["e3b2_iterations"]) for record in converged]
    kl_values = [float(record["e3b2_kl_q_prior"]) for record in converged]
    return {
        "count": len(records),
        "converged_count": len(converged),
        "failure_count": len(failures),
        "failure_examples": [
            {
                "match_key": record.get("match_key"),
                "status": record.get("e3b2_status"),
                "error": record.get("e3b2_error"),
            }
            for record in failures[:20]
        ],
        "all_converged": len(converged) == len(records),
        "max_iterations": max(iterations, default=0),
        "mean_iterations": mean(iterations) if iterations else None,
        "max_probability_residual": max(
            (float(record["e3b2_probability_residual"]) for record in converged),
            default=None,
        ),
        "max_total_marginal_residual": max(
            (float(record["e3b2_max_total_residual"]) for record in converged),
            default=None,
        ),
        "max_outcome_marginal_residual": max(
            (float(record["e3b2_max_outcome_residual"]) for record in converged),
            default=None,
        ),
        "all_support_preserved": all(
            bool(record["e3b2_support_preserved"]) for record in converged
        ),
        "mean_kl_q_prior": mean(kl_values) if kl_values else None,
        "max_kl_q_prior": max(kl_values, default=None),
        "max_tail_residual": max(
            (
                abs(float(record["e3b2_tail_after"]) - float(record["e3b2_tail_before"]))
                for record in converged
            ),
            default=None,
        ),
        "mean_absolute_btts_shift": mean(
            abs(float(record["e3b2_btts_after"]) - float(record["e3b2_btts_before"]))
            for record in converged
        ) if converged else None,
        "max_absolute_btts_shift": max(
            (
                abs(float(record["e3b2_btts_after"]) - float(record["e3b2_btts_before"]))
                for record in converged
            ),
            default=None,
        ),
    }


def section_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(records),
        "champion_outcome": e3b1.metrics(records, "champion_probs"),
        "e3b1_outcome": e3b1.metrics(records, "e3b1_probs"),
        "e3b2_outcome": e3b1.metrics(records, "e3b2_probs"),
        "champion_score": score_metrics(records, "matrix"),
        "e3b2_score": score_metrics(records, "e3b2_matrix"),
        "projection_audit": projection_audit(records),
    }


def per_league(records: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for cid, name in b100.BIG5.items():
        subset = [record for record in records if record["competition_id"] == cid]
        metrics = section_metrics(subset)
        metrics["competition_zh"] = name
        metrics["delta_e3b2_minus_champion_outcome"] = deltas(
            metrics["e3b2_outcome"], metrics["champion_outcome"]
        )
        metrics["delta_e3b2_minus_e3b1_outcome"] = deltas(
            metrics["e3b2_outcome"], metrics["e3b1_outcome"]
        )
        metrics["delta_e3b2_minus_champion_score"] = deltas(
            metrics["e3b2_score"], metrics["champion_score"]
        )
        result[cid] = metrics
    return result


def build_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_rows: list[dict[str, Any]] = []
    join_audit: dict[str, Any] = {}
    base_folds: dict[str, Any] = {}
    failures = []
    for cid in b100.BIG5:
        try:
            season_rows, folds = e3a.nested_competition(cid)
            rows, audit = e3b1.join(cid, season_rows)
            all_rows.extend(rows)
            join_audit[cid] = audit
            base_folds[cid] = folds
        except Exception as exc:
            failures.append({
                "competition_id": cid,
                "error": f"{type(exc).__name__}: {exc}",
            })
    if failures or not all_rows:
        raise RuntimeError(json.dumps(failures or [{"error": "no rows"}], ensure_ascii=False))
    evaluated, head_folds = e3b1.expanding_oos(all_rows)
    return evaluated, {
        "join_audit": join_audit,
        "base_parameter_folds": base_folds,
        "e3b1_head_folds": head_folds,
    }


def markdown(report: dict[str, Any]) -> str:
    full = report["full_oos"]
    audit = full["projection_audit"]
    lines = [
        "# E3b-2 Minimum-KL/IPF Unified Matrix Coordination",
        "",
        "Research-only; formal_weight=0; no automatic promotion.",
        "",
        f"- Repository HEAD: `{report['repository_head']}`",
        f"- Prior: {report['optimization']['prior']}",
        f"- Objective: `{report['optimization']['objective']}`",
        f"- Constraints: {', '.join(report['optimization']['constraints'])}",
        f"- Full OOS records: {full['count']}",
        f"- Fixed B100 records: {report['b100']['count']}",
        f"- Converged: {audit['converged_count']}/{audit['count']}",
        f"- Max outcome residual: {audit['max_outcome_marginal_residual']:.3e}",
        f"- Max total residual: {audit['max_total_marginal_residual']:.3e}",
        f"- Max probability residual: {audit['max_probability_residual']:.3e}",
        f"- Mean / max iterations: {audit['mean_iterations']:.2f} / {audit['max_iterations']}",
        f"- Mean / max KL(q||prior): {audit['mean_kl_q_prior']:.6f} / {audit['max_kl_q_prior']:.6f}",
        "",
        "## Full OOS outcome metrics",
        "",
        "| Model | Accuracy | Balanced Acc. | Macro-F1 | Draw P | Draw R | Draw F1 | LogLoss | Brier | RPS | ECE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, key in (
        ("Champion prior", "champion_outcome"),
        ("E3b-1 target", "e3b1_outcome"),
        ("E3b-2 matrix", "e3b2_outcome"),
    ):
        m = full[key]
        lines.append(
            f"| {label} | {m['accuracy']:.4%} | {m['balanced_accuracy']:.4%} | "
            f"{m['macro_f1']:.4%} | {m['draw_precision']:.4%} | "
            f"{m['draw_recall']:.4%} | {m['draw_f1']:.4%} | "
            f"{m['logloss']:.6f} | {m['brier']:.6f} | {m['rps']:.6f} | "
            f"{m['confidence_ece_10bin']:.6f} |"
        )
    lines.extend((
        "",
        "## Full OOS score-matrix metrics",
        "",
        "| Matrix | Exact Top-1 | Exact Top-3 | Exact LogLoss | BTTS Brier | BTTS LogLoss |",
        "|---|---:|---:|---:|---:|---:|",
    ))
    for label, key in (("Champion prior", "champion_score"), ("E3b-2", "e3b2_score")):
        m = full[key]
        lines.append(
            f"| {label} | {m['exact_score_top1_accuracy']:.4%} | "
            f"{m['exact_score_top3_coverage']:.4%} | {m['exact_score_logloss']:.6f} | "
            f"{m['btts_brier']:.6f} | {m['btts_logloss']:.6f} |"
        )
    lines.extend((
        "",
        "## Per-league projection audit",
        "",
        "| League | N | Converged | Max outcome residual | Max total residual | Mean KL | Exact Top-1 delta |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ))
    for cid, item in full["per_league"].items():
        pa = item["projection_audit"]
        score_delta = item["delta_e3b2_minus_champion_score"]["exact_score_top1_accuracy"]
        lines.append(
            f"| {item['competition_zh']} | {item['count']} | "
            f"{pa['converged_count']}/{pa['count']} | "
            f"{pa['max_outcome_marginal_residual']:.3e} | "
            f"{pa['max_total_marginal_residual']:.3e} | "
            f"{pa['mean_kl_q_prior']:.6f} | {score_delta:+.4%} |"
        )
    lines.extend((
        "",
        "## Fixed verdict",
        "",
        "- E3b-2 PASS means only that the stated I-projection was actually computed and audited.",
        "- Outcome metrics must equal E3b-1 within numerical tolerance because H/D/A is an explicit constraint.",
        "- Total-goal marginal is preserved exactly; any total-goal performance claim is unchanged by construction.",
        "- Score/BTTS shifts are downstream consequences of the constrained projection, not new fitted skill.",
        "- Formal use remains prohibited; formal_weight=0.",
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

    try:
        evaluated, lineage = build_records()
        projected = [project_record(record) for record in evaluated]
        failures = [
            {
                "match_key": record.get("match_key"),
                "status": record.get("e3b2_status"),
                "error": record.get("e3b2_error"),
            }
            for record in projected
            if record.get("e3b2_status") != "CONVERGED"
        ]
        if failures:
            raise RuntimeError(f"projection failures: {failures[:20]}")

        by_competition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in projected:
            by_competition[record["competition_id"]].append(record)
        b100_rows, selection = e3a.fixed_b100(by_competition)

        full = section_metrics(projected)
        fixed = section_metrics(b100_rows)
        full["per_league"] = per_league(projected)
        fixed["per_league"] = per_league(b100_rows)
        fixed["selection"] = selection

        outcome_identity = max(
            abs(
                float(record["e3b2_probs"][label])
                - float(record["e3b1_probs"][label])
            )
            for record in projected
            for label in OUTCOMES
        )
        expected_b100 = b100.TARGET_PER_LEAGUE * len(b100.BIG5)
        audit = full["projection_audit"]
        passed = (
            len(b100_rows) == expected_b100
            and audit["all_converged"]
            and audit["all_support_preserved"]
            and float(audit["max_probability_residual"]) <= TOL
            and float(audit["max_total_marginal_residual"]) <= TOL
            and float(audit["max_outcome_marginal_residual"]) <= TOL
            and float(audit["max_tail_residual"]) <= TOL
            and outcome_identity <= TOL
        )

        report = {
            "schema_version": "1.0",
            "research_status": "PASS" if passed else "FAIL",
            "repository_head": repository_head(),
            "scope": "90_minutes_including_stoppage",
            "experiment": "E3B2_MINIMUM_KL_IPF_UNIFIED_MATRIX",
            "optimization": {
                "prior": "Champion unified score matrix",
                "objective": "min_q KL(q || p_prior)",
                "constraints": [
                    "sum(q)=1",
                    "Champion direct total-goal marginal preserved for every represented total",
                    "H/D/A marginal equals E3b-1 direct outcome target",
                    "prior zero support preserved",
                    "score cells remain nonnegative integer H/A coordinates",
                ],
                "algorithm": "iterative proportional fitting over total and outcome partitions",
                "tolerance": TOL,
                "maximum_iterations": MAX_ITER,
                "manual_probability_adjustment": False,
            },
            "full_oos": full,
            "b100": fixed,
            "audit": {
                "outcome_identity_max_residual": outcome_identity,
                "b100_count_contract": "PASS" if len(b100_rows) == expected_b100 else "FAIL",
                "probability_conservation": "PASS" if audit["max_probability_residual"] <= TOL else "FAIL",
                "total_marginal": "PASS" if audit["max_total_marginal_residual"] <= TOL else "FAIL",
                "outcome_marginal": "PASS" if audit["max_outcome_marginal_residual"] <= TOL else "FAIL",
                "tail_probability": "PASS" if audit["max_tail_residual"] <= TOL else "FAIL",
                "support_preservation": "PASS" if audit["all_support_preserved"] else "FAIL",
            },
            "lineage": lineage,
            "promotion": {
                "automatic_promotion": False,
                "formal_weight": 0,
                "status": "CHALLENGE_LAYER_ONLY",
                "e3b1_promoted": False,
                "per_domain_forward_validation": "NOT_EVALUATED",
            },
            "formal_mutation": {
                "model": 0,
                "data": 0,
                "config": 0,
                "current": 0,
                "formal_weight": 0,
            },
            "failures": [],
        }
    except Exception as exc:
        report = {
            "schema_version": "1.0",
            "research_status": "FAIL",
            "repository_head": repository_head(),
            "experiment": "E3B2_MINIMUM_KL_IPF_UNIFIED_MATRIX",
            "failures": [{"error": f"{type(exc).__name__}: {exc}"}],
            "promotion": {
                "automatic_promotion": False,
                "formal_weight": 0,
                "status": "CHALLENGE_LAYER_ONLY",
            },
            "formal_mutation": {
                "model": 0,
                "data": 0,
                "config": 0,
                "current": 0,
                "formal_weight": 0,
            },
        }

    json_path = output_dir / "e3b2_min_kl_unified_matrix.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if report["research_status"] == "PASS":
        (output_dir / "e3b2_min_kl_unified_matrix.md").write_text(
            markdown(report), encoding="utf-8"
        )
    if args.print_summary:
        print(json.dumps({
            "research_status": report["research_status"],
            "repository_head": report.get("repository_head"),
            "audit": report.get("audit"),
            "full_oos": {
                key: report.get("full_oos", {}).get(key)
                for key in (
                    "count",
                    "champion_outcome",
                    "e3b1_outcome",
                    "e3b2_outcome",
                    "champion_score",
                    "e3b2_score",
                    "projection_audit",
                )
            },
            "b100_count": report.get("b100", {}).get("count"),
            "promotion": report.get("promotion"),
            "failures": report.get("failures"),
        }, ensure_ascii=False, indent=2))
    return 0 if report["research_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
