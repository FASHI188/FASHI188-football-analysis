#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import evaluate_v511_fixed100_market_residual_r34 as r34
import evaluate_v511_fixed100_score_matrix_r35 as r35

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = r35.OUTCOMES


class StudyError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StudyError(f"JSON root must be object: {path}")
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def ah_market(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        ("AHCh", "PCAHH", "PCAHA", "Pinnacle_closing"),
        ("AHCh", "AvgCAHH", "AvgCAHA", "Average_closing"),
        ("AHCh", "B365CAHH", "B365CAHA", "Bet365_closing"),
        ("AHCh", "MaxCAHH", "MaxCAHA", "Maximum_closing"),
        ("AHh", "PAHH", "PAHA", "Pinnacle_opening"),
        ("AHh", "AvgAHH", "AvgAHA", "Average_opening"),
        ("AHh", "B365AHH", "B365AHA", "Bet365_opening"),
    ]
    for line_key, home_key, away_key, provider in candidates:
        line = r35.sf(row.get(line_key))
        home_odds = r35.sf(row.get(home_key))
        away_odds = r35.sf(row.get(away_key))
        if line is None or home_odds is None or away_odds is None:
            continue
        if home_odds <= 1.0 or away_odds <= 1.0:
            continue
        quarter = round(line * 4.0) / 4.0
        if abs(line - quarter) > 1e-8:
            continue
        fair_home, fair_away = r35.dv(home_odds, away_odds)
        return {
            "provider": provider,
            "line_source": line_key,
            "home_price_source": home_key,
            "away_price_source": away_key,
            "home_line": quarter,
            "home_odds": home_odds,
            "away_odds": away_odds,
            "fair_home": fair_home,
            "fair_away": fair_away,
        }
    return None


def split_quarter_line(line: float) -> tuple[float, ...]:
    quarter = round(line * 4.0) / 4.0
    if abs(quarter - line) > 1e-8:
        raise StudyError(f"non-quarter Asian line: {line}")
    doubled = quarter * 2.0
    if abs(doubled - round(doubled)) <= 1e-8:
        return (quarter,)
    return (math.floor(doubled) / 2.0, math.ceil(doubled) / 2.0)


def net_return(margin: int, line: float, decimal_odds: float) -> float:
    values = []
    for component in split_quarter_line(line):
        settled = margin + component
        if settled > 1e-10:
            values.append(decimal_odds - 1.0)
        elif settled < -1e-10:
            values.append(-1.0)
        else:
            values.append(0.0)
    return sum(values) / len(values)


def payoff_vectors(ah: dict[str, Any]) -> tuple[dict[str, float], dict[str, float]]:
    line = float(ah["home_line"])
    fair_home_odds = 1.0 / float(ah["fair_home"])
    fair_away_odds = 1.0 / float(ah["fair_away"])
    margins = {"home": 1, "draw": 0, "away": -1}
    home = {
        outcome: net_return(margin, line, fair_home_odds)
        for outcome, margin in margins.items()
    }
    away = {
        outcome: net_return(-margin, -line, fair_away_odds)
        for outcome, margin in margins.items()
    }
    return home, away


def expected_value(probabilities: dict[str, float], payoff: dict[str, float]) -> float:
    return sum(probabilities[outcome] * payoff[outcome] for outcome in OUTCOMES)


