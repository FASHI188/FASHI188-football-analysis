#!/usr/bin/env python3
"""V6.25.5 direct 0-7+ shot-conditioned bucket residual challenger.

Research only; formal_weight=0.

V6.25.4 directly modeled cumulative thresholds and improved RPS in some domains
but could hurt exact-total Top-1. V6.25.5 therefore targets each exact total
bucket directly while retaining the frozen baseline total distribution as prior.

For bucket k in 0,1,2,3,4,5,6,7+:
    logit r_k = logit q_k + beta_k' x
where q_k is the baseline exact-total probability and x contains log(mu_total),
log(k) and strict-PIT rolling HS/AS/HST/AST features. The eight one-vs-rest
posterior scores r_k are normalized to one distribution.

Correction strength alpha is selected per competition by nested prior-season OOS:
1. alpha must have pooled total RPS no worse than alpha=0 baseline;
2. among those, choose minimum exact-total multiclass log loss;
3. ties choose smaller alpha.
Alpha=0 exactly reproduces the baseline distribution.

The selected P(T) is re-embedded into the baseline joint score matrix while
preserving P(score | total). Target-season outcomes are never used for fitting or
alpha selection.
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
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _delta, _total_distribution  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402
from v6_total_shot_feature_offset_v6253 import MIN_SHOT_COVERAGE, _build_rows, _read_stat_rows  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_shot_bucket_residual_v6255_status.json"
SEED = 20260725
SAMPLE_N = 100
RIDGE_LAMBDA = 10.0
ALPHAS = (0.0, 0.25, 0.50, 0.75, 1.0)
EPS = 1e-9
LOGIT_CAP = 12.0
RESIDUAL_CAP = 2.0
MIN_TRAIN_ROWS = 120
RPS_TOLERANCE = 1e-12


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


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


def _base_probs(row: dict[str, Any]) -> dict[str, float]:
    return {key: float(value) for key, value in _total_distribution(row["matrix"]).items()}


def _feature(row: dict[str, Any]) -> list[float]:
    return [
        math.log(max(EPS, float(row["base_mu"]))),
        math.log(max(EPS, float(row["k"]))),
        *[float(v) for v in row["feature"]],
    ]


def _standardize(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    features = [_feature(row) for row in rows]
    dim = len(features[0])
    means: list[float] = []
    sds: list[float] = []
    for j in range(dim):
        values = [f[j] for f in features]
        means.append(sum(values) / len(values))
        sds.append(max(1e-6, statistics.pstdev(values) if len(values) > 1 else 1.0))
    return means, sds


def _design(row: dict[str, Any], means: list[float], sds: list[float]) -> list[float]:
    f = _feature(row)
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


def _fit_bucket(
    rows: list[dict[str, Any]],
    designs: list[list[float]],
    base_probs: list[dict[str, float]],
    bucket_key: str,
) -> list[float]:
    dim = len(designs[0])
    beta = [0.0] * dim
    for _ in range(30):
        grad = [0.0] * dim
        hess = [[0.0] * dim for _ in range(dim)]
        for row, x, prob in zip(rows, designs, base_probs):
            actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
            y = 1.0 if actual == bucket_key else 0.0
            residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(beta[j] * x[j] for j in range(dim))))
            p = _sigmoid(_logit(prob[bucket_key]) + residual)
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
        raise PlatformError(f"bucket shot training rows {len(rows)} < {MIN_TRAIN_ROWS}")
    means, sds = _standardize(rows)
    designs = [_design(row, means, sds) for row in rows]
    base_probs = [_base_probs(row) for row in rows]
    betas = {
        key: _fit_bucket(rows, designs, base_probs, key)
        for key in TOTAL_BUCKETS
    }
    return {"means": means, "sds": sds, "betas": betas}


def _candidate_probs(row: dict[str, Any], model: dict[str, Any], alpha: float) -> dict[str, float]:
    base = _base_probs(row)
    x = _design(row, model["means"], model["sds"])
    a = max(0.0, min(1.0, float(alpha)))
    raw: dict[str, float] = {}
    for key in TOTAL_BUCKETS:
        beta = model["betas"][key]
        residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(float(beta[j]) * x[j] for j in range(len(x)))))
        raw[key] = _sigmoid(_logit(base[key]) + a * residual)
    norm = sum(raw.values())
    if norm <= 0.0:
        raise PlatformError("bucket residual distribution has zero mass")
    return {key: raw[key] / norm for key in TOTAL_BUCKETS}


def _reweight_matrix(matrix: list[dict[str, Any]], target: dict[str, float]) -> list[dict[str, Any]]:
    base = {key: 0.0 for key in TOTAL_BUCKETS}
    for h, a, p in score_matrix_rows(matrix):
        base[_bucket(int(h + a))] += float(p)
    factors = {key: float(target[key]) / max(EPS, float(base[key])) for key in TOTAL_BUCKETS}
    output: list[dict[str, Any]] = []
    mass = 0.0
    for h, a, p in score_matrix_rows(matrix):
        value = float(p) * factors[_bucket(int(h + a))]
        output.append({"home_goals": int(h), "away_goals": int(a), "probability": value})
        mass += value
    if mass <= 0.0:
        raise PlatformError("bucket residual joint matrix has zero mass")
    for cell in output:
        cell["probability"] = float(cell["probability"]) / mass
    return output


def _candidate_rows(rows: list[dict[str, Any]], model: dict[str, Any] | None, alpha: float) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        if model is None or alpha <= 0.0:
            candidate = row["matrix"]
        else:
            candidate = _reweight_matrix(row["matrix"], _candidate_probs(row, model, alpha))
        output.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": candidate,
            "alpha": float(alpha),
        })
    return output


def _total_log_loss(rows: list[dict[str, Any]], matrix_key: str) -> float:
    total = 0.0
    for row in rows:
        actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
        prob = _total_distribution(row[matrix_key])
        total += -math.log(max(EPS, float(prob[actual])))
    return total / len(rows) if rows else float("inf")


def _load_season(cid: str, season: str, report: dict[str, Any], config: dict[str, Any], stats: list[Any]) -> list[dict[str, Any]]:
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
    scores = {
        alpha: {"rps_sum": 0.0, "log_sum": 0.0, "count": 0, "folds": []}
        for alpha in ALPHAS
    }
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
            logloss = _total_log_loss(rows, "candidate_matrix")
            scores[alpha]["rps_sum"] += rps * n
            scores[alpha]["log_sum"] += logloss * n
            scores[alpha]["count"] += n
            scores[alpha]["folds"].append({
                "validation_season": prior[idx],
                "training_seasons": list(prior[:idx]),
                "count": n,
                "mean_total_rps": rps,
                "mean_total_log_loss": logloss,
            })
    for alpha in ALPHAS:
        n = int(scores[alpha]["count"])
        scores[alpha]["mean_rps"] = scores[alpha]["rps_sum"] / n if n else None
        scores[alpha]["mean_log_loss"] = scores[alpha]["log_sum"] / n if n else None
    baseline_rps = scores[0.0]["mean_rps"]
    if baseline_rps is None:
        return 0.0, {"fallback": "insufficient_nested_bucket_folds", "alpha_scores": scores}
    eligible: list[tuple[float, float]] = []
    for alpha in ALPHAS:
        rps = scores[alpha]["mean_rps"]
        logloss = scores[alpha]["mean_log_loss"]
        if rps is None or logloss is None:
            continue
        if float(rps) <= float(baseline_rps) + RPS_TOLERANCE:
            eligible.append((float(logloss), float(alpha)))
    if not eligible:
        return 0.0, {"fallback": "no_rps_nonworse_alpha", "alpha_scores": scores}
    best_log, best_alpha = min(eligible, key=lambda item: (item[0], item[1]))
    return best_alpha, {
        "selected_alpha": best_alpha,
        "selected_mean_log_loss": best_log,
        "baseline_mean_rps": baseline_rps,
        "selection_rule": "RPS nonworse than alpha0, then minimum exact-total log loss; tie -> smaller alpha",
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
        "baseline_total_log_loss": _total_log_loss(rows, "baseline_matrix"),
        "candidate_total_log_loss": _total_log_loss(rows, "candidate_matrix"),
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
        raise PlatformError("no bucket shot-feature target predictions")

    full_base = _score(pool, "baseline_matrix")
    full_candidate = _score(pool, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(pool))
    sampled = random.Random(SEED).sample(pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.5-shot-direct-bucket-residual-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_DIRECT_BUCKET_SHOT_FORMAL_WEIGHT_0",
        "applied_domain_count": sum(1 for result in reports.values() if result.get("applied")),
        "eligible_target_pool_count": len(pool),
        "alpha_grid": list(ALPHAS),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "full_pool": {
            "baseline": full_base,
            "candidate": full_candidate,
            "baseline_total_log_loss": _total_log_loss(pool, "baseline_matrix"),
            "candidate_total_log_loss": _total_log_loss(pool, "candidate_matrix"),
            "delta": _delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "count": sample_n,
            "baseline": sample_base,
            "candidate": sample_candidate,
            "baseline_total_log_loss": _total_log_loss(sampled, "baseline_matrix"),
            "candidate_total_log_loss": _total_log_loss(sampled, "candidate_matrix"),
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
            "alpha_selection_requires_nested_rps_nonworse": True,
            "alpha_selection_optimizes_exact_total_log_loss_after_rps_gate": True,
            "baseline_total_distribution_used_as_bucket_logit_offset": True,
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
