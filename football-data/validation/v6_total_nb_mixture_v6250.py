#!/usr/bin/env python3
"""V6.25.0 nested-OOS mean-preserving two-NB mixture total head.

Research only; formal_weight=0.

Motivation: a single NB total distribution is unimodal and the current direct
track places 73% of predictions on exact total=2. This challenger adds a latent
low/high-tempo mixture while preserving the baseline match-specific total mean.

For spread delta in a fixed ex-ante grid:
  P(T=t) = 0.5 * NB(mu*(1-delta), k) + 0.5 * NB(mu*(1+delta), k)
The mixture mean remains mu. delta=0 is exactly the baseline NB family.

Per competition, delta is selected only by nested prior-season OOS total-goal
RPS. Ties prefer smaller delta. Target-season outcomes are never used for model
selection. The selected total marginal is embedded into the baseline joint score
matrix while preserving P(score | total), so all outputs remain coherent.
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for p in (ENGINE, VALIDATION):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from backtest_last_complete_season_all_domains_v470 import (  # noqa: E402
    FORMAL_STATUS,
    REPORT_ROOT,
    _fold_for_season,
    _requested_last_complete_season,
)
from football_v460_engine import _merge_parameters, load_config, negative_binomial_pmf  # noqa: E402
from platform_core import PlatformError, load_json, score_matrix_rows  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402
from v6_total_local_residual_knn_v6248 import _season_rows  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_nb_mixture_v6250_status.json"
SEED = 20260725
SAMPLE_N = 100
DELTAS = (0.0, 0.10, 0.20, 0.30, 0.40)
EPS = 1e-15


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _mixture_bucket_probs(mu: float, k: float, delta: float) -> dict[str, float]:
    d = max(0.0, min(0.75, float(delta)))
    low = max(1e-9, float(mu) * (1.0 - d))
    high = max(1e-9, float(mu) * (1.0 + d))
    out = {key: 0.0 for key in TOTAL_BUCKETS}
    exact_sum = 0.0
    for total in range(7):
        p = 0.5 * negative_binomial_pmf(total, low, k) + 0.5 * negative_binomial_pmf(total, high, k)
        out[str(total)] = p
        exact_sum += p
    out["7+"] = max(0.0, 1.0 - exact_sum)
    norm = sum(out.values())
    if norm <= 0.0:
        raise PlatformError("mixture total distribution has zero mass")
    return {key: value / norm for key, value in out.items()}


def _baseline_bucket_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        out[_bucket(int(h + a))] += float(p)
    return out


def _mixture_matrix(row: dict[str, Any], delta: float) -> list[dict[str, Any]]:
    matrix = row["matrix"]
    mu = math.exp(float(row["feature"][0]))
    k = math.exp(float(row["feature"][1]))
    target = _mixture_bucket_probs(mu, k, delta)
    base = _baseline_bucket_probs(matrix)
    factors = {key: target[key] / max(EPS, base[key]) for key in TOTAL_BUCKETS}
    output = []
    mass = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * factors[_bucket(int(h + a))]
        output.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        mass += value
    if mass <= 0.0:
        raise PlatformError("mixture joint matrix has zero mass")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / mass
    return output


def _candidate_rows(rows: list[dict[str, Any]], delta: float) -> list[dict[str, Any]]:
    return [{
        **row,
        "baseline_matrix": row["matrix"],
        "candidate_matrix": row["matrix"] if delta == 0.0 else _mixture_matrix(row, delta),
        "mixture_delta": float(delta),
    } for row in rows]


def _load_rows(cid: str, season: str, report: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    fold = _fold_for_season(report, season)
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError(f"invalid selected parameters for {cid} {season}")
    params = _merge_parameters(config, selected)
    rows, _ = _season_rows(cid, season, params, config)
    return rows


def _select_delta(cid: str, target_fold: dict[str, Any], report: dict[str, Any], config: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    prior = [str(s) for s in (target_fold.get("prior_seasons") or [])]
    scores: dict[float, dict[str, Any]] = {d: {"rps_sum": 0.0, "count": 0, "folds": []} for d in DELTAS}
    # Mixture shape does not need a fitted neighbor set, but selection is still
    # validated only on seasons with at least one strictly earlier season.
    for idx in range(1, len(prior)):
        validation_season = prior[idx]
        try:
            validation_rows = _load_rows(cid, validation_season, report, config)
        except Exception:
            continue
        if not validation_rows:
            continue
        for delta in DELTAS:
            rows = _candidate_rows(validation_rows, delta)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            scores[delta]["rps_sum"] += rps * n
            scores[delta]["count"] += n
            scores[delta]["folds"].append({
                "validation_season": validation_season,
                "strictly_earlier_seasons_exist": True,
                "count": n,
                "mean_total_rps": rps,
            })
    eligible = []
    for delta in DELTAS:
        n = int(scores[delta]["count"])
        mean_rps = scores[delta]["rps_sum"] / n if n else None
        scores[delta]["mean_rps"] = mean_rps
        if mean_rps is not None:
            eligible.append((float(mean_rps), float(delta)))
    if not eligible:
        return 0.0, {"fallback": "no_nested_oos_validation_seasons", "delta_scores": scores}
    best_rps, best_delta = min(eligible, key=lambda item: (item[0], item[1]))
    return best_delta, {
        "selected_delta": best_delta,
        "selected_mean_rps": best_rps,
        "selection_rule": "minimum pooled nested-prior-season total RPS; tie -> smaller delta",
        "delta_scores": scores,
    }


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError(f"invalid target parameters for {cid} {target_season}")
    target_params = _merge_parameters(config, target_selected)
    selected_delta, selection = _select_delta(cid, target_fold, report, config)
    target_raw, skips = _season_rows(cid, target_season, target_params, config)
    target_rows = _candidate_rows(target_raw, selected_delta)
    base = _score(target_rows, "baseline_matrix")
    cand = _score(target_rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "target_season": target_season,
        "selected_delta": selected_delta,
        "selection": selection,
        "baseline": base,
        "candidate": cand,
        "delta": _delta(base, cand),
        "baseline_top1_bucket_counts": _top1_counts(target_rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(target_rows, "candidate_matrix"),
        "target_skips": dict(skips),
    }, target_rows


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    selected_counts = Counter()
    for cid in competitions:
        try:
            result, rows = _domain(cid)
            reports[cid] = result
            pool.extend(rows)
            selected_counts[str(result["selected_delta"])] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise PlatformError(f"competition failures: {failures}")
    if len(pool) < SAMPLE_N:
        raise PlatformError("insufficient pooled target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_cand = _score(pool, "candidate_matrix")
    sampled = random.Random(SEED).sample(pool, SAMPLE_N)
    sample_base = _score(sampled, "baseline_matrix")
    sample_cand = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.0-mean-preserving-two-nb-mixture-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_NESTED_OOS_FORMAL_WEIGHT_0",
        "eligible_target_pool_count": len(pool),
        "delta_grid": list(DELTAS),
        "selected_delta_domain_counts": dict(selected_counts),
        "mixture_contract": "0.5*NB(mu*(1-d),k)+0.5*NB(mu*(1+d),k); mean preserved; d=0 baseline",
        "full_pool": {
            "baseline": full_base,
            "candidate": full_cand,
            "delta": _delta(full_base, full_cand),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "baseline": sample_base,
            "candidate": sample_cand,
            "delta": _delta(sample_base, sample_cand),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "target_season_results_used_for_delta_selection": False,
            "delta_selected_nested_prior_seasons_only": True,
            "delta_zero_exact_baseline_family": True,
            "mixture_mean_equals_baseline_mu_total": True,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "historical_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "eligible_target_pool_count": len(pool),
        "selected_delta_domain_counts": payload["selected_delta_domain_counts"],
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