def exponential_tilt_zero(
    prior: dict[str, float], payoff: dict[str, float]
) -> tuple[dict[str, float], dict[str, Any]]:
    normalized = r35.normm(prior)
    if min(payoff.values()) > 0.0 or max(payoff.values()) < 0.0:
        raise StudyError("zero Asian expected return outside payoff support")

    def tilted(lam: float) -> tuple[dict[str, float], float]:
        exponents = {outcome: lam * payoff[outcome] for outcome in OUTCOMES}
        shift = max(exponents.values())
        weights = {
            outcome: normalized[outcome] * math.exp(exponents[outcome] - shift)
            for outcome in OUTCOMES
        }
        total = sum(weights.values())
        probabilities = {outcome: weights[outcome] / total for outcome in OUTCOMES}
        return probabilities, expected_value(probabilities, payoff)

    probabilities, residual = tilted(0.0)
    if abs(residual) <= 1e-14:
        return probabilities, {
            "lambda": 0.0,
            "iterations": 0,
            "home_fair_ev": residual,
            "converged": True,
        }

    lower, upper = -1.0, 1.0
    _, lower_value = tilted(lower)
    _, upper_value = tilted(upper)
    expansions = 0
    while not (lower_value <= 0.0 <= upper_value):
        lower *= 2.0
        upper *= 2.0
        _, lower_value = tilted(lower)
        _, upper_value = tilted(upper)
        expansions += 1
        if expansions > 60:
            raise StudyError("unable to bracket Asian expected-return root")

    iterations = 0
    for iterations in range(1, 121):
        midpoint = (lower + upper) / 2.0
        probabilities, residual = tilted(midpoint)
        if abs(residual) <= 1e-13:
            lower = upper = midpoint
            break
        if residual < 0.0:
            lower = midpoint
        else:
            upper = midpoint

    lam = (lower + upper) / 2.0
    probabilities, residual = tilted(lam)
    return probabilities, {
        "lambda": lam,
        "iterations": iterations,
        "home_fair_ev": residual,
        "converged": abs(residual) <= 1e-11,
    }


def ah_implied_target(
    prior_outcomes: dict[str, float], ah: dict[str, Any]
) -> tuple[dict[str, float], dict[str, Any]]:
    home_payoff, away_payoff = payoff_vectors(ah)
    target, audit = exponential_tilt_zero(prior_outcomes, home_payoff)
    away_residual = expected_value(target, away_payoff)
    audit.update(
        {
            "away_fair_ev": away_residual,
            "home_payoff": home_payoff,
            "away_payoff": away_payoff,
        }
    )
    if not audit["converged"] or abs(away_residual) > 1e-10:
        raise StudyError(
            "Asian fair-return projection failed: "
            f"home={audit['home_fair_ev']} away={away_residual}"
        )
    return target, audit


def candidate(
    prior: dict[str, float],
    market: dict[str, float],
    ou25: dict[str, Any],
    ah: dict[str, Any],
    spec: dict[str, Any],
) -> tuple[dict[str, float], dict[str, Any]]:
    matrix = prior
    steps: list[dict[str, Any]] = []

    ou_weight = float(spec.get("ou_weight", 0.0))
    if ou_weight:
        current = r35.aggo(matrix)
        target = r35.gtarget(
            current,
            {"over": float(ou25["over"]), "under": float(ou25["under"])},
            ou_weight,
        )
        before = matrix
        matrix = r35.proj(
            matrix,
            lambda state: "over" if r35.over(state) else "under",
            target,
        )
        fitted = r35.aggo(matrix)
        steps.append(
            {
                "type": "ou25",
                "weight": ou_weight,
                "kl": r35.kl(matrix, before),
                "partition_residual": max(
                    abs(fitted[key] - target[key]) for key in target
                ),
            }
        )

    x1_weight = float(spec.get("x1_weight", 0.0))
    if x1_weight:
        current = r35.agg1(matrix)
        target = r35.gtarget(current, market, x1_weight)
        before = matrix
        matrix = r35.proj(matrix, r35.state_out, target)
        fitted = r35.agg1(matrix)
        steps.append(
            {
                "type": "1x2",
                "weight": x1_weight,
                "kl": r35.kl(matrix, before),
                "partition_residual": max(
                    abs(fitted[key] - target[key]) for key in target
                ),
            }
        )

    ah_weight = float(spec.get("ah_weight", 0.0))
    exact_ah_audit: dict[str, Any] | None = None
    if ah_weight:
        current = r35.agg1(matrix)
        exact_target, exact_ah_audit = ah_implied_target(current, ah)
        target = r35.gtarget(current, exact_target, ah_weight)
        before = matrix
        matrix = r35.proj(matrix, r35.state_out, target)
        fitted = r35.agg1(matrix)
        home_payoff, away_payoff = payoff_vectors(ah)
        steps.append(
            {
                "type": "asian_handicap",
                "weight": ah_weight,
                "line": float(ah["home_line"]),
                "kl": r35.kl(matrix, before),
                "partition_residual": max(
                    abs(fitted[key] - target[key]) for key in target
                ),
                "final_home_fair_ev": expected_value(fitted, home_payoff),
                "final_away_fair_ev": expected_value(fitted, away_payoff),
                "exact_target_home_fair_ev": exact_ah_audit["home_fair_ev"],
                "exact_target_away_fair_ev": exact_ah_audit["away_fair_ev"],
            }
        )

    return matrix, {
        "sum": sum(matrix.values()),
        "max_partition_residual": max(
            [float(step["partition_residual"]) for step in steps] or [0.0]
        ),
        "exact_ah_converged": (
            exact_ah_audit is None or bool(exact_ah_audit["converged"])
        ),
        "steps": steps,
    }


