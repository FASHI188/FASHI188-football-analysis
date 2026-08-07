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
import evaluate_v511_fixed100_ah_score_matrix_r36 as r36

ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = r35.OUTCOMES
EPS = 1e-15


class StudyError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StudyError(f"JSON root must be object: {path}")
    return value


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def de_vig_three(odds: tuple[float, float, float]) -> dict[str, float]:
    inverses = [1.0 / value for value in odds]
    total = sum(inverses)
    return {
        outcome: value / total
        for outcome, value in zip(OUTCOMES, inverses)
    }


def de_vig_two(first: float, second: float) -> tuple[float, float]:
    a, b = 1.0 / first, 1.0 / second
    total = a + b
    return a / total, b / total


def valid_odds(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[float, ...] | None:
    values = tuple(r35.sf(row.get(key)) for key in keys)
    if any(value is None or value <= 1.0 for value in values):
        return None
    return tuple(float(value) for value in values)


def x1_trajectory(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        ("Pinnacle", ("PSH", "PSD", "PSA"), ("PSCH", "PSCD", "PSCA")),
        ("Bet365", ("B365H", "B365D", "B365A"), ("B365CH", "B365CD", "B365CA")),
        ("Average", ("AvgH", "AvgD", "AvgA"), ("AvgCH", "AvgCD", "AvgCA")),
        ("Maximum", ("MaxH", "MaxD", "MaxA"), ("MaxCH", "MaxCD", "MaxCA")),
    ]
    for provider, open_keys, close_keys in candidates:
        open_odds = valid_odds(row, open_keys)
        close_odds = valid_odds(row, close_keys)
        if open_odds is None or close_odds is None:
            continue
        return {
            "provider": provider,
            "open_keys": open_keys,
            "close_keys": close_keys,
            "open_odds": open_odds,
            "close_odds": close_odds,
            "open": de_vig_three(open_odds),
            "close": de_vig_three(close_odds),
        }
    return None


def ou_trajectory(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        ("Pinnacle", ("P>2.5", "P<2.5"), ("PC>2.5", "PC<2.5")),
        ("Bet365", ("B365>2.5", "B365<2.5"), ("B365C>2.5", "B365C<2.5")),
        ("Average", ("Avg>2.5", "Avg<2.5"), ("AvgC>2.5", "AvgC<2.5")),
        ("Maximum", ("Max>2.5", "Max<2.5"), ("MaxC>2.5", "MaxC<2.5")),
    ]
    for provider, open_keys, close_keys in candidates:
        open_odds = valid_odds(row, open_keys)
        close_odds = valid_odds(row, close_keys)
        if open_odds is None or close_odds is None:
            continue
        open_over, open_under = de_vig_two(*open_odds)
        close_over, close_under = de_vig_two(*close_odds)
        return {
            "provider": provider,
            "open_keys": open_keys,
            "close_keys": close_keys,
            "open_odds": open_odds,
            "close_odds": close_odds,
            "open": {"over": open_over, "under": open_under},
            "close": {"over": close_over, "under": close_under},
        }
    return None


def ah_trajectory(row: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        ("Pinnacle", "AHh", ("PAHH", "PAHA"), "AHCh", ("PCAHH", "PCAHA")),
        ("Bet365", "AHh", ("B365AHH", "B365AHA"), "AHCh", ("B365CAHH", "B365CAHA")),
        ("Average", "AHh", ("AvgAHH", "AvgAHA"), "AHCh", ("AvgCAHH", "AvgCAHA")),
        ("Maximum", "AHh", ("MaxAHH", "MaxAHA"), "AHCh", ("MaxCAHH", "MaxCAHA")),
    ]
    for provider, open_line_key, open_keys, close_line_key, close_keys in candidates:
        open_line = r35.sf(row.get(open_line_key))
        close_line = r35.sf(row.get(close_line_key))
        open_odds = valid_odds(row, open_keys)
        close_odds = valid_odds(row, close_keys)
        if open_line is None or close_line is None or open_odds is None or close_odds is None:
            continue
        open_home, open_away = de_vig_two(*open_odds)
        close_home, close_away = de_vig_two(*close_odds)
        return {
            "provider": provider,
            "open_line_key": open_line_key,
            "close_line_key": close_line_key,
            "open_keys": open_keys,
            "close_keys": close_keys,
            "open_line": float(open_line),
            "close_line": float(close_line),
            "open_odds": open_odds,
            "close_odds": close_odds,
            "open": {"home": open_home, "away": open_away},
            "close": {"home": close_home, "away": close_away},
        }
    return None


def project_snapshot(
    prior: dict[str, float],
    x1: dict[str, float],
    ou: dict[str, float],
) -> tuple[dict[str, float], dict[str, Any]]:
    matrix = r35.normm(prior)
    iterations = 0
    ou_residual = math.inf
    x1_residual = math.inf
    for iterations in range(1, 201):
        matrix = r35.proj(
            matrix,
            lambda state: "over" if r35.over(state) else "under",
            ou,
        )
        matrix = r35.proj(matrix, r35.state_out, x1)
        fitted_ou = r35.aggo(matrix)
        fitted_x1 = r35.agg1(matrix)
        ou_residual = max(abs(fitted_ou[key] - ou[key]) for key in ou)
        x1_residual = max(abs(fitted_x1[key] - x1[key]) for key in x1)
        if max(ou_residual, x1_residual) <= 1e-13:
            break
    return matrix, {
        "sum": sum(matrix.values()),
        "iterations": iterations,
        "converged": max(ou_residual, x1_residual) <= 1e-11,
        "ou_residual": ou_residual,
        "x1_residual": x1_residual,
    }


def probability_momentum(
    opening: dict[str, float],
    closing: dict[str, float],
    alpha: float,
) -> dict[str, float]:
    weights = {
        key: max(EPS, closing[key]) * (
            max(EPS, closing[key]) / max(EPS, opening[key])
        ) ** alpha
        for key in closing
    }
    total = sum(weights.values())
    return {key: value / total for key, value in weights.items()}


def matrix_momentum(
    opening: dict[str, float],
    closing: dict[str, float],
    alpha: float,
) -> dict[str, float]:
    weights = {
        state: max(EPS, closing[state]) * (
            max(EPS, closing[state]) / max(EPS, opening[state])
        ) ** alpha
        for state in closing
    }
    return r35.normm(weights)


def draw_auc(rows: list[dict[str, Any]], key: str) -> float | None:
    positives = [row for row in rows if row["actual"] == "draw"]
    negatives = [row for row in rows if row["actual"] != "draw"]
    if not positives or not negatives:
        return None
    wins = 0.0
    pairs = 0
    for positive in positives:
        ps = float(positive[key]["draw"])
        for negative in negatives:
            ns = float(negative[key]["draw"])
            pairs += 1
            if ps > ns:
                wins += 1.0
            elif ps == ns:
                wins += 0.5
    return wins / pairs


def reconstruct_exclusions(
    enriched: list[dict[str, Any]],
    contract: dict[str, Any],
) -> tuple[set[str], dict[str, int]]:
    r34_ids, _ = r34.fixed_sample(
        enriched, 100, int(contract["excluded_r34_seed"])
    )
    r35_pool = [
        row
        for row in enriched
        if row["_identity"] not in r34_ids and row["ou25"] is not None
    ]
    r35_ids, _ = r34.fixed_sample(
        r35_pool, 100, int(contract["excluded_r35_seed"])
    )
    for row in enriched:
        row["r36_ah_market"] = r36.ah_market(row["_src"])
    r36_pool = [
        row
        for row in enriched
        if row["_identity"] not in (r34_ids | r35_ids)
        and row["ou25"] is not None
        and row["r36_ah_market"] is not None
        and abs(float(row["r36_ah_market"]["home_line"])) <= 0.5 + 1e-12
    ]
    r36_ids, _ = r34.fixed_sample(
        r36_pool, 100, int(contract["excluded_r36_seed"])
    )
    return r34_ids | r35_ids | r36_ids, {
        "r34": len(r34_ids),
        "r35": len(r35_ids),
        "r36": len(r36_ids),
    }


def write_manifest(out_dir: Path) -> None:
    files = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            files.append({
                "name": path.name,
                "bytes": path.stat().st_size,
                "sha256": r35.hfile(path),
            })
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema": "r37-manifest", "files": files}, indent=2),
        encoding="utf-8",
    )


