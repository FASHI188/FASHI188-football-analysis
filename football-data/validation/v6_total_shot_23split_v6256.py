#!/usr/bin/env python3
"""V6.25.6 shot-conditioned 2-vs-3 exact-total split challenger.

Research only; formal_weight=0.

The dominant modal-collapse problem is specifically the split between exact
2 and 3 total goals. This challenger leaves P(T=0,1,4,5,6,7+) and the combined
mass P(T in {2,3}) unchanged. It only models

    logit P(T=2 | T in {2,3})
      = logit baseline_split + beta' x

using strict-PIT shot/SOT features plus baseline mu_total and dispersion.
Training uses only strictly prior-season rows whose realized total is 2 or 3.
The correction is applied to every target match. Alpha is selected only by
nested prior-season OOS: total RPS must be no worse than alpha=0, then exact-total
multiclass log loss is minimized. Alpha=0 is the exact baseline fallback.

The corrected total marginal is embedded back into the same joint score matrix
while preserving P(score | total).
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

OUT = ROOT / "manifests" / "v6_total_shot_23split_v6256_status.json"
SEED = 20260725
SAMPLE_N = 100
RIDGE_LAMBDA = 10.0
ALPHAS = (0.0, 0.25, 0.50, 0.75, 1.0)
EPS = 1e-9
LOGIT_CAP = 12.0
RESIDUAL_CAP = 2.0
MIN_CONDITIONAL_TRAIN_ROWS = 80
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


def _base_split(row: dict[str, Any]) -> float:
    dist = _total_distribution(row["matrix"])
    mass = float(dist["2"]) + float(dist["3"])
    if mass <= EPS:
        return 0.5
    return float(dist["2"]) / mass


def _fit_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    conditional = [
        row for row in rows
        if int(row["home_goals"]) + int(row["away_goals"]) in (2, 3)
    ]
    if len(conditional) < MIN_CONDITIONAL_TRAIN_ROWS:
        raise PlatformError(
            f"2v3 shot training rows {len(conditional)} < {MIN_CONDITIONAL_TRAIN_ROWS}"
        )
    means, sds = _standardize(rows)
    designs = [_design(row, means, sds) for row in conditional]
    dim = len(designs[0])
    beta = [0.0] * dim
    for _ in range(35):
        grad = [0.0] * dim
        hess = [[0.0] * dim for _ in range(dim)]
        for row, x in zip(conditional, designs):
            actual_total = int(row["home_goals"]) + int(row["away_goals"])
            y = 1.0 if actual_total == 2 else 0.0
            residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(beta[j] * x[j] for j in range(dim))))
            p = _sigmoid(_logit(_base_split(row)) + residual)
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
        if max(abs(value) for value in step) < 1e-6:
            break
    return {
        "means": means,
        "sds": sds,
        "beta": beta,
        "conditional_training_count": len(conditional),
    }


def _target_probs(row: dict[str, Any], model: dict[str, Any], alpha: float) -> dict[str, float]:
    base = {key: float(value) for key, value in _total_distribution(row["matrix"]).items()}
    mass23 = base["2"] + base["3"]
    if mass23 <= EPS:
        return base
    x = _design(row, model["means"], model["sds"])
    residual = max(-RESIDUAL_CAP, min(RESIDUAL_CAP, sum(float(model["beta"][j]) * x[j] for j in range(len(x)))))
    split2 = _sigmoid(_logit(base["2"] / mass23) + float(alpha) * residual)
    target = dict(base)
    target["2"] = mass23 * split2
    target["3"] = mass23 * (1.0 - split2)
    return target


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
    for cell in output:
        cell["probability"] = float(cell["probability"]) / max(EPS, mass)
    return output


def _candidate_rows(rows: list[dict[str, Any]], model: dict[str, Any] | None, alpha: float) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        candidate = row["matrix"] if model is None or alpha <= 0.0 else _reweight_matrix(
            row["matrix"], _target_probs(row, model, alpha)
        )
        output.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": candidate,
            "alpha": float(alpha),
        })
    return output


def _total_log_loss(rows: list[dict[str, Any]], matrix_key: str) -> float:
    loss = 0.0
    for row in rows:
        actual = _bucket(int(row["home_goals"]) + int(row["away_goals"]))
        dist = _total_distribution(row[matrix_key])
        loss += -math.log(max(EPS, float(dist[actual])))
    return loss / len(rows) if rows else float("inf")


def _load_season(cid: str, season: str, report: dict[str, Any], config: dict[str, Any], stats: list[Any]) -> list[dict[str, Any]]:
    try:
        fold = _fold_for_season(report, season)
    except Exception:
        return []
    selected = fold.get("selected_parameters")
    if not isinstance(selected, dict):
        return []
    return _build_rows(cid, season, _merge_parameters(config, selected), config, stats)


def _select_alpha(cid: str, prior: list[str], report: dict[str, Any], config: dict[str, Any], stats: list[Any]) -> tuple[float, dict[str, Any]]:
    scores = {alpha: {"rps_sum": 0.0, "log_sum": 0.0, "count": 0, "folds": []} for alpha in ALPHAS}
    for idx in range(1, len(prior)):
        training: list[dict[str, Any]] = []
        for season in prior[:idx]:
            training.extend(_load_season(cid, season, report, config, stats))
        validation = _load_season(cid, prior[idx], report, config, stats)
        if not validation:
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
        return 0.0, {"fallback": "insufficient_nested_2v3_folds", "alpha_scores": scores}
    eligible = []
    for alpha in ALPHAS:
        rps = scores[alpha]["mean_rps"]
        logloss = scores[alpha]["mean_log_loss"]
        if rps is not None and logloss is not None and float(rps) <= float(baseline_rps) + RPS_TOLERANCE:
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
    selected = target_fold.get("selected_parameters")
    if not isinstance(selected, dict):
        raise PlatformError("invalid target parameters")
    if float(coverage["coverage"]) < MIN_SHOT_COVERAGE:
        return {"competition_id": cid, "applied": False, "coverage": coverage, "reason": "shot_coverage_below_gate"}, []
    config = load_config()
    target_params = _merge_parameters(config, selected)
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
    try:
        model = _fit_model(training) if alpha > 0.0 else None
    except Exception:
        model = None
        alpha = 0.0
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
        "selected_alpha": alpha,
        "selection": selection,
        "conditional_training_count": model.get("conditional_training_count") if model else None,
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
    reports = {}; pool = []; failures = {}; alpha_counts = Counter()
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
        raise PlatformError("no 2v3 shot target predictions")
    full_base = _score(pool, "baseline_matrix")
    full_candidate = _score(pool, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(pool))
    sampled = random.Random(SEED).sample(pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")
    payload = {
        "schema_version": "V6.25.6-shot-2v3-split-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_STRICT_PIT_2V3_SHOT_FORMAL_WEIGHT_0",
        "applied_domain_count": sum(1 for r in reports.values() if r.get("applied")),
        "eligible_target_pool_count": len(pool),
        "alpha_grid": list(ALPHAS),
        "selected_alpha_domain_counts": dict(alpha_counts),
        "full_pool": {
            "baseline": full_base, "candidate": full_candidate,
            "baseline_total_log_loss": _total_log_loss(pool, "baseline_matrix"),
            "candidate_total_log_loss": _total_log_loss(pool, "candidate_matrix"),
            "delta": _delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(pool, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED, "count": sample_n,
            "baseline": sample_base, "candidate": sample_candidate,
            "baseline_total_log_loss": _total_log_loss(sampled, "baseline_matrix"),
            "candidate_total_log_loss": _total_log_loss(sampled, "candidate_matrix"),
            "delta": _delta(sample_base, sample_candidate),
            "baseline_top1_bucket_counts": _top1_counts(sampled, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "failures": failures,
        "governance": {
            "only_2_and_3_bucket_split_changed": True,
            "combined_p2_p3_mass_preserved": True,
            "shot_stats_prior_matches_only": True,
            "same_day_stats_excluded": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_selected_nested_prior_season_oos": True,
            "alpha_zero_exact_baseline_fallback": True,
            "alpha_selection_requires_nested_rps_nonworse": True,
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
    print(json.dumps({k: payload[k] for k in (
        "status", "applied_domain_count", "eligible_target_pool_count", "selected_alpha_domain_counts", "full_pool", "random100", "failures"
    )}, ensure_ascii=False, indent=2))
    return 0 if not failures else 2

if __name__ == "__main__":
    raise SystemExit(main())
