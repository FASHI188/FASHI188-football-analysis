#!/usr/bin/env python3
"""V6.46.2 fixed1000 three-track ablation: market-blind vs market vs blend curve.

The blend curve is descriptive only. No weight is selected for runtime or promotion from
this benchmark. Any future fusion weight must be selected on a disjoint development set,
then frozen before prospective evaluation.

V6.46.4 cache repair: reuse the hash-validated V6.46.1 prediction cache when available;
fall back to deterministic recomputation only when the cache is absent or invalid.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation"
ENGINE = ROOT / "engine"
for p in (VALIDATION, ENGINE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_1x2_fixed1000_benchmark_v6130 as metrics
import evaluate_market_blind_elo_fixed1000_v6461 as blind

BENCHMARK = ROOT / "benchmarks" / "v6_1x2_neutral_fixed1000_v6131.json"
OUT = ROOT / "manifests" / "v6_three_track_ablation_v6462_status.json"
GRID = tuple(round(i / 10.0, 1) for i in range(11))
DIRECTIONS = ("home", "draw", "away")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def key(row: dict[str, Any]) -> tuple[str, str, str, str, int]:
    return (
        str(row["competition_id"]), str(row["date"]), str(row["home_team"]),
        str(row["away_team"]), int(row["row_index"]),
    )


def normalize(p: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, float(p[d])) for d in DIRECTIONS)
    if s <= 0:
        raise RuntimeError("non-positive probability sum")
    return {d: max(0.0, float(p[d])) / s for d in DIRECTIONS}


def row_for_metrics(base: dict[str, Any], probs: dict[str, float], provider: str) -> dict[str, Any]:
    p = normalize(probs)
    pick = max(DIRECTIONS, key=lambda d: p[d])
    return {
        "competition_id": base["competition_id"],
        "season": base["season"],
        "date": base["date"],
        "row_index": base["row_index"],
        "home_team": base["home_team"],
        "away_team": base["away_team"],
        "actual": base["actual"],
        "provider": provider,
        "probabilities": p,
        "pick": pick,
        "pmax": p[pick],
    }


def load_blind_predictions(benchmark: dict[str, Any], benchmark_keys: set[tuple[str, int]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    benchmark_sha = blind.file_sha256(BENCHMARK)
    config_sha = blind.model_config_sha256()
    cached, audit = blind.load_valid_cache(benchmark_sha, config_sha)
    if cached is not None:
        return cached, {"mode": "HASH_VALIDATED_CACHE", **audit}
    rows = []
    for cid in benchmark["target_competitions"]:
        pred, _ = blind.evaluate_domain(cid, benchmark_keys)
        rows.extend(pred)
    rows.sort(key=lambda r: (r["date"], r["competition_id"], r["home_team"], r["away_team"]))
    return rows, {"mode": "DETERMINISTIC_RECOMPUTE", **audit}


def main() -> int:
    benchmark = json.loads(BENCHMARK.read_text(encoding="utf-8"))
    benchmark_keys = {(str(r["source_file"]), int(r["row_index"])) for r in benchmark["rows"]}
    blind_rows, blind_input_audit = load_blind_predictions(benchmark, benchmark_keys)
    blind_map = {key(r): r for r in blind_rows}

    common = []
    market_only = []
    blind_only = []
    for b in benchmark["rows"]:
        m = b.get("market")
        br = blind_map.get(key(b))
        if not m or br is None:
            continue
        market_row = row_for_metrics(b, m["probabilities"], "MARKET")
        blind_row = row_for_metrics(b, br["probabilities"], "MARKET_BLIND_ELO")
        market_only.append(market_row)
        blind_only.append(blind_row)
        common.append((b, market_row, blind_row))

    curve = []
    for alpha in GRID:
        rows = []
        for b, mr, br in common:
            probs = {
                d: alpha * float(mr["probabilities"][d]) + (1.0 - alpha) * float(br["probabilities"][d])
                for d in DIRECTIONS
            }
            rows.append(row_for_metrics(b, probs, f"LINEAR_BLEND_MARKET_WEIGHT_{alpha:.1f}"))
        curve.append({
            "market_weight": alpha,
            "blind_weight": round(1.0 - alpha, 1),
            "metrics": metrics.multiclass_scores(rows),
            "draw_audit": metrics.draw_audit(rows),
        })

    def best(metric: str, lower: bool) -> dict[str, Any]:
        eligible = [x for x in curve if x["metrics"].get(metric) is not None]
        return min(eligible, key=lambda x: x["metrics"][metric]) if lower else max(eligible, key=lambda x: x["metrics"][metric])

    market_metrics = metrics.multiclass_scores(market_only)
    blind_metrics = metrics.multiclass_scores(blind_only)
    best_log = best("log_loss", True)
    best_brier = best("brier", True)
    best_rps = best("rps", True)
    best_acc = best("accuracy", False)

    interior = [x for x in curve if 0.0 < x["market_weight"] < 1.0]
    interior_dominates_market_all_proper = any(
        x["metrics"]["log_loss"] < market_metrics["log_loss"]
        and x["metrics"]["brier"] < market_metrics["brier"]
        and x["metrics"]["rps"] < market_metrics["rps"]
        for x in interior
    )

    payload = {
        "schema_version": "V6.46.4-three-track-ablation-cache-r2",
        "generated_at_utc": utc_now(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_FIXED1000_DESCRIPTIVE_ABLATION_ONLY",
        "benchmark_path": str(BENCHMARK.relative_to(ROOT)),
        "benchmark_target_n": benchmark["target_n"],
        "blind_prediction_input": blind_input_audit,
        "common_row_count": len(common),
        "common_row_coverage": len(common) / int(benchmark["target_n"]),
        "market_only_common_rows": market_metrics,
        "market_blind_common_rows": blind_metrics,
        "blend_curve": curve,
        "descriptive_best_points": {
            "accuracy": {"market_weight": best_acc["market_weight"], "value": best_acc["metrics"]["accuracy"]},
            "log_loss": {"market_weight": best_log["market_weight"], "value": best_log["metrics"]["log_loss"]},
            "brier": {"market_weight": best_brier["market_weight"], "value": best_brier["metrics"]["brier"]},
            "rps": {"market_weight": best_rps["market_weight"], "value": best_rps["metrics"]["rps"]},
        },
        "independent_signal_diagnostic": {
            "any_interior_blend_dominates_market_on_all_three_proper_scores": interior_dominates_market_all_proper,
            "interpretation": "If true, the result-only blind baseline contains some incremental information on this retrospective benchmark. This does not authorize selecting the observed weight for runtime.",
        },
        "anti_overfit_governance": {
            "grid_predeclared": list(GRID),
            "benchmark_used_to_choose_runtime_weight": False,
            "descriptive_argmin_is_not_a_candidate_weight": True,
            "future_candidate_weight_requires_disjoint_development_selection": True,
            "future_candidate_weight_requires_postfreeze_validation": True,
        },
        "engineering": {
            "hash_safe_prediction_cache_supported": True,
            "cache_key_benchmark_sha_required": True,
            "cache_key_model_config_sha_required": True,
            "recompute_only_on_cache_miss_or_invalidation": True,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "formal_probability_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "common_row_count": payload["common_row_count"],
        "blind_prediction_input": blind_input_audit,
        "market": market_metrics,
        "blind": blind_metrics,
        "best": payload["descriptive_best_points"],
        "independent_signal": payload["independent_signal_diagnostic"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