def run(config: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    benchmark = r35.load(ROOT / config["source_benchmark"])
    market_pool, exclusions, source_rows = r34.prepare_market_pool(benchmark)
    enriched, _source_audit = r35.pre_source(market_pool)
    audit = Counter()
    providers = {"x1": Counter(), "ou": Counter(), "ah": Counter()}
    for row in enriched:
        x1 = x1_trajectory(row["_src"])
        ou = ou_trajectory(row["_src"])
        ah = ah_trajectory(row["_src"])
        audit["x1_open_close"] += int(x1 is not None)
        audit["ou_open_close"] += int(ou is not None)
        audit["ah_open_close"] += int(ah is not None)
        if x1 is not None:
            providers["x1"][x1["provider"]] += 1
        if ou is not None:
            providers["ou"][ou["provider"]] += 1
        if ah is not None:
            providers["ah"][ah["provider"]] += 1
        row["_trajectory"] = (
            {"x1": x1, "ou": ou, "ah": ah}
            if x1 is not None and ou is not None and ah is not None
            else None
        )
        audit["complete_all_three"] += int(row["_trajectory"] is not None)

    contract = config["sample_contract"]
    excluded_ids, excluded_counts = reconstruct_exclusions(enriched, contract)
    available = [
        row
        for row in enriched
        if row["_identity"] not in excluded_ids
        and row["_trajectory"] is not None
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    if len(available) < 100:
        result = {
            "schema_version": config["schema_version"],
            "status": "STOP_INSUFFICIENT_OPEN_CLOSE_TRAJECTORY_SAMPLE_BEFORE_LABELS",
            "source": {
                "source_rows": source_rows,
                "market_complete_rows": len(enriched),
                "external_collection": 0,
                "provider_requests": 0,
            },
            "field_audit": {
                "availability": dict(audit),
                "providers": {
                    key: dict(sorted(value.items()))
                    for key, value in providers.items()
                },
            },
            "sample": {
                "target": 100,
                "eligible_remaining": len(available),
                "excluded_prior_fixed100": excluded_counts,
                "score_labels_parsed": 0,
            },
            "hard_limits": config["hard_limits"],
        }
        (out_dir / "status.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_manifest(out_dir)
        return result

    selected_ids, quota = r34.fixed_sample(
        available, 100, int(contract["seed"])
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
    predictions: list[dict[str, Any]] = []
    alphas = [float(value) for value in config["candidate_contract"]["alphas"]]

    for day in sorted(by_day):
        frozen_updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in by_day[day]:
            trajectory = row.get("_trajectory")
            feature_row = row
            if trajectory is not None:
                feature_row = dict(row)
                feature_row["market"] = {"probabilities": trajectory["x1"]["open"]}
            features = model.f(feature_row)
            prior, prior_total = model.pred(features)

            if row["_identity"] in selected_ids:
                if trajectory is None:
                    raise StudyError("selected row lost trajectory eligibility")
                x1_open = trajectory["x1"]["open"]
                x1_close = trajectory["x1"]["close"]
                ou_open = trajectory["ou"]["open"]
                ou_close = trajectory["ou"]["close"]
                open_matrix, open_audit = project_snapshot(prior, x1_open, ou_open)
                close_matrix, close_audit = project_snapshot(prior, x1_close, ou_close)
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
                    "market": x1_close,
                    "opening": x1_open,
                    "trajectory": trajectory,
                    "prior_total": prior_total,
                    "open_matrix": open_matrix,
                    "close_matrix": close_matrix,
                    "open_audit": open_audit,
                    "close_audit": close_audit,
                    "p_close_matrix": r35.agg1(close_matrix),
                }
                for alpha in alphas:
                    suffix = str(alpha).replace(".", "_")
                    direct = probability_momentum(x1_open, x1_close, alpha)
                    matrix = matrix_momentum(open_matrix, close_matrix, alpha)
                    record[f"p_x1_mom_{suffix}"] = direct
                    record[f"m_matrix_mom_{suffix}"] = matrix
                    record[f"p_matrix_mom_{suffix}"] = r35.agg1(matrix)
                predictions.append(record)
            frozen_updates.append((row, features))

        for row, features in frozen_updates:
            model.update(row, features)

    rows = predictions
    if len(rows) != 100:
        raise StudyError(f"selected prediction count mismatch: {len(rows)}")

    metrics: dict[str, Any] = {
        "market": r35.metrics(rows, "market"),
        "opening": r35.metrics(rows, "opening"),
        "close_matrix": r35.metrics(rows, "p_close_matrix"),
    }
    joint_metrics: dict[str, Any] = {
        "close_matrix": r35.joint(rows, "close_matrix")
    }
    bootstrap: dict[str, Any] = {}
    gates: dict[str, Any] = {}
    auc: dict[str, float | None] = {
        "market": draw_auc(rows, "market"),
        "opening": draw_auc(rows, "opening"),
        "close_matrix": draw_auc(rows, "p_close_matrix"),
    }
    candidate_keys: list[tuple[str, str]] = []
    for alpha in alphas:
        suffix = str(alpha).replace(".", "_")
        candidate_keys.extend([
            (f"x1_mom_{suffix}", f"p_x1_mom_{suffix}"),
            (f"matrix_mom_{suffix}", f"p_matrix_mom_{suffix}"),
        ])

    bootstrap_cfg = config["evaluation"]["paired_bootstrap"]
    promising: list[str] = []
    for index, (name, key) in enumerate(candidate_keys):
        metrics[name] = r35.metrics(rows, key)
        auc[name] = draw_auc(rows, key)
        if name.startswith("matrix_"):
            joint_metrics[name] = r35.joint(rows, "m_" + name)
        bootstrap[name] = r35.boot(
            rows,
            key,
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
        if gate["passed"]:
            promising.append(name)

    max_sum_error = max(
        max(
            abs(row["open_audit"]["sum"] - 1.0),
            abs(row["close_audit"]["sum"] - 1.0),
        )
        for row in rows
    )
    max_residual = max(
        max(
            row["open_audit"]["ou_residual"],
            row["open_audit"]["x1_residual"],
            row["close_audit"]["ou_residual"],
            row["close_audit"]["x1_residual"],
        )
        for row in rows
    )
    all_converged = all(
        row["open_audit"]["converged"] and row["close_audit"]["converged"]
        for row in rows
    )

    selected_provider_distribution = {
        market: dict(sorted(Counter(
            str(row["trajectory"][market]["provider"])
            for row in rows
        ).items()))
        for market in ("x1", "ou", "ah")
    }
    ah_line_moves = Counter(
        f"{row['trajectory']['ah']['open_line']:+.2f}->{row['trajectory']['ah']['close_line']:+.2f}"
        for row in rows
    )
    status = (
        "PROMISING_OPEN_CLOSE_TRAJECTORY_SIGNAL_EXPLORATION_ONLY"
        if promising
        else "NO_OPEN_CLOSE_TRAJECTORY_INCREMENT_FIXED100_EXPLORATION_ONLY"
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
            "opening_and_closing_columns_without_original_quote_timestamps": True,
        },
        "field_audit": {
            "availability": dict(audit),
            "providers": {
                key: dict(sorted(value.items()))
                for key, value in providers.items()
            },
        },
        "sample": {
            "rows": 100,
            "pool_rows": len(available),
            "excluded_prior_fixed100": excluded_counts,
            "prior_overlap_rows": len(selected_ids & excluded_ids),
            "seed": contract["seed"],
            "quota": quota,
            "identity_sha256": identity_sha256,
            "selection_uses_score_labels": False,
            "selection_frozen_before_score_label_parsing": True,
            "no_resampling_after_result": True,
            "selected_provider_distribution": selected_provider_distribution,
            "selected_ah_line_moves": dict(sorted(ah_line_moves.items())),
            "actual_distribution": dict(Counter(row["actual"] for row in rows)),
        },
        "architecture": {
            "opening_to_closing_proxy": True,
            "original_quote_timestamps_available": False,
            "direct_x1_probability_momentum": True,
            "ou25_x1_unified_score_matrix_momentum": True,
            "asian_open_close_used_for_eligibility_and_diagnostics": True,
            "asian_settlement_projection_used": False,
            "same_day_freeze": True,
            "poisson_used": False,
            "manual_draw_offset_used": False,
            "manual_draw_quota_used": False,
        },
        "candidate_contract": {
            "alphas": alphas,
            "formula": "close * (close/open)^alpha, normalized",
            "matrix_formula": "close_score_cell * (close_score_cell/open_score_cell)^alpha, normalized",
        },
        "coordination_audit": {
            "all_snapshot_projections_converged": all_converged,
            "max_probability_sum_error": max_sum_error,
            "max_market_partition_residual": max_residual,
        },
        "metrics": metrics,
        "joint_metrics": joint_metrics,
        "paired_bootstrap": bootstrap,
        "gates": gates,
        "post_result_draw_auc_diagnostic": auc,
        "promising_candidates": promising,
        "hard_limits": config["hard_limits"],
    }

    (out_dir / "status.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (out_dir / "candidate_summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "candidate", "hits", "accuracy", "log_loss", "brier", "rps",
            "predicted_draw", "draw_precision", "draw_recall", "draw_f1",
            "draw_auc", "bootstrap_accuracy_p05", "gate_passed",
        ])
        for name, metric in metrics.items():
            writer.writerow([
                name,
                metric.get("hits"),
                metric.get("accuracy"),
                metric.get("log_loss"),
                metric.get("brier"),
                metric.get("rps"),
                metric.get("predicted_draw"),
                metric.get("draw_precision"),
                metric.get("draw_recall"),
                metric.get("draw_f1"),
                auc.get(name),
                bootstrap.get(name, {}).get("accuracy", {}).get("p05"),
                gates.get(name, {}).get("passed"),
            ])

    prediction_fields = [
        "id", "competition_id", "season", "date",
        "home_team", "away_team", "hg", "ag", "actual",
        "x1_provider", "ou_provider", "ah_provider",
        "open_home", "open_draw", "open_away",
        "close_home", "close_draw", "close_away",
        "open_over", "close_over",
        "ah_open_line", "ah_close_line",
        "market_pick",
    ] + [
        field
        for name, _ in candidate_keys
        for field in (f"{name}_pick", f"{name}_draw")
    ]
    with (out_dir / "fixed100_predictions.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=prediction_fields)
        writer.writeheader()
        for row in rows:
            trajectory = row["trajectory"]
            output = {
                key: row[key]
                for key in (
                    "id", "competition_id", "season", "date",
                    "home_team", "away_team", "hg", "ag", "actual",
                )
            }
            output.update({
                "x1_provider": trajectory["x1"]["provider"],
                "ou_provider": trajectory["ou"]["provider"],
                "ah_provider": trajectory["ah"]["provider"],
                "open_home": row["opening"]["home"],
                "open_draw": row["opening"]["draw"],
                "open_away": row["opening"]["away"],
                "close_home": row["market"]["home"],
                "close_draw": row["market"]["draw"],
                "close_away": row["market"]["away"],
                "open_over": trajectory["ou"]["open"]["over"],
                "close_over": trajectory["ou"]["close"]["over"],
                "ah_open_line": trajectory["ah"]["open_line"],
                "ah_close_line": trajectory["ah"]["close_line"],
                "market_pick": max(
                    OUTCOMES, key=lambda outcome: row["market"][outcome]
                ),
            })
            for name, key in candidate_keys:
                output[f"{name}_pick"] = max(
                    OUTCOMES, key=lambda outcome: row[key][outcome]
                )
                output[f"{name}_draw"] = row[key]["draw"]
            writer.writerow(output)

    with (out_dir / "trajectory_field_audit.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["market", "provider", "rows"])
        for market, counts in providers.items():
            for provider, count in sorted(counts.items()):
                writer.writerow([market, provider, count])

    write_manifest(out_dir)
    return result


def self_test() -> None:
    opening = {"home": 0.40, "draw": 0.30, "away": 0.30}
    closing = {"home": 0.45, "draw": 0.28, "away": 0.27}
    momentum = probability_momentum(opening, closing, 0.5)
    assert abs(sum(momentum.values()) - 1.0) <= 1e-12
    assert momentum["home"] > closing["home"]

    prior = r35.normm({
        "0-0": 0.12,
        "1-0": 0.20,
        "0-1": 0.14,
        "1-1": 0.18,
        "2-1": 0.20,
        "1-2": 0.16,
    })
    open_matrix, open_audit = project_snapshot(
        prior,
        opening,
        {"over": 0.45, "under": 0.55},
    )
    close_matrix, close_audit = project_snapshot(
        prior,
        closing,
        {"over": 0.50, "under": 0.50},
    )
    projected = matrix_momentum(open_matrix, close_matrix, 0.5)
    assert abs(sum(projected.values()) - 1.0) <= 1e-12
    assert open_audit["converged"] is True
    assert close_audit["converged"] is True
    assert open_audit["x1_residual"] <= 1e-11
    assert close_audit["x1_residual"] <= 1e-11
    print("PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "config/v511_fixed100_market_trajectory_r37.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=ROOT / "manifests/v511_fixed100_market_trajectory_r37",
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
        "availability": result.get("field_audit", {}).get("availability"),
        "metrics": result.get("metrics"),
        "promising": result.get("promising_candidates"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
