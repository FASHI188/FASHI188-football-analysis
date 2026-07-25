#!/usr/bin/env python3
"""V6.25.9 multi-seed random-100 stability audit for V6.25.8.

Diagnostic only; no model is changed.

Reconstruct the exact V6.25.8 pooled dynamic-shot 2-vs-3 challenger once, then
run 1000 deterministic simple-random samples without replacement of size 100
from the frozen 2279-row target pool. Baseline and candidate use identical rows
inside every replicate.

This estimates whether the single seed 20260725 result (27 -> 26 exact totals)
is representative or sampling noise. It does not select or tune the model.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_pooled_dynamic_shot_23split_v6258 as model
import v6_total_shot_23split_v6256 as split
from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS
from platform_core import load_json
from v6_total_distribution_pit_calibration_v6244 import _score

OUT = ROOT / "manifests" / "v6_total_pooled_dynamic_shot_resample_v6259_status.json"
BASE_SEED = 20260725
REPEATS = 1000
SAMPLE_N = 100

# Pure execution cache; statistical definition is unchanged.
_original_load = model._load
_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _cached_load(ctx: dict[str, Any], season: str) -> list[dict[str, Any]]:
    key = (str(ctx["cid"]), str(season))
    if key not in _cache:
        _cache[key] = _original_load(ctx, season)
    return _cache[key]


model._load = _cached_load


def _quantiles(values: list[float], probs=(0.025, 0.05, 0.25, 0.50, 0.75, 0.95, 0.975)) -> dict[str, float]:
    xs = sorted(float(x) for x in values)
    result: dict[str, float] = {}
    if not xs:
        return result
    n = len(xs)
    for q in probs:
        pos = q * (n - 1)
        lo = int(math.floor(pos)); hi = int(math.ceil(pos)); frac = pos - lo
        value = xs[lo] * (1.0 - frac) + xs[hi] * frac
        result[f"q{q:.3f}"] = value
    return result


def _build_pool() -> tuple[list[dict[str, Any]], float, dict[str, Any], list[str]]:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    contexts = []
    for cid in competitions:
        ctx = model._domain_context(cid)
        if ctx is not None:
            contexts.append(ctx)
    alpha, selection = model._select_global_alpha(contexts)
    training: list[dict[str, Any]] = []
    target_pool: list[dict[str, Any]] = []
    for ctx in contexts:
        for season in ctx["prior"]:
            training.extend(model._load(ctx, season))
        target_pool.extend(model.dynamic._build_rows_dynamic(
            ctx["cid"], ctx["target_season"], ctx["target_params"], ctx["config"], ctx["stats"]
        ))
    fitted = split._fit_model(training) if alpha > 0.0 else None
    candidate_pool = split._candidate_rows(target_pool, fitted, alpha)
    return candidate_pool, float(alpha), selection, [str(ctx["cid"]) for ctx in contexts]


def main() -> int:
    pool, alpha, selection, domains = _build_pool()
    if len(pool) < SAMPLE_N:
        raise RuntimeError("insufficient V6.25.8 target pool")

    exact_deltas: list[float] = []
    rps_deltas: list[float] = []
    log_deltas: list[float] = []
    score_top1_deltas: list[float] = []
    wins = ties = losses = 0
    exact_base_values: list[float] = []
    exact_candidate_values: list[float] = []

    for i in range(REPEATS):
        seed = BASE_SEED + i
        sampled = random.Random(seed).sample(pool, SAMPLE_N)
        base_metric = _score(sampled, "baseline_matrix")
        cand_metric = _score(sampled, "candidate_matrix")
        base_exact = float(base_metric["total_goals_0_7plus"]["top1_accuracy"])
        cand_exact = float(cand_metric["total_goals_0_7plus"]["top1_accuracy"])
        exact_delta = cand_exact - base_exact
        rps_delta = float(cand_metric["total_goals_0_7plus"]["mean_rps"]) - float(base_metric["total_goals_0_7plus"]["mean_rps"])
        base_log = split._total_log_loss(sampled, "baseline_matrix")
        cand_log = split._total_log_loss(sampled, "candidate_matrix")
        score_delta = float(cand_metric["score"]["top1_accuracy"]) - float(base_metric["score"]["top1_accuracy"])

        exact_base_values.append(base_exact)
        exact_candidate_values.append(cand_exact)
        exact_deltas.append(exact_delta)
        rps_deltas.append(rps_delta)
        log_deltas.append(cand_log - base_log)
        score_top1_deltas.append(score_delta)
        if exact_delta > 1e-12:
            wins += 1
        elif exact_delta < -1e-12:
            losses += 1
        else:
            ties += 1

    full_base = _score(pool, "baseline_matrix")
    full_candidate = _score(pool, "candidate_matrix")
    payload = {
        "schema_version": "V6.25.9-v6258-multiseed-random100-stability-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "classification": "RESEARCH_DIAGNOSTIC_NO_MODEL_CHANGE_FORMAL_WEIGHT_0",
        "source_model": "V6.25.8 pooled dynamic-shot 2v3",
        "source_selected_alpha": alpha,
        "source_selection": selection,
        "domains": domains,
        "eligible_pool_count": len(pool),
        "sample_n": SAMPLE_N,
        "repeat_count": REPEATS,
        "seed_rule": "seed=20260725+i for i=0..999",
        "full_pool": {
            "baseline_exact_total_accuracy": full_base["total_goals_0_7plus"]["top1_accuracy"],
            "candidate_exact_total_accuracy": full_candidate["total_goals_0_7plus"]["top1_accuracy"],
            "baseline_total_rps": full_base["total_goals_0_7plus"]["mean_rps"],
            "candidate_total_rps": full_candidate["total_goals_0_7plus"]["mean_rps"],
            "baseline_score_top1_accuracy": full_base["score"]["top1_accuracy"],
            "candidate_score_top1_accuracy": full_candidate["score"]["top1_accuracy"],
        },
        "random100_stability": {
            "candidate_exact_wins": wins,
            "ties": ties,
            "candidate_exact_losses": losses,
            "candidate_exact_win_rate": wins / REPEATS,
            "candidate_exact_nonloss_rate": (wins + ties) / REPEATS,
            "mean_baseline_exact_accuracy": statistics.fmean(exact_base_values),
            "mean_candidate_exact_accuracy": statistics.fmean(exact_candidate_values),
            "mean_exact_accuracy_delta": statistics.fmean(exact_deltas),
            "exact_accuracy_delta_quantiles": _quantiles(exact_deltas),
            "mean_total_rps_delta": statistics.fmean(rps_deltas),
            "total_rps_delta_quantiles": _quantiles(rps_deltas),
            "rps_improvement_rate": sum(1 for x in rps_deltas if x < 0.0) / REPEATS,
            "mean_total_logloss_delta": statistics.fmean(log_deltas),
            "total_logloss_delta_quantiles": _quantiles(log_deltas),
            "logloss_improvement_rate": sum(1 for x in log_deltas if x < 0.0) / REPEATS,
            "mean_score_top1_delta": statistics.fmean(score_top1_deltas),
        },
        "governance": {
            "model_parameters_changed": False,
            "sample_seeds_used_for_model_selection": False,
            "same_rows_baseline_candidate_each_repeat": True,
            "sampling_without_replacement": True,
            "formal_weight": 0,
            "current_rule_change": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