def manifest(out_dir: Path) -> None:
    files = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            files.append(
                {
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "sha256": r35.hfile(path),
                }
            )
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema": "r36-manifest", "files": files}, indent=2),
        encoding="utf-8",
    )


def line_bucket(line: float) -> str:
    return f"{line:+.2f}"


def run(config: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    benchmark = r35.load(ROOT / config["source_benchmark"])
    market_pool, exclusions, source_rows = r34.prepare_market_pool(benchmark)
    enriched, source_audit = r35.pre_source(market_pool)
    for row in enriched:
        row["ah_market"] = ah_market(row["_src"])

    sample_contract = config["sample_contract"]
    r34_ids, _ = r34.fixed_sample(
        enriched, 100, int(sample_contract["excluded_r34_seed"])
    )
    r35_pool = [
        row
        for row in enriched
        if row["_identity"] not in r34_ids and row["ou25"] is not None
    ]
    r35_ids, _ = r34.fixed_sample(
        r35_pool, 100, int(sample_contract["excluded_r35_seed"])
    )
    excluded_ids = r34_ids | r35_ids

    max_abs_line = float(sample_contract["max_abs_home_handicap"])
    available = [
        row
        for row in enriched
        if row["_identity"] not in excluded_ids
        and row["ou25"] is not None
        and row["ah_market"] is not None
        and abs(float(row["ah_market"]["home_line"])) <= max_abs_line + 1e-12
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    prelabel_line_distribution = dict(
        sorted(
            Counter(
                line_bucket(float(row["ah_market"]["home_line"]))
                for row in available
            ).items()
        )
    )
    prelabel_provider_distribution = dict(
        sorted(Counter(str(row["ah_market"]["provider"]) for row in available).items())
    )

    if len(available) < 100:
        result = {
            "schema_version": config["schema_version"],
            "status": "STOP_INSUFFICIENT_SHALLOW_AH_SAMPLE_BEFORE_LABELS",
            "source": {
                "source_rows": source_rows,
                "market_complete_rows": len(enriched),
                "external_collection": 0,
                "provider_requests": 0,
            },
            "field_audit": source_audit,
            "sample": {
                "target": 100,
                "eligible_remaining": len(available),
                "score_labels_parsed": 0,
                "prelabel_line_distribution": prelabel_line_distribution,
                "prelabel_provider_distribution": prelabel_provider_distribution,
            },
            "hard_limits": config["hard_limits"],
        }
        (out_dir / "status.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest(out_dir)
        return result

    selected_ids, quota = r34.fixed_sample(
        available, 100, int(sample_contract["seed"])
    )
    identity_sha256 = sha256_bytes(
        ("\n".join(sorted(selected_ids)) + "\n").encode("utf-8")
    )

    labeled = r35.labels(enriched)
    labeled.sort(
        key=lambda row: (
            str(row["date"]),
            str(row["competition_id"]),
            str(row["home_team"]),
            str(row["away_team"]),
        )
    )
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in labeled:
        by_day[str(row["date"])[:10]].append(row)

    model = r35.Model(config["model"])
    specs = config["candidate_contract"]["fixed_candidates"]
    predictions: list[dict[str, Any]] = []

    for day in sorted(by_day):
        frozen_updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in by_day[day]:
            features = model.f(row)
            prior, prior_total = model.pred(features)
            if row["_identity"] in selected_ids:
                ah = row["ah_market"]
                if row["ou25"] is None or ah is None:
                    raise StudyError("selected row lost pre-label market eligibility")
                record: dict[str, Any] = {
                    "id": row["_identity"],
                    "competition_id": row["competition_id"],
                    "season": row.get("season"),
                    "date": row["date"],
                    "home_team": row["home_team"],
                    "away_team": row["away_team"],
                    "hg": row["hg"],
                    "ag": row["ag"],
                    "actual": row["actual"],
                    "market": features["m"],
                    "ou25": row["ou25"],
                    "ah": ah,
                    "prior_total": prior_total,
                }
                for spec in specs:
                    matrix, audit = candidate(
                        prior, features["m"], row["ou25"], ah, spec
                    )
                    name = spec["name"]
                    record[f"m_{name}"] = matrix
                    record[f"p_{name}"] = r35.agg1(matrix)
                    record[f"a_{name}"] = audit
                predictions.append(record)
            frozen_updates.append((row, features))
        for row, features in frozen_updates:
            model.update(row, features)

    rows = predictions
    if len(rows) != 100:
        raise StudyError(f"selected prediction count mismatch: {len(rows)}")

    metrics: dict[str, Any] = {"market": r35.metrics(rows, "market")}
    joint_metrics: dict[str, Any] = {}
    bootstrap: dict[str, Any] = {}
    gates: dict[str, Any] = {}
    promising: list[str] = []
    bootstrap_cfg = config["evaluation"]["paired_bootstrap"]

    for index, spec in enumerate(specs):
        name = spec["name"]
        metrics[name] = r35.metrics(rows, f"p_{name}")
        joint_metrics[name] = r35.joint(rows, f"m_{name}")
        bootstrap[name] = r35.boot(
            rows,
            f"p_{name}",
            int(bootstrap_cfg["samples"]),
            int(bootstrap_cfg["seed"]) + index,
        )
        gate = {
            "accuracy_better": metrics[name]["accuracy"] > metrics["market"]["accuracy"],
            "log_loss_nonworse": metrics[name]["log_loss"] <= metrics["market"]["log_loss"] + 1e-12,
            "brier_nonworse": metrics[name]["brier"] <= metrics["market"]["brier"] + 1e-12,
            "rps_nonworse": metrics[name]["rps"] <= metrics["market"]["rps"] + 1e-12,
            "accuracy_p05_positive": bootstrap[name]["accuracy"]["p05"] > 0.0,
            "draw_exists": metrics[name]["predicted_draw"] > 0,
        }
        gate["passed"] = all(gate.values())
        gates[name] = gate
        if gate["passed"] and float(spec.get("ah_weight", 0.0)) > 0.0:
            promising.append(name)

    coordination_audit = {}
    for spec in specs:
        name = spec["name"]
        candidate_audits = [row[f"a_{name}"] for row in rows]
        ah_steps = [
            step
            for audit in candidate_audits
            for step in audit["steps"]
            if step["type"] == "asian_handicap"
        ]
        coordination_audit[name] = {
            "all_sum_one": all(abs(audit["sum"] - 1.0) <= 1e-12 for audit in candidate_audits),
            "max_partition_residual": max(audit["max_partition_residual"] for audit in candidate_audits),
            "all_exact_ah_converged": all(audit["exact_ah_converged"] for audit in candidate_audits),
            "max_exact_ah_home_ev_abs": max([abs(step["exact_target_home_fair_ev"]) for step in ah_steps] or [0.0]),
            "max_exact_ah_away_ev_abs": max([abs(step["exact_target_away_fair_ev"]) for step in ah_steps] or [0.0]),
            "max_final_ah_home_ev_abs": max([abs(step["final_home_fair_ev"]) for step in ah_steps] or [0.0]),
        }

    selected_line_distribution = dict(
        sorted(Counter(line_bucket(float(row["ah"]["home_line"])) for row in rows).items())
    )
    selected_provider_distribution = dict(
        sorted(Counter(str(row["ah"]["provider"]) for row in rows).items())
    )

    status = (
        "PROMISING_AH_SCORE_MATRIX_SIGNAL_EXPLORATION_ONLY"
        if promising
        else "NO_AH_SCORE_MATRIX_INCREMENT_FIXED100_EXPLORATION_ONLY"
    )
    result = {
        "schema_version": config["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "classification": config["classification"],
        "source": {
            "source_rows": source_rows,
            "market_complete_rows": len(enriched),
            "pre_result_exclusions": exclusions,
            "external_collection": 0,
            "provider_requests": 0,
            "closing_prices_without_original_quote_timestamp": True,
        },
        "field_audit": {
            **source_audit,
            "shallow_ah_prelabel_pool": len(available),
            "prelabel_line_distribution": prelabel_line_distribution,
            "prelabel_provider_distribution": prelabel_provider_distribution,
        },
        "sample": {
            "rows": 100,
            "pool_rows": len(available),
            "r34_excluded_count": len(r34_ids),
            "r35_excluded_count": len(r35_ids),
            "r34_overlap_rows": len(selected_ids & r34_ids),
            "r35_overlap_rows": len(selected_ids & r35_ids),
            "seed": sample_contract["seed"],
            "quota": quota,
            "identity_sha256": identity_sha256,
            "selection_uses_score_labels": False,
            "selection_frozen_before_score_label_parsing": True,
            "no_resampling_after_result": True,
            "max_abs_home_handicap": max_abs_line,
            "selected_line_distribution": selected_line_distribution,
            "selected_provider_distribution": selected_provider_distribution,
            "actual_distribution": dict(Counter(row["actual"] for row in rows)),
        },
        "architecture": {
            "direct_total_goals_track": True,
            "conditional_goal_difference_track": True,
            "unified_score_lattice": True,
            "tail": ["7+_H", "7+_D", "7+_A"],
            "shallow_ah_only_for_exact_tail_settlement": True,
            "asian_handicap_fair_return_constraint": True,
            "asian_quarter_line_split_settlement": True,
            "same_day_freeze": True,
            "poisson_used": False,
            "manual_expected_goals_used": False,
            "manual_draw_offset_used": False,
        },
        "market_coordination": {
            "objective": "direct score prior with sequential KL/I-projection for O/U 2.5, 1X2 and Asian fair-return target",
            "order": ["ou25", "1x2", "asian_handicap"],
            "candidate_specs": specs,
            "audit": coordination_audit,
        },
        "metrics": metrics,
        "joint_metrics": joint_metrics,
        "paired_bootstrap": bootstrap,
        "gates": gates,
        "promising_candidates": promising,
        "hard_limits": config["hard_limits"],
    }

    (out_dir / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    with (out_dir / "candidate_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "candidate", "hits", "accuracy", "log_loss", "brier", "rps",
            "predicted_draw", "draw_precision", "draw_recall", "draw_f1",
            "joint_top1_accuracy", "total_top1_accuracy",
            "bootstrap_accuracy_p05", "gate_passed",
        ])
        for name, metric in metrics.items():
            writer.writerow([
                name, metric.get("hits"), metric.get("accuracy"), metric.get("log_loss"),
                metric.get("brier"), metric.get("rps"), metric.get("predicted_draw"),
                metric.get("draw_precision"), metric.get("draw_recall"), metric.get("draw_f1"),
                joint_metrics.get(name, {}).get("joint_top1_accuracy"),
                joint_metrics.get(name, {}).get("total_top1_accuracy"),
                bootstrap.get(name, {}).get("accuracy", {}).get("p05"),
                gates.get(name, {}).get("passed"),
            ])

    fields = [
        "id", "competition_id", "season", "date", "home_team", "away_team",
        "hg", "ag", "actual", "ou_provider", "ou_over", "ah_provider",
        "ah_home_line", "ah_home_odds", "ah_away_odds", "market_pick",
    ] + [
        field
        for spec in specs
        for field in (
            f"{spec['name']}_pick", f"{spec['name']}_draw", f"{spec['name']}_top_score"
        )
    ]
    with (out_dir / "fixed100_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {key: row[key] for key in (
                "id", "competition_id", "season", "date", "home_team", "away_team",
                "hg", "ag", "actual",
            )}
            output.update({
                "ou_provider": row["ou25"]["provider"],
                "ou_over": row["ou25"]["over"],
                "ah_provider": row["ah"]["provider"],
                "ah_home_line": row["ah"]["home_line"],
                "ah_home_odds": row["ah"]["home_odds"],
                "ah_away_odds": row["ah"]["away_odds"],
                "market_pick": max(OUTCOMES, key=lambda outcome: row["market"][outcome]),
            })
            for spec in specs:
                name = spec["name"]
                output[f"{name}_pick"] = max(
                    OUTCOMES, key=lambda outcome: row[f"p_{name}"][outcome]
                )
                output[f"{name}_draw"] = row[f"p_{name}"]["draw"]
                output[f"{name}_top_score"] = max(
                    row[f"m_{name}"], key=row[f"m_{name}"].get
                )
            writer.writerow(output)

    with (out_dir / "field_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["competition", "rows", "ou25", "asian"])
        for competition, values in source_audit["by_competition"].items():
            writer.writerow([
                competition, values.get("rows", 0), values.get("ou25", 0),
                values.get("asian", 0),
            ])

    with (out_dir / "ah_line_audit.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scope", "home_line", "rows"])
        for line, count in prelabel_line_distribution.items():
            writer.writerow(["prelabel_pool", line, count])
        for line, count in selected_line_distribution.items():
            writer.writerow(["fixed100", line, count])

    manifest(out_dir)
    return result


def self_test() -> None:
    assert split_quarter_line(-0.25) == (-0.5, 0.0)
    assert split_quarter_line(0.25) == (0.0, 0.5)
    assert abs(net_return(0, -0.25, 2.0) + 0.5) <= 1e-12
    assert abs(net_return(0, 0.25, 2.0) - 0.5) <= 1e-12

    ah = {"home_line": -0.25, "fair_home": 0.51, "fair_away": 0.49}
    target, audit = ah_implied_target(
        {"home": 0.42, "draw": 0.29, "away": 0.29}, ah
    )
    assert abs(sum(target.values()) - 1.0) <= 1e-12
    assert abs(audit["home_fair_ev"]) <= 1e-11
    assert abs(audit["away_fair_ev"]) <= 1e-10

    prior_matrix = r35.normm({
        "0-0": 0.12, "1-0": 0.20, "0-1": 0.14,
        "1-1": 0.18, "2-1": 0.20, "1-2": 0.16,
    })
    matrix, candidate_audit = candidate(
        prior_matrix,
        {"home": 0.44, "draw": 0.29, "away": 0.27},
        {"over": 0.46, "under": 0.54},
        ah,
        {"name": "test", "ou_weight": 1.0, "x1_weight": 0.75, "ah_weight": 1.0},
    )
    assert abs(sum(matrix.values()) - 1.0) <= 1e-12
    assert candidate_audit["max_partition_residual"] <= 1e-12
    assert candidate_audit["exact_ah_converged"] is True
    print("PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path,
        default=ROOT / "config/v511_fixed100_ah_score_matrix_r36.json",
    )
    parser.add_argument(
        "--out-dir", type=Path,
        default=ROOT / "manifests/v511_fixed100_ah_score_matrix_r36",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(load_json(args.config), args.out_dir)
    print(json.dumps({
        "status": result["status"],
        "sample": result.get("sample"),
        "field": result.get("field_audit", {}).get("availability"),
        "metrics": result.get("metrics"),
        "promising": result.get("promising_candidates"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
