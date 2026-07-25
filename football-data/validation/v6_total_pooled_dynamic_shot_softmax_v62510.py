#!/usr/bin/env python3
"""V6.25.10 pooled full-bucket dynamic-shot softmax residual challenger.

Research only; formal_weight=0.

V6.25.8 proved that changing only the exact 2-vs-3 split cannot materially fix
the 0-7+ modal-collapse problem. This challenger adjusts the complete total-goal
marginal in one coherent residual model:

    P_candidate(T=k | x) proportional to P_baseline(T=k) * exp(alpha * r_k(x))

for k in {0,1,2,3,4,5,6,7+}. The residual r_k(x) is a pooled multinomial
logistic head using the strict-PIT dynamic shot/SOT features already audited in
V6.25.7/8. The frozen per-match baseline total distribution remains the offset.

Chronology and governance:
- pooled training uses only prior seasons inside each domain;
- nested validation fold i trains on earlier prior seasons and validates on the
  next prior season across eligible shot-covered domains;
- no target-season result enters fitting or alpha selection;
- alpha=0 exactly recovers the baseline;
- candidate alpha must be no worse than baseline on nested-OOS total RPS and
  exact-total log loss, then exact-total Top-1 accuracy is maximized;
- the adjusted total marginal is re-embedded into the same joint score matrix,
  preserving P(score | total);
- no market odds are used; formal_weight remains 0.
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT / "engine", ROOT / "validation"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_pooled_dynamic_shot_23split_v6258 as pooled  # noqa: E402
import v6_total_shot_23split_v6256 as base  # noqa: E402
from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _total_distribution  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_pooled_dynamic_shot_softmax_v62510_status.json"

# Fixed ex-ante optimizer/model constants. They are not selected on target results.
RIDGE_LAMBDA = float(base.RIDGE_LAMBDA)
EPOCHS = 45
BATCH_SIZE = 256
LEARNING_RATE = 0.03
ADAM_BETA1 = 0.9
ADAM_BETA2 = 0.999
ADAM_EPS = 1e-8
RESIDUAL_CAP = 1.75
ALPHAS = (0.0, 0.125, 0.25, 0.50, 0.75, 1.0)
RPS_TOLERANCE = 1e-12
LOGLOSS_TOLERANCE = 1e-12
EPS = 1e-12
SEED = 20260725
SAMPLE_N = 100

_BUCKETS = list(TOTAL_BUCKETS)
_BUCKET_INDEX = {bucket: i for i, bucket in enumerate(_BUCKETS)}
_ROW_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _load(ctx: dict[str, Any], season: str) -> list[dict[str, Any]]:
    key = (str(ctx["cid"]), str(season))
    if key not in _ROW_CACHE:
        _ROW_CACHE[key] = pooled._load(ctx, season)
    return _ROW_CACHE[key]


def _feature(row: dict[str, Any]) -> list[float]:
    # Importing pooled imports V6.25.7 first, which patches base._feature to the
    # strict-PIT dynamic-shot feature contract.
    return [float(v) for v in base._feature(row)]


def _standardize(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    features = [_feature(row) for row in rows]
    if not features:
        raise PlatformError("softmax training rows unavailable")
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


def _base_probs(row: dict[str, Any]) -> list[float]:
    dist = _total_distribution(row["matrix"])
    probs = [max(EPS, float(dist[bucket])) for bucket in _BUCKETS]
    mass = sum(probs)
    return [p / mass for p in probs]


def _softmax(logits: list[float]) -> list[float]:
    m = max(logits)
    exps = [math.exp(max(-50.0, min(50.0, value - m))) for value in logits]
    total = sum(exps)
    return [value / max(EPS, total) for value in exps]


def _fit_model(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 500:
        raise PlatformError(f"pooled full-bucket training rows {len(rows)} < 500")
    means, sds = _standardize(rows)
    prepared: list[tuple[list[float], list[float], int]] = []
    for row in rows:
        x = _design(row, means, sds)
        logs = [math.log(max(EPS, p)) for p in _base_probs(row)]
        y = _BUCKET_INDEX[_bucket(int(row["home_goals"]) + int(row["away_goals"]))]
        prepared.append((x, logs, y))

    classes = len(_BUCKETS)
    dim = len(prepared[0][0])
    beta = [[0.0 for _ in range(dim)] for _ in range(classes)]
    m1 = [[0.0 for _ in range(dim)] for _ in range(classes)]
    m2 = [[0.0 for _ in range(dim)] for _ in range(classes)]
    step_count = 0
    rng = random.Random(SEED)
    order = list(range(len(prepared)))

    ridge_scale = RIDGE_LAMBDA / max(1.0, float(len(prepared)))
    for epoch in range(EPOCHS):
        rng.shuffle(order)
        max_update = 0.0
        for start in range(0, len(order), BATCH_SIZE):
            indices = order[start:start + BATCH_SIZE]
            grad = [[0.0 for _ in range(dim)] for _ in range(classes)]
            for idx in indices:
                x, base_logs, y = prepared[idx]
                residuals = []
                for c in range(classes):
                    raw = sum(beta[c][j] * x[j] for j in range(dim))
                    residuals.append(max(-RESIDUAL_CAP, min(RESIDUAL_CAP, raw)))
                probs = _softmax([base_logs[c] + residuals[c] for c in range(classes)])
                for c in range(classes):
                    err = probs[c] - (1.0 if c == y else 0.0)
                    for j in range(dim):
                        grad[c][j] += err * x[j]

            batch_n = max(1.0, float(len(indices)))
            step_count += 1
            b1_corr = 1.0 - ADAM_BETA1 ** step_count
            b2_corr = 1.0 - ADAM_BETA2 ** step_count
            for c in range(classes):
                for j in range(dim):
                    g = grad[c][j] / batch_n
                    if j > 0:
                        g += ridge_scale * beta[c][j]
                    m1[c][j] = ADAM_BETA1 * m1[c][j] + (1.0 - ADAM_BETA1) * g
                    m2[c][j] = ADAM_BETA2 * m2[c][j] + (1.0 - ADAM_BETA2) * g * g
                    mhat = m1[c][j] / max(EPS, b1_corr)
                    vhat = m2[c][j] / max(EPS, b2_corr)
                    update = LEARNING_RATE * mhat / (math.sqrt(vhat) + ADAM_EPS)
                    beta[c][j] -= update
                    max_update = max(max_update, abs(update))

            # Remove the non-identifiable common class shift exactly.
            for j in range(dim):
                mean_beta = sum(beta[c][j] for c in range(classes)) / classes
                for c in range(classes):
                    beta[c][j] -= mean_beta

        if epoch >= 10 and max_update < 1e-5:
            break

    return {
        "means": means,
        "sds": sds,
        "beta": beta,
        "training_count": len(rows),
        "optimizer": {
            "epochs_requested": EPOCHS,
            "epochs_completed": epoch + 1,
            "batch_size": BATCH_SIZE,
            "learning_rate": LEARNING_RATE,
            "ridge_lambda_sum_loss_equivalent": RIDGE_LAMBDA,
            "residual_cap": RESIDUAL_CAP,
            "seed": SEED,
        },
    }


def _target_probs(row: dict[str, Any], model: dict[str, Any], alpha: float) -> dict[str, float]:
    base_probs = _base_probs(row)
    x = _design(row, model["means"], model["sds"])
    residuals: list[float] = []
    for c in range(len(_BUCKETS)):
        raw = sum(float(model["beta"][c][j]) * x[j] for j in range(len(x)))
        residuals.append(max(-RESIDUAL_CAP, min(RESIDUAL_CAP, raw)))
    logits = [math.log(max(EPS, base_probs[c])) + float(alpha) * residuals[c] for c in range(len(_BUCKETS))]
    probs = _softmax(logits)
    return {bucket: probs[i] for i, bucket in enumerate(_BUCKETS)}


def _candidate_rows(
    rows: list[dict[str, Any]],
    model: dict[str, Any] | None,
    alpha: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        candidate = row["matrix"]
        if model is not None and alpha > 0.0:
            target = _target_probs(row, model, alpha)
            candidate = base._reweight_matrix(row["matrix"], target)
        output.append({
            **row,
            "baseline_matrix": row["matrix"],
            "candidate_matrix": candidate,
            "alpha": float(alpha),
        })
    return output


def _total_log_loss(rows: list[dict[str, Any]], matrix_key: str) -> float:
    return base._total_log_loss(rows, matrix_key)


def _aggregate_alpha_scores(
    scores: dict[float, dict[str, Any]],
) -> dict[float, dict[str, Any]]:
    for alpha in ALPHAS:
        item = scores[alpha]
        n = int(item["count"])
        item["mean_rps"] = item["rps_sum"] / n if n else None
        item["mean_log_loss"] = item["log_sum"] / n if n else None
        item["top1_accuracy"] = item["top1_hits"] / n if n else None
        item["one_x_two_mean_brier"] = item["brier_sum"] / n if n else None
        item["score_mean_joint_log_score"] = item["joint_log_sum"] / n if n else None
    return scores


def _select_global_alpha(contexts: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    scores = {
        alpha: {
            "rps_sum": 0.0,
            "log_sum": 0.0,
            "top1_hits": 0,
            "brier_sum": 0.0,
            "joint_log_sum": 0.0,
            "count": 0,
            "folds": [],
        }
        for alpha in ALPHAS
    }
    max_prior = max((len(ctx["prior"]) for ctx in contexts), default=0)
    for idx in range(1, max_prior):
        training: list[dict[str, Any]] = []
        validation: list[dict[str, Any]] = []
        fold_domains: list[str] = []
        for ctx in contexts:
            prior = ctx["prior"]
            if idx >= len(prior):
                continue
            for season in prior[:idx]:
                training.extend(_load(ctx, season))
            rows = _load(ctx, prior[idx])
            if rows:
                validation.extend(rows)
                fold_domains.append(ctx["cid"])
        if not validation:
            continue
        try:
            model = _fit_model(training)
        except Exception as exc:
            for alpha in ALPHAS:
                scores[alpha]["folds"].append({
                    "fold_index": idx,
                    "domains": list(fold_domains),
                    "training_count": len(training),
                    "validation_count": len(validation),
                    "fit_failure": f"{type(exc).__name__}: {exc}",
                })
            continue

        for alpha in ALPHAS:
            rows = _candidate_rows(validation, model if alpha > 0.0 else None, alpha)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            logloss = _total_log_loss(rows, "candidate_matrix")
            top1_hits = int(metric["total_goals_0_7plus"]["top1_hits"])
            brier = float(metric["one_x_two"]["mean_brier"])
            joint_log = float(metric["score"]["mean_joint_log_score"])
            item = scores[alpha]
            item["rps_sum"] += rps * n
            item["log_sum"] += logloss * n
            item["top1_hits"] += top1_hits
            item["brier_sum"] += brier * n
            item["joint_log_sum"] += joint_log * n
            item["count"] += n
            item["folds"].append({
                "fold_index": idx,
                "domains": list(fold_domains),
                "training_count": len(training),
                "validation_count": n,
                "mean_total_rps": rps,
                "mean_total_log_loss": logloss,
                "total_top1_accuracy": float(metric["total_goals_0_7plus"]["top1_accuracy"]),
                "one_x_two_mean_brier": brier,
                "score_mean_joint_log_score": joint_log,
            })

    _aggregate_alpha_scores(scores)
    baseline_rps = scores[0.0]["mean_rps"]
    baseline_log = scores[0.0]["mean_log_loss"]
    if baseline_rps is None or baseline_log is None:
        return 0.0, {"fallback": "insufficient_pooled_nested_folds", "alpha_scores": scores}

    eligible: list[tuple[float, float, float, float]] = []
    for alpha in ALPHAS:
        rps = scores[alpha]["mean_rps"]
        logloss = scores[alpha]["mean_log_loss"]
        accuracy = scores[alpha]["top1_accuracy"]
        if rps is None or logloss is None or accuracy is None:
            continue
        if (
            float(rps) <= float(baseline_rps) + RPS_TOLERANCE
            and float(logloss) <= float(baseline_log) + LOGLOSS_TOLERANCE
        ):
            eligible.append((-float(accuracy), float(rps), float(logloss), float(alpha)))

    if not eligible:
        return 0.0, {"fallback": "no_pooled_proper_score_nonworse_alpha", "alpha_scores": scores}

    _, selected_rps, selected_log, selected_alpha = min(eligible)
    return selected_alpha, {
        "selected_alpha": selected_alpha,
        "selected_mean_rps": selected_rps,
        "selected_mean_log_loss": selected_log,
        "selected_top1_accuracy": scores[selected_alpha]["top1_accuracy"],
        "baseline_mean_rps": baseline_rps,
        "baseline_mean_log_loss": baseline_log,
        "baseline_top1_accuracy": scores[0.0]["top1_accuracy"],
        "selection_rule": (
            "pooled nested-OOS total RPS and total log loss both nonworse than alpha=0; "
            "then maximize exact-total Top1; tie -> lower RPS, lower log loss, smaller alpha"
        ),
        "alpha_scores": scores,
    }


def _delta(base_metric: dict[str, Any], cand_metric: dict[str, Any]) -> dict[str, float]:
    return {
        "one_x_two_mean_brier": float(cand_metric["one_x_two"]["mean_brier"]) - float(base_metric["one_x_two"]["mean_brier"]),
        "one_x_two_mean_log_loss": float(cand_metric["one_x_two"]["mean_log_loss"]) - float(base_metric["one_x_two"]["mean_log_loss"]),
        "one_x_two_mean_rps": float(cand_metric["one_x_two"]["mean_rps"]) - float(base_metric["one_x_two"]["mean_rps"]),
        "one_x_two_top1_accuracy": float(cand_metric["one_x_two"]["top1_accuracy"]) - float(base_metric["one_x_two"]["top1_accuracy"]),
        "score_mean_joint_log_score": float(cand_metric["score"]["mean_joint_log_score"]) - float(base_metric["score"]["mean_joint_log_score"]),
        "score_top1_accuracy": float(cand_metric["score"]["top1_accuracy"]) - float(base_metric["score"]["top1_accuracy"]),
        "score_top3_accuracy": float(cand_metric["score"]["top3_accuracy"]) - float(base_metric["score"]["top3_accuracy"]),
        "total_goals_mean_rps": float(cand_metric["total_goals_0_7plus"]["mean_rps"]) - float(base_metric["total_goals_0_7plus"]["mean_rps"]),
        "total_goals_top1_accuracy": float(cand_metric["total_goals_0_7plus"]["top1_accuracy"]) - float(base_metric["total_goals_0_7plus"]["top1_accuracy"]),
    }


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    contexts: list[dict[str, Any]] = []
    unavailable: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in competitions:
        try:
            ctx = pooled._domain_context(cid)
            if ctx is None:
                unavailable[cid] = "shot_coverage_or_target_fold_unavailable"
            else:
                contexts.append(ctx)
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"
    if not contexts:
        raise PlatformError("no shot-covered pooled domains")

    alpha, selection = _select_global_alpha(contexts)

    training: list[dict[str, Any]] = []
    target_pool: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}
    for ctx in contexts:
        domain_training: list[dict[str, Any]] = []
        training_seasons: list[str] = []
        for season in ctx["prior"]:
            rows = _load(ctx, season)
            if rows:
                domain_training.extend(rows)
                training_seasons.append(season)
        training.extend(domain_training)

        target_key = (str(ctx["cid"]), str(ctx["target_season"]))
        if target_key not in _ROW_CACHE:
            _ROW_CACHE[target_key] = pooled.dynamic._build_rows_dynamic(
                ctx["cid"],
                ctx["target_season"],
                ctx["target_params"],
                ctx["config"],
                ctx["stats"],
            )
        target = _ROW_CACHE[target_key]
        reports[ctx["cid"]] = {
            "coverage": ctx["coverage"],
            "target_season": ctx["target_season"],
            "training_seasons": training_seasons,
            "training_prediction_count": len(domain_training),
            "target_prediction_count": len(target),
        }
        target_pool.extend(target)

    model = _fit_model(training) if alpha > 0.0 else None
    candidate_pool = _candidate_rows(target_pool, model, alpha)

    full_base = _score(candidate_pool, "baseline_matrix")
    full_candidate = _score(candidate_pool, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(candidate_pool))
    sampled = random.Random(SEED).sample(candidate_pool, sample_n)
    sample_base = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.10-pooled-dynamic-shot-full-softmax-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RESEARCH_CHALLENGER_POOLED_STRICT_PIT_FULL_TOTAL_SOFTMAX_FORMAL_WEIGHT_0",
        "applied_domains": [ctx["cid"] for ctx in contexts],
        "applied_domain_count": len(contexts),
        "eligible_target_pool_count": len(candidate_pool),
        "training_prediction_count": len(training),
        "selected_alpha": alpha,
        "selection": selection,
        "model": {
            "total_buckets": _BUCKETS,
            "full_distribution_adjusted": True,
            "baseline_distribution_used_as_multinomial_offset": True,
            "dynamic_shot_feature_contract": "V6.25.7 strict-PIT shot/SOT + recency + venue state",
            "optimizer": model["optimizer"] if model is not None else None,
        },
        "full_pool": {
            "baseline": full_base,
            "candidate": full_candidate,
            "baseline_total_log_loss": _total_log_loss(candidate_pool, "baseline_matrix"),
            "candidate_total_log_loss": _total_log_loss(candidate_pool, "candidate_matrix"),
            "delta": _delta(full_base, full_candidate),
            "baseline_top1_bucket_counts": _top1_counts(candidate_pool, "baseline_matrix"),
            "candidate_top1_bucket_counts": _top1_counts(candidate_pool, "candidate_matrix"),
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
        "unavailable_domains": unavailable,
        "failures": failures,
        "governance": {
            "pooled_training_across_shot_domains": True,
            "domain_baseline_distribution_remains_probability_offset": True,
            "nested_validation_strictly_prior_inside_each_domain": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "alpha_zero_exact_baseline_fallback": True,
            "alpha_selection_requires_total_rps_and_logloss_nonworse": True,
            "exact_top1_used_only_after_proper_score_gate": True,
            "all_0_7plus_total_buckets_may_change": True,
            "one_joint_matrix_only": True,
            "conditional_score_given_total_preserved": True,
            "historical_market_odds_used": False,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "applied_domain_count": payload["applied_domain_count"],
        "eligible_target_pool_count": payload["eligible_target_pool_count"],
        "training_prediction_count": payload["training_prediction_count"],
        "selected_alpha": payload["selected_alpha"],
        "selection": payload["selection"],
        "full_pool": payload["full_pool"],
        "random100": payload["random100"],
        "failures": payload["failures"],
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
