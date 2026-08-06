#!/usr/bin/env python3
"""Fixed 100-match exploration over the existing neutral 1X2 benchmark.

Research only. The sample is selected without labels. The frozen market-residual
algorithm is run prequentially over all 1,000 rows; only the fixed 100 identities
are scored for this study. No provider request, external collection, formal
promotion, current-match output, unified matrix, exact score or EV is allowed.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import evaluate_v510_market_residual_1x2_r1 as base

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "v511_fixed100_market_residual_r34.json"
DEFAULT_OUT_DIR = ROOT / "manifests" / "v511_fixed100_market_residual_r34"
DIRECTIONS = base.DIRECTIONS
EPS = 1e-15


class StudyError(RuntimeError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise StudyError(f"JSON root must be object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def identity(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(field) or "").strip()
        for field in ("competition_id", "date", "home_team", "away_team")
    )


def normalize(probabilities: dict[str, float]) -> dict[str, float]:
    values = {direction: max(EPS, float(probabilities[direction])) for direction in DIRECTIONS}
    total = sum(values.values())
    if not math.isfinite(total) or total <= 0:
        raise StudyError("invalid probability total")
    return {direction: values[direction] / total for direction in DIRECTIONS}


def linear_pool(market: dict[str, float], residual: dict[str, float], weight: float) -> dict[str, float]:
    return normalize({
        direction: (1.0 - weight) * market[direction] + weight * residual[direction]
        for direction in DIRECTIONS
    })


def log_pool(market: dict[str, float], residual: dict[str, float]) -> dict[str, float]:
    return normalize({direction: math.sqrt(market[direction] * residual[direction]) for direction in DIRECTIONS})


def draw_only(market: dict[str, float], residual: dict[str, float], half: bool) -> dict[str, float]:
    q = (market["draw"] + residual["draw"]) / 2.0 if half else residual["draw"]
    q = min(1.0 - EPS, max(EPS, q))
    non_draw_market = market["home"] + market["away"]
    if non_draw_market <= 0:
        raise StudyError("invalid market non-draw mass")
    home_share = market["home"] / non_draw_market
    return normalize({
        "home": (1.0 - q) * home_share,
        "draw": q,
        "away": (1.0 - q) * (1.0 - home_share),
    })


def candidate_probabilities(market: dict[str, float], residual: dict[str, float]) -> dict[str, dict[str, float]]:
    return {
        "market": market,
        "full_residual": residual,
        "linear_residual_25": linear_pool(market, residual, 0.25),
        "linear_residual_50": linear_pool(market, residual, 0.50),
        "linear_residual_75": linear_pool(market, residual, 0.75),
        "log_pool_50": log_pool(market, residual),
        "draw_only_full": draw_only(market, residual, False),
        "draw_only_half": draw_only(market, residual, True),
    }


def clean_benchmark(benchmark: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = benchmark.get("rows")
    if not isinstance(raw_rows, list):
        raise StudyError("benchmark rows missing")
    clean: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict):
            raise StudyError(f"row {index} is not object")
        key = identity(raw)
        if not key or key in seen:
            raise StudyError(f"invalid or duplicate identity: {key}")
        seen.add(key)
        actual = base.actual_direction(raw)
        market = base.normalized_probs((raw.get("market") or {}).get("probabilities") or {})
        date = datetime.fromisoformat(str(raw.get("date") or "").replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise StudyError(f"naive date at row {index}")
        clean.append({
            **raw,
            "actual": actual,
            "market": {**(raw.get("market") or {}), "probabilities": market},
            "_identity": key,
        })
    clean.sort(key=lambda row: (
        str(row["date"]), str(row["competition_id"]), str(row["home_team"]), str(row["away_team"])
    ))
    return clean


def quota_by_competition(rows: list[dict[str, Any]], target: int) -> dict[str, int]:
    counts = Counter(str(row["competition_id"]) for row in rows)
    total = sum(counts.values())
    if target <= 0 or target > total:
        raise StudyError("invalid sample target")
    exact = {competition: target * count / total for competition, count in counts.items()}
    quota = {competition: min(counts[competition], int(math.floor(value))) for competition, value in exact.items()}
    remaining = target - sum(quota.values())
    order = sorted(counts, key=lambda competition: (-(exact[competition] - quota[competition]), competition))
    while remaining:
        progressed = False
        for competition in order:
            if quota[competition] < counts[competition]:
                quota[competition] += 1
                remaining -= 1
                progressed = True
                if remaining == 0:
                    break
        if not progressed:
            raise StudyError("unable to allocate exact sample quota")
    return dict(sorted(quota.items()))


def fixed_sample(rows: list[dict[str, Any]], target: int, seed: int) -> tuple[set[str], dict[str, int]]:
    quota = quota_by_competition(rows, target)
    by_competition: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_competition[str(row["competition_id"])].append(row)
    selected: set[str] = set()
    for competition in sorted(by_competition):
        ranked = sorted(
            by_competition[competition],
            key=lambda row: (
                hashlib.sha256(f"{seed}|{row['_identity']}".encode("utf-8")).hexdigest(),
                row["_identity"],
            ),
        )
        selected.update(row["_identity"] for row in ranked[: quota[competition]])
    if len(selected) != target:
        raise StudyError(f"sample count mismatch: {len(selected)}")
    return selected, quota


def prequential_predictions(rows: list[dict[str, Any]], model_config: dict[str, Any]) -> list[dict[str, Any]]:
    residual_model = base.MarketResidualModel(model_config.get("market_residual") or {})
    by_day: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["date"])[:10]].append(row)
    output: list[dict[str, Any]] = []
    for day in sorted(by_day):
        frozen: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in by_day[day]:
            features = residual_model.frozen_features(row)
            market = normalize(features["market"])
            residual = normalize(residual_model.predict(features))
            output.append({
                "identity": row["_identity"],
                "competition_id": str(row["competition_id"]),
                "season": row.get("season"),
                "date": str(row["date"]),
                "home_team": str(row["home_team"]),
                "away_team": str(row["away_team"]),
                "actual": row["actual"],
                **candidate_probabilities(market, residual),
            })
            frozen.append((row, features))
        for row, features in frozen:
            residual_model.update(row, features)
    return output


def pick(probability: dict[str, float]) -> str:
    return max(DIRECTIONS, key=lambda direction: (probability[direction], direction == "home", direction == "draw"))


def row_components(row: dict[str, Any], candidate: str) -> dict[str, float]:
    probability = row[candidate]
    actual = row["actual"]
    target = {direction: 1.0 if direction == actual else 0.0 for direction in DIRECTIONS}
    predicted = pick(probability)
    return {
        "accuracy": float(predicted == actual),
        "log_loss": -math.log(max(EPS, probability[actual])),
        "brier": sum((probability[direction] - target[direction]) ** 2 for direction in DIRECTIONS),
        "rps": (
            (probability["home"] - target["home"]) ** 2
            + (probability["home"] + probability["draw"] - target["home"] - target["draw"]) ** 2
        ) / 2.0,
    }


def summarize(rows: list[dict[str, Any]], candidate: str) -> dict[str, Any]:
    if not rows:
        raise StudyError("empty summary rows")
    components = [row_components(row, candidate) for row in rows]
    actual_counts = Counter(row["actual"] for row in rows)
    predicted_counts = Counter(pick(row[candidate]) for row in rows)
    confusion = {actual: {predicted: 0 for predicted in DIRECTIONS} for actual in DIRECTIONS}
    for row in rows:
        confusion[row["actual"]][pick(row[candidate])] += 1
    true_draw = confusion["draw"]["draw"]
    draw_precision = true_draw / predicted_counts["draw"] if predicted_counts["draw"] else 0.0
    draw_recall = true_draw / actual_counts["draw"] if actual_counts["draw"] else 0.0
    draw_f1 = 2 * draw_precision * draw_recall / (draw_precision + draw_recall) if draw_precision + draw_recall else 0.0
    non_draw_rows = [row for row in rows if row["actual"] != "draw"]
    non_draw_hits = sum(pick(row[candidate]) == row["actual"] for row in non_draw_rows)
    return {
        "rows": len(rows),
        "hits": int(sum(value["accuracy"] for value in components)),
        "accuracy": sum(value["accuracy"] for value in components) / len(components),
        "log_loss": sum(value["log_loss"] for value in components) / len(components),
        "brier": sum(value["brier"] for value in components) / len(components),
        "rps": sum(value["rps"] for value in components) / len(components),
        "actual_counts": {direction: int(actual_counts[direction]) for direction in DIRECTIONS},
        "predicted_counts": {direction: int(predicted_counts[direction]) for direction in DIRECTIONS},
        "draw_precision": draw_precision,
        "draw_recall": draw_recall,
        "draw_f1": draw_f1,
        "non_draw_accuracy": non_draw_hits / len(non_draw_rows) if non_draw_rows else 0.0,
        "mean_confidence": sum(max(row[candidate].values()) for row in rows) / len(rows),
        "confusion": confusion,
    }


def confidence_summaries(rows: list[dict[str, Any]], candidate: str, coverages: list[float]) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: (-max(row[candidate].values()), row["identity"]))
    output: dict[str, Any] = {}
    for coverage in coverages:
        count = max(1, min(len(rows), int(round(len(rows) * float(coverage)))))
        output[f"{int(round(coverage * 100))}pct"] = summarize(ordered[:count], candidate)
    return output


def r7_quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise StudyError("empty quantile")
    if len(ordered) == 1:
        return ordered[0]
    h = (len(ordered) - 1) * probability
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def paired_bootstrap(rows: list[dict[str, Any]], candidate: str, cfg: dict[str, Any], offset: int) -> dict[str, Any]:
    baseline = [row_components(row, "market") for row in rows]
    challenger = [row_components(row, candidate) for row in rows]
    samples = int(cfg["evaluation"]["paired_bootstrap_samples"])
    interval = [float(value) for value in cfg["evaluation"]["interval"]]
    rng = random.Random(int(cfg["evaluation"]["paired_bootstrap_seed"]) + offset)
    output: dict[str, Any] = {}
    for metric in ("accuracy", "log_loss", "brier", "rps"):
        delta = [challenger[i][metric] - baseline[i][metric] for i in range(len(rows))]
        draws: list[float] = []
        for _ in range(samples):
            draws.append(sum(delta[rng.randrange(len(rows))] for _ in range(len(rows))) / len(rows))
        output[metric] = {
            "point": sum(delta) / len(delta),
            "p05": r7_quantile(draws, interval[0]),
            "p95": r7_quantile(draws, interval[1]),
        }
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise StudyError(f"empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(config_path: Path, out_dir: Path) -> dict[str, Any]:
    cfg = load_json(config_path)
    benchmark_path = ROOT / str(cfg["source_benchmark"])
    model_cfg_path = ROOT / str(cfg["source_market_residual_config"])
    benchmark = load_json(benchmark_path)
    model_cfg = load_json(model_cfg_path)
    rows = clean_benchmark(benchmark)
    expected = int(cfg["sample_contract"]["source_rows"])
    if len(rows) != expected:
        raise StudyError(f"source row mismatch: {len(rows)} != {expected}")
    selected, quota = fixed_sample(
        rows,
        int(cfg["sample_contract"]["rows"]),
        int(cfg["sample_contract"]["seed"]),
    )
    all_predictions = prequential_predictions(rows, model_cfg)
    sample = [row for row in all_predictions if row["identity"] in selected]
    sample.sort(key=lambda row: row["identity"])
    if len(sample) != 100 or len({row["identity"] for row in sample}) != 100:
        raise StudyError("fixed100 identity mismatch")

    candidates = ["market", *cfg["candidate_contract"]["fixed_candidates"]]
    metrics: dict[str, Any] = {}
    bootstrap: dict[str, Any] = {}
    confidence: dict[str, Any] = {}
    for offset, candidate in enumerate(candidates):
        metrics[candidate] = summarize(sample, candidate)
        confidence[candidate] = confidence_summaries(
            sample, candidate, [float(value) for value in cfg["evaluation"]["confidence_coverages"]]
        )
        if candidate != "market":
            bootstrap[candidate] = paired_bootstrap(sample, candidate, cfg, offset)

    market = metrics["market"]
    gate = cfg["evaluation"]["promising_point_gate"]
    promising: list[str] = []
    for candidate in candidates[1:]:
        result = metrics[candidate]
        if (
            result["accuracy"] - market["accuracy"] > float(gate["accuracy_delta_minimum"])
            and result["log_loss"] - market["log_loss"] <= float(gate["log_loss_delta_maximum"])
            and result["brier"] - market["brier"] <= float(gate["brier_delta_maximum"])
        ):
            promising.append(candidate)
    descriptive_best = sorted(
        candidates,
        key=lambda candidate: (
            -metrics[candidate]["accuracy"],
            metrics[candidate]["log_loss"],
            metrics[candidate]["brier"],
            candidate,
        ),
    )[0]
    status = (
        "PROMISING_FIXED100_POINT_SIGNAL_EXPLORATION_ONLY"
        if promising
        else "NO_FIXED100_POINT_IMPROVEMENT_EXPLORATION_ONLY"
    )

    sample_identities = [row["identity"] for row in sample]
    sample_sha = hashlib.sha256("\n".join(sample_identities).encode("utf-8")).hexdigest()
    result = {
        "schema_version": "v511_fixed100_market_residual_r34_status.1",
        "status": status,
        "classification": cfg["classification"],
        "source": {
            "benchmark_path": str(benchmark_path.relative_to(ROOT)),
            "benchmark_sha256": sha256_file(benchmark_path),
            "source_rows": len(rows),
            "source_market_residual_config": str(model_cfg_path.relative_to(ROOT)),
            "external_collection": 0,
        },
        "sample": {
            "rows": len(sample),
            "selection_uses_labels": False,
            "quota_by_competition": quota,
            "actual_counts_revealed_only_after_selection": metrics["market"]["actual_counts"],
            "identity_sha256": sample_sha,
            "no_resampling_after_result": True,
        },
        "execution": {
            "prequential_predictions_over_all_source_rows": True,
            "same_day_predictions_frozen_before_updates": True,
            "candidate_catalog_fixed_before_scoring": True,
            "test_labels_used_to_remove_candidates": False,
        },
        "metrics": metrics,
        "confidence_coverage": confidence,
        "paired_bootstrap_candidate_minus_market": bootstrap,
        "descriptive_best": descriptive_best,
        "promising_point_candidates": promising,
        "ruling": {
            "independent_confirmation": False,
            "formal_promotion_allowed": False,
            "formal_weight": 0,
            "current_match_use_allowed": False,
            "interpretation": (
                "At least one fixed candidate improved full-coverage point accuracy without worsening point log loss or Brier on this viewed 100-match exploratory sample. Independent confirmation is still required."
                if promising
                else "No fixed candidate improved full-coverage point accuracy while preserving point log loss and Brier on this viewed 100-match exploratory sample."
            ),
        },
        "hard_limits": cfg["hard_limits"],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    status_path = out_dir / "status.json"
    status_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        value = metrics[candidate]
        summary_rows.append({
            "candidate": candidate,
            "rows": value["rows"],
            "hits": value["hits"],
            "accuracy": value["accuracy"],
            "accuracy_delta_vs_market": value["accuracy"] - market["accuracy"],
            "log_loss": value["log_loss"],
            "log_loss_delta_vs_market": value["log_loss"] - market["log_loss"],
            "brier": value["brier"],
            "brier_delta_vs_market": value["brier"] - market["brier"],
            "rps": value["rps"],
            "rps_delta_vs_market": value["rps"] - market["rps"],
            "draw_precision": value["draw_precision"],
            "draw_recall": value["draw_recall"],
            "draw_f1": value["draw_f1"],
            "non_draw_accuracy": value["non_draw_accuracy"],
            "predicted_home": value["predicted_counts"]["home"],
            "predicted_draw": value["predicted_counts"]["draw"],
            "predicted_away": value["predicted_counts"]["away"],
            "promising_point_gate": candidate in promising,
        })
    write_csv(out_dir / "candidate_summary.csv", summary_rows)

    match_rows: list[dict[str, Any]] = []
    for row in sample:
        output: dict[str, Any] = {
            "identity": row["identity"],
            "competition_id": row["competition_id"],
            "season": row.get("season"),
            "date": row["date"],
            "home_team": row["home_team"],
            "away_team": row["away_team"],
            "actual": row["actual"],
        }
        for candidate in candidates:
            output[f"{candidate}_pick"] = pick(row[candidate])
            output[f"{candidate}_p_home"] = row[candidate]["home"]
            output[f"{candidate}_p_draw"] = row[candidate]["draw"]
            output[f"{candidate}_p_away"] = row[candidate]["away"]
        match_rows.append(output)
    write_csv(out_dir / "fixed100_predictions.csv", match_rows)

    manifest = {
        "schema_version": "v511_fixed100_market_residual_r34_artifact.1",
        "files": {},
    }
    for path in sorted(out_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            manifest["files"][path.name] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def self_test() -> None:
    market = {"home": 0.5, "draw": 0.3, "away": 0.2}
    residual = {"home": 0.4, "draw": 0.4, "away": 0.2}
    candidates = candidate_probabilities(market, residual)
    assert set(candidates) == {
        "market", "full_residual", "linear_residual_25", "linear_residual_50",
        "linear_residual_75", "log_pool_50", "draw_only_full", "draw_only_half",
    }
    for probability in candidates.values():
        assert abs(sum(probability.values()) - 1.0) < 1e-12
        assert all(value > 0 for value in probability.values())
    assert abs(candidates["draw_only_full"]["draw"] - 0.4) < 1e-12
    print(json.dumps({"status": "PASS", "self_test": True}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    result = run(args.config, args.out_dir)
    print(json.dumps({
        "status": result["status"],
        "sample": result["sample"],
        "descriptive_best": result["descriptive_best"],
        "promising_point_candidates": result["promising_point_candidates"],
        "metrics": result["metrics"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
