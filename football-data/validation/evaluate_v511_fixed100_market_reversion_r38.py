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
import evaluate_v511_fixed100_market_trajectory_r37 as r37

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


def logit(p: float) -> float:
    p = min(1.0 - EPS, max(EPS, p))
    return math.log(p / (1.0 - p))


def logistic(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def geometric_revert(opening: dict[str, float], closing: dict[str, float], weight: float) -> dict[str, float]:
    values = {
        key: max(EPS, closing[key]) ** (1.0 - weight) * max(EPS, opening[key]) ** weight
        for key in closing
    }
    total = sum(values.values())
    return {key: value / total for key, value in values.items()}


def draw_only_revert(opening: dict[str, float], closing: dict[str, float], weight: float) -> dict[str, float]:
    draw = logistic((1.0 - weight) * logit(closing["draw"]) + weight * logit(opening["draw"]))
    nondraw = 1.0 - draw
    ha = closing["home"] + closing["away"]
    if ha <= 0:
        raise StudyError("invalid closing home-away mass")
    home_share = closing["home"] / ha
    return {
        "home": nondraw * home_share,
        "draw": draw,
        "away": nondraw * (1.0 - home_share),
    }


def matrix_revert(opening: dict[str, float], closing: dict[str, float], weight: float) -> dict[str, float]:
    values = {
        state: max(EPS, closing[state]) ** (1.0 - weight) * max(EPS, opening[state]) ** weight
        for state in closing
    }
    return r35.normm(values)


def setup_trajectory(enriched: list[dict[str, Any]]) -> dict[str, Any]:
    audit = Counter()
    providers = {"x1": Counter(), "ou": Counter(), "ah": Counter()}
    for row in enriched:
        x1 = r37.x1_trajectory(row["_src"])
        ou = r37.ou_trajectory(row["_src"])
        ah = r37.ah_trajectory(row["_src"])
        audit["x1_open_close"] += int(x1 is not None)
        audit["ou_open_close"] += int(ou is not None)
        audit["ah_open_close"] += int(ah is not None)
        if x1 is not None:
            providers["x1"][x1["provider"]] += 1
        if ou is not None:
            providers["ou"][ou["provider"]] += 1
        if ah is not None:
            providers["ah"][ah["provider"]] += 1
        row["_trajectory"] = {"x1": x1, "ou": ou, "ah": ah} if x1 and ou and ah else None
        audit["complete_all_three"] += int(row["_trajectory"] is not None)
    return {
        "availability": dict(audit),
        "providers": {k: dict(sorted(v.items())) for k, v in providers.items()},
    }


def reconstruct_exclusions(enriched: list[dict[str, Any]], contract: dict[str, Any]) -> tuple[set[str], dict[str, int]]:
    prior_ids, counts = r37.reconstruct_exclusions(enriched, contract)
    r37_pool = [row for row in enriched if row["_identity"] not in prior_ids and row["_trajectory"] is not None]
    r37_ids, _ = r34.fixed_sample(r37_pool, 100, int(contract["excluded_r37_seed"]))
    counts = {**counts, "r37": len(r37_ids)}
    return prior_ids | r37_ids, counts


def draw_auc(rows: list[dict[str, Any]], key: str) -> float | None:
    return r37.draw_auc(rows, key)


def write_manifest(out_dir: Path) -> None:
    files = []
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": r35.hfile(path)})
    (out_dir / "manifest.json").write_text(
        json.dumps({"schema": "r38-manifest", "files": files}, indent=2), encoding="utf-8"
    )


def run(config: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    benchmark = r35.load(ROOT / config["source_benchmark"])
    market_pool, exclusions, source_rows = r34.prepare_market_pool(benchmark)
    enriched, _source_audit = r35.pre_source(market_pool)
    field_audit = setup_trajectory(enriched)

    contract = config["sample_contract"]
    excluded_ids, excluded_counts = reconstruct_exclusions(enriched, contract)
    available = [row for row in enriched if row["_identity"] not in excluded_ids and row["_trajectory"] is not None]

    out_dir.mkdir(parents=True, exist_ok=True)
    if len(available) < 100:
        result = {
            "schema_version": config["schema_version"],
            "status": "STOP_INSUFFICIENT_REVERSION_SAMPLE_BEFORE_LABELS",
            "source": {"source_rows": source_rows, "market_complete_rows": len(enriched), "external_collection": 0, "provider_requests": 0},
            "field_audit": field_audit,
            "sample": {"target": 100, "eligible_remaining": len(available), "excluded_prior_fixed100": excluded_counts, "score_labels_parsed": 0},
            "hard_limits": config["hard_limits"],
        }
        (out_dir / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        write_manifest(out_dir)
        return result

    selected_ids, quota = r34.fixed_sample(available, 100, int(contract["seed"]))
    identity_sha256 = sha256_bytes(("\n".join(sorted(selected_ids)) + "\n").encode("utf-8"))

    labeled = r35.labels(enriched)
    labeled.sort(key=lambda row: (str(row["date"]), str(row["competition_id"]), str(row["home_team"]), str(row["away_team"])))
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in labeled:
        by_day[str(row["date"])[:10]].append(row)

    model = r35.Model(config["model"])
    weights = [float(value) for value in config["candidate_contract"]["weights"]]
    predictions: list[dict[str, Any]] = []

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
                opening = trajectory["x1"]["open"]
                closing = trajectory["x1"]["close"]
                open_matrix, open_audit = r37.project_snapshot(prior, opening, trajectory["ou"]["open"])
                close_matrix, close_audit = r37.project_snapshot(prior, closing, trajectory["ou"]["close"])
                record: dict[str, Any] = {
                    "id": row["_identity"], "competition_id": row["competition_id"], "season": row.get("season"), "date": row["date"],
                    "home_team": row["home_team"], "away_team": row["away_team"], "hg": row["hg"], "ag": row["ag"], "actual": row["actual"],
                    "market": closing, "opening": opening, "trajectory": trajectory, "prior_total": prior_total,
                    "open_matrix": open_matrix, "close_matrix": close_matrix, "open_audit": open_audit, "close_audit": close_audit,
                }
                for weight in weights:
                    suffix = str(weight).replace(".", "_")
                    record[f"p_full_{suffix}"] = geometric_revert(opening, closing, weight)
                    record[f"p_draw_{suffix}"] = draw_only_revert(opening, closing, weight)
                    matrix = matrix_revert(open_matrix, close_matrix, weight)
                    record[f"m_matrix_{suffix}"] = matrix
                    record[f"p_matrix_{suffix}"] = r35.agg1(matrix)
                predictions.append(record)
            frozen_updates.append((row, features))
        for row, features in frozen_updates:
            model.update(row, features)

    rows = predictions
    if len(rows) != 100:
        raise StudyError(f"selected prediction count mismatch: {len(rows)}")

    metrics: dict[str, Any] = {"market": r35.metrics(rows, "market"), "opening": r35.metrics(rows, "opening")}
    auc: dict[str, float | None] = {"market": draw_auc(rows, "market"), "opening": draw_auc(rows, "opening")}
    bootstrap: dict[str, Any] = {}
    gates: dict[str, Any] = {}
    promising: list[str] = []
    candidate_keys: list[tuple[str, str]] = []
    for weight in weights:
        suffix = str(weight).replace(".", "_")
        candidate_keys.extend([
            (f"full_revert_{suffix}", f"p_full_{suffix}"),
            (f"draw_revert_{suffix}", f"p_draw_{suffix}"),
            (f"matrix_revert_{suffix}", f"p_matrix_{suffix}"),
        ])

    boot_cfg = config["evaluation"]["paired_bootstrap"]
    for index, (name, key) in enumerate(candidate_keys):
        metrics[name] = r35.metrics(rows, key)
        auc[name] = draw_auc(rows, key)
        bootstrap[name] = r35.boot(rows, key, int(boot_cfg["samples"]), int(boot_cfg["seed"]) + index)
        gate = {
            "accuracy_better": metrics[name]["accuracy"] > metrics["market"]["accuracy"],
            "log_loss_nonworse": metrics[name]["log_loss"] <= metrics["market"]["log_loss"] + 1e-12,
            "brier_nonworse": metrics[name]["brier"] <= metrics["market"]["brier"] + 1e-12,
            "rps_nonworse": metrics[name]["rps"] <= metrics["market"]["rps"] + 1e-12,
            "accuracy_p05_positive": bootstrap[name]["accuracy"]["p05"] > 0.0,
            "draw_exists": metrics[name]["predicted_draw"] > 0,
            "draw_auc_better": auc[name] is not None and auc["market"] is not None and auc[name] > auc["market"],
        }
        gate["passed"] = all(gate.values())
        gates[name] = gate
        if gate["passed"]:
            promising.append(name)

    max_sum_error = max(max(abs(row["open_audit"]["sum"] - 1.0), abs(row["close_audit"]["sum"] - 1.0)) for row in rows)
    max_residual = max(max(row["open_audit"]["ou_residual"], row["open_audit"]["x1_residual"], row["close_audit"]["ou_residual"], row["close_audit"]["x1_residual"]) for row in rows)
    all_converged = all(row["open_audit"]["converged"] and row["close_audit"]["converged"] for row in rows)

    providers = {
        market: dict(sorted(Counter(str(row["trajectory"][market]["provider"]) for row in rows).items()))
        for market in ("x1", "ou", "ah")
    }
    status = "PROMISING_MARKET_REVERSION_SIGNAL_EXPLORATION_ONLY" if promising else "NO_MARKET_REVERSION_INCREMENT_FIXED100_EXPLORATION_ONLY"
    result = {
        "schema_version": config["schema_version"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "classification": config["classification"],
        "source": {"source_rows": source_rows, "market_complete_rows": len(enriched), "pre_result_exclusions": exclusions, "external_collection": 0, "provider_requests": 0, "opening_and_closing_columns_without_original_quote_timestamps": True},
        "field_audit": field_audit,
        "sample": {
            "rows": 100, "pool_rows": len(available), "excluded_prior_fixed100": excluded_counts, "prior_overlap_rows": len(selected_ids & excluded_ids),
            "seed": contract["seed"], "quota": quota, "identity_sha256": identity_sha256,
            "selection_uses_score_labels": False, "selection_frozen_before_score_label_parsing": True, "no_resampling_after_result": True,
            "selected_provider_distribution": providers, "actual_distribution": dict(Counter(row["actual"] for row in rows)),
        },
        "architecture": {
            "opening_to_closing_reversion_proxy": True, "original_quote_timestamps_available": False,
            "full_vector_reversion": True, "draw_only_reversion_preserves_closing_home_away_ratio": True, "score_matrix_reversion": True,
            "same_day_freeze": True, "poisson_used": False, "manual_draw_offset_used": False, "manual_draw_quota_used": False,
        },
        "candidate_contract": config["candidate_contract"],
        "coordination_audit": {"all_snapshot_projections_converged": all_converged, "max_probability_sum_error": max_sum_error, "max_market_partition_residual": max_residual},
        "metrics": metrics,
        "paired_bootstrap": bootstrap,
        "gates": gates,
        "post_result_draw_auc_diagnostic": auc,
        "promising_candidates": promising,
        "hard_limits": config["hard_limits"],
    }
    (out_dir / "status.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out_dir / "candidate_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["candidate", "hits", "accuracy", "log_loss", "brier", "rps", "predicted_draw", "draw_precision", "draw_recall", "draw_f1", "draw_auc", "bootstrap_accuracy_p05", "gate_passed"])
        for name, metric in metrics.items():
            writer.writerow([name, metric.get("hits"), metric.get("accuracy"), metric.get("log_loss"), metric.get("brier"), metric.get("rps"), metric.get("predicted_draw"), metric.get("draw_precision"), metric.get("draw_recall"), metric.get("draw_f1"), auc.get(name), bootstrap.get(name, {}).get("accuracy", {}).get("p05"), gates.get(name, {}).get("passed")])

    fields = ["id", "competition_id", "season", "date", "home_team", "away_team", "hg", "ag", "actual", "x1_provider", "ou_provider", "ah_provider", "open_home", "open_draw", "open_away", "close_home", "close_draw", "close_away", "market_pick"] + [field for name, _ in candidate_keys for field in (f"{name}_pick", f"{name}_draw")]
    with (out_dir / "fixed100_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = {key: row[key] for key in ("id", "competition_id", "season", "date", "home_team", "away_team", "hg", "ag", "actual")}
            output.update({
                "x1_provider": row["trajectory"]["x1"]["provider"], "ou_provider": row["trajectory"]["ou"]["provider"], "ah_provider": row["trajectory"]["ah"]["provider"],
                "open_home": row["opening"]["home"], "open_draw": row["opening"]["draw"], "open_away": row["opening"]["away"],
                "close_home": row["market"]["home"], "close_draw": row["market"]["draw"], "close_away": row["market"]["away"],
                "market_pick": max(OUTCOMES, key=lambda outcome: row["market"][outcome]),
            })
            for name, key in candidate_keys:
                output[f"{name}_pick"] = max(OUTCOMES, key=lambda outcome: row[key][outcome])
                output[f"{name}_draw"] = row[key]["draw"]
            writer.writerow(output)

    write_manifest(out_dir)
    return result


def self_test() -> None:
    opening = {"home": 0.40, "draw": 0.32, "away": 0.28}
    closing = {"home": 0.44, "draw": 0.28, "away": 0.28}
    full = geometric_revert(opening, closing, 0.5)
    draw = draw_only_revert(opening, closing, 0.5)
    assert abs(sum(full.values()) - 1.0) <= 1e-12
    assert abs(sum(draw.values()) - 1.0) <= 1e-12
    assert closing["draw"] < draw["draw"] < opening["draw"]
    prior = r35.normm({"0-0": 0.12, "1-0": 0.20, "0-1": 0.14, "1-1": 0.18, "2-1": 0.20, "1-2": 0.16})
    open_matrix, oa = r37.project_snapshot(prior, opening, {"over": 0.45, "under": 0.55})
    close_matrix, ca = r37.project_snapshot(prior, closing, {"over": 0.50, "under": 0.50})
    matrix = matrix_revert(open_matrix, close_matrix, 0.5)
    assert oa["converged"] and ca["converged"]
    assert abs(sum(matrix.values()) - 1.0) <= 1e-12
    print("PASS")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/v511_fixed100_market_reversion_r38.json")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "manifests/v511_fixed100_market_reversion_r38")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(load_json(args.config), args.out_dir)
    print(json.dumps({"status": result["status"], "sample": result.get("sample"), "metrics": result.get("metrics"), "draw_auc": result.get("post_result_draw_auc_diagnostic"), "promising": result.get("promising_candidates")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
