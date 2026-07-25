#!/usr/bin/env python3
"""V6.25.4 shot-conditioned ordinal residual total-distribution challenger.

Research only; formal_weight=0.

V6.25.3 proved that strict-PIT shot/SOT features improve total-goal RPS when
used only as a mean offset, but exact-total Top-1 did not improve robustly. This
challenger therefore models the ordered 0/1/2/3/4/5/6/7+ distribution directly.

For thresholds j=0..6:
    logit P(T<=j) = logit P_baseline(T<=j) + beta_j' x
where x contains the existing baseline total-state features (log mu_total,
log dispersion) plus strict pre-match rolling HS/AS/HST/AST features.

Each threshold model is ridge-logistic with the baseline CDF as an offset.
Candidate correction strength alpha is selected per competition only by nested
prior-season OOS total-goal RPS from a fixed grid including alpha=0. Target
season outcomes are never used for fitting or alpha selection.

The seven corrected cumulative probabilities are projected to a monotone CDF,
converted back to 8 bucket probabilities, then embedded into the baseline joint
score matrix while preserving P(score | total). Thus 1X2, totals and score still
come from one coherent joint matrix.
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
from football_v460_engine import _merge_parameters, load_config  # noqa: E402
from platform_core import PlatformError, load_json, score_matrix_rows  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402
from v6_total_shot_feature_offset_v6253 import (  # noqa: E402
    MIN_SHOT_COVERAGE,
    _build_rows,
    _read_stat_rows,
)

OUT = ROOT / "manifests" / "v6_total_shot_ordinal_residual_v6254_status.json"
SEED = 20260725
SAMPLE_N = 100
RIDGE_LAMBDA = 10.0
ALPHAS = (0.0, 0.25, 0.50, 0.75, 1.0)
EPS = 1e-9
LOGIT_CAP = 12.0
RESIDUAL_CAP = 2.0
MIN_TRAIN_ROWS = 120


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _base_bucket_probs(matrix: list[dict[str, Any]]) -> dict[str, float]:
    out = {key: 0.0 for key in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        out[_bucket(int(h + a))] += float(p)
    norm = sum(out.values())
    if norm <= 0.0:
        raise PlatformError("baseline total distribution has zero mass")
    return {key: value / norm for key, value in out.items()}


def _logit(p: float) -> float:
    q = min(1.0 - EPS, max(EPS, float(p)))
    return max(-LOGIT_CAP, min(LOGIT_CAP, math.log(q / (1.0 - q))))


def _sigmoid(x: float) -> float:
    z = max(-LOGIT_CAP, min(LOGIT_CAP, float(x)))
    if z >= 0:
        e = math.exp(-z)
        return 1.0 / (1.0 + e)
    e = math.exp(z)
    return e / (1.0 + e)


def _model_feature(row: dict[str, Any]) -> list[float]:
    return [
        math.log(max(EPS, float(row["base_mu"]))),
        math.log(max(EPS, float(row["k"]))),
        *[float(v) for v in row["feature"]],
    ]


def _standardize(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    features = [_model_feature(row) for row in rows]
    dim = len(features[0])
    means: list[float] = []
    sds: list[float] = []
    for j in range(dim):
        values = [x[j] for x in features]
        means.append(sum(values) / len(values))
        sds.append(max(1e-6, statistics.pstdev(values) if len(values) > 1 else 1.0))
    return means, sds


def _design(row: dict[str, Any], means: list[float], sds: list[float]) -> list[float]:
    f = _model_feature(row)
    return [1.0] + [(f[i] - means[i]) / sds[i] for i in range(len(f))]


def _solve_linear(a: list[list[float]], b: list[float]) -> list[float]:
    n = len(b)
    aug = [list(a[i]) + [float(b[i])] for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-9:
            aug[pivot][col] += 1e-5
        aug[col], aug[pivot] = aug[pivot], aug[col]
        div = aug[col][col]
        if abs(div) < 1e-12:
            div = 1e-12 if div >= 0 else -1e-12
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if abs(factor) < 1e-18:
                continue
            for j in range(col, n + 1):
                aug[r][j] -= factor * aug[col][j]
    return [aug[i][n] for i in range(n)]


def _baseline_cdfs(row: dict[str, Any]) -> list[float]:
    dist = _base_bucket_probs(row["matrix"])
    cdfs: list[float] = []
    running = 0.0
    for key in TOTAL_BUCKETS[:-1]:
        running += float(dist[key])
        cdfs.append(min(1.0 - EPS, max(EPS, running)))
    return cdfs


def _fit_threshold(
    rows: list[dict[str, Any]],
    designs: list[list[float]],
    threshold: int,
) -> list[float]:
    dim = len(designs[0])
    beta = [0.0] * dim
    for _ in range(35):
        grad = [0.0] * dim
        hess = [[0.0] * dim for _ in range(dim)]
        for row, x in zip(rows, designs):
            actual_total = int(row["home_goals"]) + int(row["away_goals"])
            y = 1.0 if actual_total <= threshold else 0.0
            offset = _logit(_baseline_cdfs(row)[threshold])
            residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(beta[j] * x[j] for j in range(dim))))
            p = _sigmoid(offset + residual)
            w = max(1e-6, p * (1.0 - p))
            err = p - y
            for j in range(dim):
                grad[j] += err * x[j]
                for k in range(dim):
                    hess[j][k] += w * x[j] * x[k]
        for j in range(1, dim):
            grad[j] += RIDGE_LAMBDA * beta[j]
            hess[j][j] += RIDGE_LAMBDA
        step = _solve_linear(hess, grad)
        beta = [beta[j] - step[j] for j in range(dim)]
        if max(abs(v) for v in step) < 1e-6:
            break
    return beta


def _fit_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < MIN_TRAIN_ROWS:
        raise PlatformError(f"ordinal shot training rows {len(rows)} < {MIN_TRAIN_ROWS}")
    means, sds = _standardize(rows)
    designs = [_design(row, means, sds) for row in rows]
    betas = [_fit_threshold(rows, designs, threshold) for threshold in range(7)]
    return {"means": means, "sds": sds, "betas": betas}


def _pava_non_decreasing(values: list[float]) -> list[float]:
    # Equal-weight pool-adjacent-violators algorithm.
    blocks: list[dict[str, float | int]] = []
    for value in values:
        blocks.append({"sum": float(value), "weight": 1, "start": len(blocks), "end": len(blocks)})
        while len(blocks) >= 2:
            left = blocks[-2]
            right = blocks[-1]
            left_mean = float(left["sum"]) / int(left["weight"])
            right_mean = float(right["sum"]) / int(right["weight"])
            if left_mean <= right_mean + 1e-15:
                break
            merged = {
                "sum": float(left["sum"]) + float(right["sum"]),
                "weight": int(left["weight"]) + int(right["weight"]),
                "start": int(left["start"]),
                "end": int(right["end"]),
            }
            blocks[-2:] = [merged]
    output: list[float] = []
    for block in blocks:
        mean = float(block["sum"]) / int(block["weight"])
        output.extend([mean] * int(block["weight"]))
    return [min(1.0 - EPS, max(EPS, value)) for value in output]


def _candidate_bucket_probs(row: dict[str, Any], model: dict[str, Any], alpha: float) -> dict[str, float]:
    base_cdf = _baseline_cdfs(row)
    x = _design(row, model["means"], model["sds"])
    raw_cdf: list[float] = []
    a = max(0.0, min(1.0, float(alpha)))
    for threshold in range(7):
        beta = model["betas"][threshold]
        residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(float(beta[j]) * x[j] for j in range(len(x)))))
        raw_cdf.append(_sigmoid(_logit(base_cdf[threshold]) + a * residual))
    cdf = _pava_non_decreasing(raw_cdf)
    probs: dict[str, float] = {}
    previous = 0.0
    for threshold, key in enumerate(TOTAL_BUCKETS[:-1]):
        probs[key] = max(0.0, cdf[threshold] - previous)
        previous = cdf[threshold]
    probs["7+"] = max(0.0, 1.0 - previous)
    norm = sum(probs.values())
    if norm <= 0.0:
        raise PlatformError("ordinal total distribution has zero mass")
    return {key: value / norm for key, value in probs.items()}


def _reweight_matrix(matrix: list[dict[str, Any]], target: dict[str, float]) -> list[dict[str, Any]]:
    base = _base_bucket_probs(matrix)
    factors = {key: float(target[key]) / max(EPS, float(base[key])) for key in TOTAL_BUCKETS}
    output: list[dict[str, Any]] = []
    total_mass = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * factors[_bucket(int(h + a))]
        output.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        total_mass += value
    if total_mass <= 0.0:
        raise PlatformError("ordinal joint matrix has zero mass")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / total_mass
    return output


def _candidate_rows(rows: list[dict[str, Any]], model: dict[str, Any] | None, alpha: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if model is None or alpha <= 0.0:
            candidate = row["matrix"]
        else:
            candidate = _reweight_matrix(row["matrix"], _candidate_bucket_probs(row, model, alpha))
        output.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": candidate,
            "alpha": float(alpha),
        })
    return output


def _load_season(
    cid: str,
    season: str,
    report: dict[str, Any],
    config: dict[str, Any],
    stats: list[Any],
) -> list[dict[str, Any]]:
    try:
        fold = _fold_for_season(report, season)
    except Exception:
        return []
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        return []
    return _build_rows(cid, season, _merge_parameters(config, selected), config, stats)


def _select_alpha(
    cid: str,
    prior: list[str],
    report: dict[str, Any],
    config: dict[str, Any],
    stats: list[Any],
) -> tuple[float, dict[str, Any]]:
    scores = {alpha: {"rps_sum": 0.0, "count": 0, "folds": []} for alpha in ALPHAS}
    for idx in range(1, len(prior)):
        training: list[dict[str, Any]] = []
        for season in prior[:idx]:
            training.extend(_load_season(cid, season, report, config, stats))
        validation = _load_season(cid, prior[idx], report, config, stats)
        if len(training) < MIN_TRAIN_ROWS or not validation:
            continue
        try:
            model = _fit_model(training)
        except Exception:
            continue
        for alpha in ALPHAS:
            rows = _candidate_rows(validation, model, alpha)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            scores[alpha]["rps_sum"] += rps * n
            scores[alpha]["count"] += n
            scores[alpha]["folds"].append({
                "validation_season": prior[idx],
                "training_seasons": list(prior[:idx]),
                "count": n,
                "mean_total_rps": rps,
            })
    eligible: list[tuple[float, float]] = []
    for alpha in ALPHAS:
        n = int(scores[alpha]["count"])
        mean_rps = scores[alpha]["rps_sum"] / n if n else None
        scores[alpha]["mean_rps"] = mean_rps
        if mean_rps is not None:
            eligible.append((float(mean_rps), float(alpha)))
    if not eligible:
        return 0.0, {"fallback": "insufficient_nested_ordinal_folds", "alpha_scores": scores}
    best_rps, best_alpha = min(eligible, key=lambda item: (item[0], item[1]))
    return best_alpha, {
        "selected_alpha": best_alpha,
        "selected_mean_rps": best_rps,
        "selection_rule": "minimum pooled nested-prior-season total RPS; tie -> smaller alpha",
        "alpha_scores": scores,
    }


def _domain(cid: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config = load_config()
    report = load_json(REPORT_ROOT / f"{cid}.json")
    stats, coverage = _read_stat_rows(cid)
    target_season = _requested_last_complete_season(cid)
    target_fold = _fold_for_season(report, target_season)
    target_selected = target_fold.get("selected_parameters")
    if not isinstance(target_selected, dict):
        raise PlatformError(f"invalid target parameters for {cid} {target_season}")
    if float(coverage["coverage"]) < MIN_SHOT_COVERAGE:
        return {
            "competition_id": cid,
            "applied": False,
            "coverage": coverage,
            "reason": "shot_coverage_below_gate",
        }, []

    target_params = _merge_parameters(config, target_selected)
    prior = [str(season) for season in (target_fold.get("prior_seasons") or [])]
    alpha, selection = _select_alpha(cid, prior, report, config, stats)
    training: list[dict[str, Any]] = []
    training_seasons: list[str] = []
    for season in prior:
        rows = _load_season(cid, season, report, config, stats)
        if rows:
            training.extend(rows)
            training_seasons.append(season)
    target = _build_rows(cid, target_season, target_params, config, stats)
    model = _fit_model(training) if alpha > 0.0 and len(training) >= MIN_TRAIN_ROWS else None
    rows = _candidate_rows(target, model, alpha)
    base = _score(rows, "baseline_matrix")
    candidate = _score(rows, "candidate_matrix")
    return {
        "competition_id": cid,
        "applied": True,
        "coverage": coverage,
        "target_season": target_season,
        "training_seasons": training_seasons,
        "training_prediction_count": len(training),
        "target_prediction_count": len(rows),
        "selected_alpha": alpha,
        "selection": selection,
        "baseline": base,
        "candidate": candidate,
        "delta": _delta(base, candidate),
        "baseline_top1_bucket_counts": _top1_counts(rows, "baseline_matrix"),
        "candidate_top1_bucket_counts": _top1_counts(rows, "candidate_matrix"),
    }, rows


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    reports: dict[str, Any] = {}
    pool: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    alpha_counts = Counter()
    for cid in competitions:
        try:
            result, rows = _domain(cid)
            reports[cid] = result
            if result.get("applied"):
                pool.extend(rows)
                alpha_counts[str(result.get("selected_alpha"))] += 1
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not pool:
        raise PlatformError("no ordinal shot-feature target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_candidate = _score(pool, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(pool))
    sampled = random.Random(SEED).sample(pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.4-shot-ordinal-residual-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_ORDINAL_SHOT_FORMAL_WEIGHT_0",
        "applied_domain_count": sum(1 for result in reports.values() if result.get("applied")),
        "eligible_target_pool_count": len(pool),
        "alpha_grid": list(ALPHAS),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "full_pool": {
            "baseline": full_base,
            "candidate": full_candidate,
            "delta": _delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "count": sample_n,
            "baseline": sample_base,
            "candidate": sample_candidate,
            "delta": _delta(sample_base, sample_candidate),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "shot_stats_prior_matches_only": True,
            "same_day_stats_excluded": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_selected_nested_prior_season_oos": True,
            "alpha_zero_exact_baseline_fallback": True,
            "baseline_total_distribution_used_as_logit_offset": True,
            "monotone_cdf_projection": "PAVA_equal_weight",
            "historical_market_odds_used": False,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in (
        "status", "applied_domain_count", "eligible_target_pool_count",
        "selected_alpha_domain_counts", "full_pool", "random100", "failures"
    )}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
