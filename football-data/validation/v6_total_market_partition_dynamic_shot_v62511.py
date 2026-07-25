#!/usr/bin/env python3
"""V6.25.11 historical O/U2.5-partition + dynamic-shot exact-total challenger.

Research only; formal_weight=0.

Motivation
----------
V6.24.2 showed that the formal 0-7+ marginal is reasonably calibrated in the
aggregate but its per-match Top-1 collapses mostly to 2 goals. V6.16.2 showed
that a retrospective de-vigged O/U2.5 constraint improves full-pool Total RPS
and exact-total Top-1, but a single 2.5 line only identifies the partition
P(T<=2) versus P(T>=3); it does not identify the exact buckets inside either
partition. V6.25.10 also showed that an unconstrained pooled 8-class residual
head is rejected by proper-score + Top-1 gating.

This challenger therefore respects the information geometry of O/U2.5:
1. KL-project the formal total marginal onto the market P(T>=3), exactly as
   V6.16.2. This fixes the low/high partition masses.
2. Fit TWO pooled strict-PIT multinomial residual heads only for the conditional
   distributions inside the partitions:
      low  = {0,1,2}
      high = {3,4,5,6,7+}
3. Features are the already audited V6.25.7 dynamic shot/SOT state plus the
   de-vigged market logit P(T>=3). The market-projected conditional distribution
   is the multinomial offset.
4. Candidate reconstruction preserves the market low/high mass exactly and
   preserves P(score | total) when mapped back into the same joint matrix.

Chronology
----------
Nested validation is chronological inside every domain. Fold i trains only on
market-matched rows from earlier prior seasons and validates on the next prior
season. Target-season outcomes never enter model fitting or alpha selection.
Historical market rows do not carry original quote timestamps, so this is only
a retrospective method test and is NOT promotion eligible.
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

import validate_joint_market_ipf_crossseason_v6164 as cross  # noqa: E402
import validate_market_ou_kl_projection_v6162 as ou  # noqa: E402
import v6_total_pooled_dynamic_shot_23split_v6258 as pooled  # noqa: E402
import v6_total_pooled_dynamic_shot_softmax_v62510 as full  # noqa: E402
import v6_total_shot_23split_v6256 as split  # noqa: E402
from backtest_last_complete_season_all_domains_v470 import FORMAL_STATUS  # noqa: E402
from platform_core import PlatformError, load_json  # noqa: E402
from v6_team_regime_state_runner_v6240 import TOTAL_BUCKETS, _total_distribution  # noqa: E402
from v6_total_distribution_pit_calibration_v6244 import _score, _top1_counts  # noqa: E402

OUT = ROOT / "manifests" / "v6_total_market_partition_dynamic_shot_v62511_status.json"

LOW = ("0", "1", "2")
HIGH = ("3", "4", "5", "6", "7+")
ALPHAS = (0.0, 0.125, 0.25, 0.50, 0.75)
RIDGE_LAMBDA = 10.0
EPOCHS = 40
BATCH_SIZE = 256
LEARNING_RATE = 0.025
BETA1 = 0.9
BETA2 = 0.999
ADAM_EPS = 1e-8
RESIDUAL_CAP = 1.5
EPS = 1e-12
SEED = 20260725
SAMPLE_N = 100
RPS_TOLERANCE = 1e-12
LOGLOSS_TOLERANCE = 1e-12

_ROW_CACHE: dict[tuple[str, str], list[dict[str, Any]]] = {}
_MARKET_CACHE: dict[tuple[str, str], dict[Any, Any]] = {}


def _bucket(total: int) -> str:
    return str(total) if total <= 6 else "7+"


def _logit(p: float) -> float:
    q = min(1.0 - 1e-9, max(1e-9, float(p)))
    return math.log(q / (1.0 - q))


def _market_lookup(cid: str, season: str) -> dict[Any, Any]:
    key = (cid, season)
    if key not in _MARKET_CACHE:
        _MARKET_CACHE[key] = cross.market_lookup(cid, season)
    return _MARKET_CACHE[key]


def _market_rows(ctx: dict[str, Any], season: str) -> list[dict[str, Any]]:
    key = (str(ctx["cid"]), str(season))
    if key in _ROW_CACHE:
        return _ROW_CACHE[key]
    rows = pooled._load(ctx, season)
    lookup = _market_lookup(ctx["cid"], season)
    out: list[dict[str, Any]] = []
    for row in rows:
        mk = lookup.get((str(row["date"]), row["home_team"], row["away_team"]))
        if not mk:
            continue
        q = float(mk["p_over25"])
        formal_total = _total_distribution(row["matrix"])
        market_total = ou.project(formal_total, q)
        if market_total is None:
            continue
        market_matrix = split._reweight_matrix(row["matrix"], market_total)
        out.append({
            **row,
            "p_over25": q,
            "market_total": {k: float(market_total[k]) for k in TOTAL_BUCKETS},
            "market_matrix": market_matrix,
        })
    _ROW_CACHE[key] = out
    return out


def _feature(row: dict[str, Any]) -> list[float]:
    # full._feature resolves to the V6.25.7 dynamic strict-PIT feature contract.
    base_feature = [float(v) for v in full._feature(row)]
    m = row["market_total"]
    market_mean = sum((7 if k == "7+" else int(k)) * float(m[k]) for k in TOTAL_BUCKETS)
    formal = _total_distribution(row["matrix"])
    formal_mean = sum((7 if k == "7+" else int(k)) * float(formal[k]) for k in TOTAL_BUCKETS)
    return [*base_feature, _logit(float(row["p_over25"])), market_mean - formal_mean]


def _standardize(rows: list[dict[str, Any]]) -> tuple[list[float], list[float]]:
    feats = [_feature(row) for row in rows]
    if not feats:
        raise PlatformError("partition-head training rows unavailable")
    dim = len(feats[0])
    means: list[float] = []
    sds: list[float] = []
    for j in range(dim):
        values = [f[j] for f in feats]
        means.append(sum(values) / len(values))
        sds.append(max(1e-6, statistics.pstdev(values) if len(values) > 1 else 1.0))
    return means, sds


def _design(row: dict[str, Any], means: list[float], sds: list[float]) -> list[float]:
    f = _feature(row)
    return [1.0] + [(f[i] - means[i]) / sds[i] for i in range(len(f))]


def _softmax(values: list[float]) -> list[float]:
    m = max(values)
    exps = [math.exp(max(-50.0, min(50.0, v - m))) for v in values]
    s = sum(exps)
    return [v / max(EPS, s) for v in exps]


def _conditional_offset(row: dict[str, Any], buckets: tuple[str, ...]) -> list[float]:
    total = row["market_total"]
    mass = sum(float(total[b]) for b in buckets)
    return [max(EPS, float(total[b]) / max(EPS, mass)) for b in buckets]


def _fit_head(rows: list[dict[str, Any]], buckets: tuple[str, ...], name: str) -> dict[str, Any]:
    allowed = set(buckets)
    conditional = [
        row for row in rows
        if _bucket(int(row["home_goals"]) + int(row["away_goals"])) in allowed
    ]
    if len(conditional) < 400:
        raise PlatformError(f"{name} conditional training rows {len(conditional)} < 400")
    means, sds = _standardize(conditional)
    index = {b: i for i, b in enumerate(buckets)}
    prepared: list[tuple[list[float], list[float], int]] = []
    for row in conditional:
        x = _design(row, means, sds)
        offset = [math.log(max(EPS, p)) for p in _conditional_offset(row, buckets)]
        y = index[_bucket(int(row["home_goals"]) + int(row["away_goals"]))]
        prepared.append((x, offset, y))

    classes = len(buckets)
    dim = len(prepared[0][0])
    beta = [[0.0 for _ in range(dim)] for _ in range(classes)]
    m1 = [[0.0 for _ in range(dim)] for _ in range(classes)]
    m2 = [[0.0 for _ in range(dim)] for _ in range(classes)]
    order = list(range(len(prepared)))
    rng = random.Random(SEED + (11 if name == "low" else 29))
    step = 0
    ridge_scale = RIDGE_LAMBDA / max(1.0, float(len(prepared)))

    for epoch in range(EPOCHS):
        rng.shuffle(order)
        max_update = 0.0
        for start in range(0, len(order), BATCH_SIZE):
            ids = order[start:start + BATCH_SIZE]
            grad = [[0.0 for _ in range(dim)] for _ in range(classes)]
            for idx in ids:
                x, offset, y = prepared[idx]
                residual = []
                for c in range(classes):
                    raw = sum(beta[c][j] * x[j] for j in range(dim))
                    residual.append(max(-RESIDUAL_CAP, min(RESIDUAL_CAP, raw)))
                probs = _softmax([offset[c] + residual[c] for c in range(classes)])
                for c in range(classes):
                    err = probs[c] - (1.0 if c == y else 0.0)
                    for j in range(dim):
                        grad[c][j] += err * x[j]
            n = max(1.0, float(len(ids)))
            step += 1
            b1c = 1.0 - BETA1 ** step
            b2c = 1.0 - BETA2 ** step
            for c in range(classes):
                for j in range(dim):
                    g = grad[c][j] / n
                    if j > 0:
                        g += ridge_scale * beta[c][j]
                    m1[c][j] = BETA1 * m1[c][j] + (1.0 - BETA1) * g
                    m2[c][j] = BETA2 * m2[c][j] + (1.0 - BETA2) * g * g
                    mh = m1[c][j] / max(EPS, b1c)
                    vh = m2[c][j] / max(EPS, b2c)
                    update = LEARNING_RATE * mh / (math.sqrt(vh) + ADAM_EPS)
                    beta[c][j] -= update
                    max_update = max(max_update, abs(update))
            for j in range(dim):
                shift = sum(beta[c][j] for c in range(classes)) / classes
                for c in range(classes):
                    beta[c][j] -= shift
        if epoch >= 10 and max_update < 1e-5:
            break

    return {
        "name": name,
        "buckets": list(buckets),
        "means": means,
        "sds": sds,
        "beta": beta,
        "conditional_training_count": len(conditional),
        "epochs_completed": epoch + 1,
    }


def _head_conditional(row: dict[str, Any], model: dict[str, Any], alpha: float) -> dict[str, float]:
    buckets = tuple(str(x) for x in model["buckets"])
    x = _design(row, model["means"], model["sds"])
    offset = [math.log(max(EPS, p)) for p in _conditional_offset(row, buckets)]
    residual: list[float] = []
    for c in range(len(buckets)):
        raw = sum(float(model["beta"][c][j]) * x[j] for j in range(len(x)))
        residual.append(max(-RESIDUAL_CAP, min(RESIDUAL_CAP, raw)))
    probs = _softmax([offset[c] + float(alpha) * residual[c] for c in range(len(buckets))])
    return {b: probs[i] for i, b in enumerate(buckets)}


def _target_total(
    row: dict[str, Any],
    low_model: dict[str, Any],
    high_model: dict[str, Any],
    alpha: float,
) -> dict[str, float]:
    market = {k: float(row["market_total"][k]) for k in TOTAL_BUCKETS}
    low_mass = sum(market[b] for b in LOW)
    high_mass = sum(market[b] for b in HIGH)
    low_cond = _head_conditional(row, low_model, alpha)
    high_cond = _head_conditional(row, high_model, alpha)
    out = {k: 0.0 for k in TOTAL_BUCKETS}
    for b in LOW:
        out[b] = low_mass * low_cond[b]
    for b in HIGH:
        out[b] = high_mass * high_cond[b]
    mass = sum(out.values())
    return {k: out[k] / max(EPS, mass) for k in TOTAL_BUCKETS}


def _candidate_rows(
    rows: list[dict[str, Any]],
    low_model: dict[str, Any] | None,
    high_model: dict[str, Any] | None,
    alpha: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if low_model is None or high_model is None or alpha <= 0.0:
            candidate = row["market_matrix"]
        else:
            candidate = split._reweight_matrix(row["matrix"], _target_total(row, low_model, high_model, alpha))
        out.append({
            **row,
            "formal_matrix": row["matrix"],
            "baseline_matrix": row["market_matrix"],
            "candidate_matrix": candidate,
            "alpha": float(alpha),
        })
    return out


def _fit_models(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    return _fit_head(rows, LOW, "low"), _fit_head(rows, HIGH, "high")


def _selection(contexts: list[dict[str, Any]]) -> tuple[float, dict[str, Any]]:
    scores = {
        a: {"count": 0, "rps_sum": 0.0, "log_sum": 0.0, "top1_hits": 0, "folds": []}
        for a in ALPHAS
    }
    max_prior = max((len(ctx["prior"]) for ctx in contexts), default=0)
    for idx in range(1, max_prior):
        train: list[dict[str, Any]] = []
        valid: list[dict[str, Any]] = []
        domains: list[str] = []
        for ctx in contexts:
            prior = ctx["prior"]
            if idx >= len(prior):
                continue
            for season in prior[:idx]:
                train.extend(_market_rows(ctx, season))
            vr = _market_rows(ctx, prior[idx])
            if vr:
                valid.extend(vr)
                domains.append(ctx["cid"])
        if not valid:
            continue
        try:
            low_model, high_model = _fit_models(train)
        except Exception as exc:
            for a in ALPHAS:
                scores[a]["folds"].append({
                    "fold_index": idx,
                    "domains": domains,
                    "training_count": len(train),
                    "validation_count": len(valid),
                    "fit_failure": f"{type(exc).__name__}: {exc}",
                })
            continue
        for a in ALPHAS:
            rows = _candidate_rows(valid, low_model if a > 0 else None, high_model if a > 0 else None, a)
            metric = _score(rows, "candidate_matrix")
            n = int(metric["count"])
            rps = float(metric["total_goals_0_7plus"]["mean_rps"])
            logloss = split._total_log_loss(rows, "candidate_matrix")
            hits = int(metric["total_goals_0_7plus"]["top1_hits"])
            s = scores[a]
            s["count"] += n
            s["rps_sum"] += rps * n
            s["log_sum"] += logloss * n
            s["top1_hits"] += hits
            s["folds"].append({
                "fold_index": idx,
                "domains": domains,
                "training_count": len(train),
                "validation_count": n,
                "mean_total_rps": rps,
                "mean_total_log_loss": logloss,
                "total_top1_accuracy": float(metric["total_goals_0_7plus"]["top1_accuracy"]),
            })
    for a in ALPHAS:
        n = int(scores[a]["count"])
        scores[a]["mean_rps"] = scores[a]["rps_sum"] / n if n else None
        scores[a]["mean_log_loss"] = scores[a]["log_sum"] / n if n else None
        scores[a]["top1_accuracy"] = scores[a]["top1_hits"] / n if n else None
    br = scores[0.0]["mean_rps"]
    bl = scores[0.0]["mean_log_loss"]
    if br is None or bl is None:
        return 0.0, {"fallback": "insufficient_nested_market_folds", "alpha_scores": scores}
    eligible: list[tuple[float, float, float, float]] = []
    for a in ALPHAS:
        r = scores[a]["mean_rps"]
        l = scores[a]["mean_log_loss"]
        acc = scores[a]["top1_accuracy"]
        if r is None or l is None or acc is None:
            continue
        if float(r) <= float(br) + RPS_TOLERANCE and float(l) <= float(bl) + LOGLOSS_TOLERANCE:
            eligible.append((-float(acc), float(r), float(l), float(a)))
    if not eligible:
        return 0.0, {"fallback": "no_market_partition_proper_score_nonworse_alpha", "alpha_scores": scores}
    _, sr, sl, sa = min(eligible)
    return sa, {
        "selected_alpha": sa,
        "selected_top1_accuracy": scores[sa]["top1_accuracy"],
        "selected_mean_rps": sr,
        "selected_mean_log_loss": sl,
        "market_baseline_top1_accuracy": scores[0.0]["top1_accuracy"],
        "market_baseline_mean_rps": br,
        "market_baseline_mean_log_loss": bl,
        "selection_rule": (
            "nested-OOS candidate must be nonworse than market O/U2.5 baseline on Total RPS and total log loss; "
            "then maximize exact-total Top1, tie -> lower RPS, lower log loss, smaller alpha"
        ),
        "alpha_scores": scores,
    }


def _delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, float]:
    return {
        "total_top1_accuracy": float(b["total_goals_0_7plus"]["top1_accuracy"]) - float(a["total_goals_0_7plus"]["top1_accuracy"]),
        "total_mean_rps": float(b["total_goals_0_7plus"]["mean_rps"]) - float(a["total_goals_0_7plus"]["mean_rps"]),
        "score_top1_accuracy": float(b["score"]["top1_accuracy"]) - float(a["score"]["top1_accuracy"]),
        "score_top3_accuracy": float(b["score"]["top3_accuracy"]) - float(a["score"]["top3_accuracy"]),
        "one_x_two_mean_brier": float(b["one_x_two"]["mean_brier"]) - float(a["one_x_two"]["mean_brier"]),
    }


def _mode_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    return _top1_counts(rows, key)


def main() -> int:
    formal = load_json(FORMAL_STATUS)
    competitions = sorted((formal.get("reports") or {}).keys())
    contexts: list[dict[str, Any]] = []
    unavailable: dict[str, str] = {}
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
        raise PlatformError("no eligible shot-covered domains")

    alpha, selection = _selection(contexts)

    train: list[dict[str, Any]] = []
    target: list[dict[str, Any]] = []
    reports: dict[str, Any] = {}
    for ctx in contexts:
        domain_train: list[dict[str, Any]] = []
        for season in ctx["prior"]:
            domain_train.extend(_market_rows(ctx, season))
        train.extend(domain_train)
        tr = _market_rows(ctx, ctx["target_season"])
        target.extend(tr)
        reports[ctx["cid"]] = {
            "target_season": ctx["target_season"],
            "prior_market_training_count": len(domain_train),
            "target_market_count": len(tr),
            "shot_coverage": ctx["coverage"],
        }
    if not target:
        raise PlatformError("no target-season market-matched rows")

    low_model: dict[str, Any] | None = None
    high_model: dict[str, Any] | None = None
    if alpha > 0.0:
        low_model, high_model = _fit_models(train)
    rows = _candidate_rows(target, low_model, high_model, alpha)

    formal_metric = _score(rows, "formal_matrix")
    market_metric = _score(rows, "baseline_matrix")
    candidate_metric = _score(rows, "candidate_matrix")
    sample_n = min(SAMPLE_N, len(rows))
    sampled = random.Random(SEED).sample(rows, sample_n)
    sample_formal = _score(sampled, "formal_matrix")
    sample_market = _score(sampled, "baseline_matrix")
    sample_candidate = _score(sampled, "candidate_matrix")

    payload = {
        "schema_version": "V6.25.11-market-partition-dynamic-shot-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "formal_current_version": "V5.0.1",
        "classification": "RETROSPECTIVE_MARKET_RESEARCH_PARTITION_HEAD_FORMAL_WEIGHT_0",
        "applied_domains": [ctx["cid"] for ctx in contexts],
        "target_market_count": len(rows),
        "training_market_count": len(train),
        "selected_alpha": alpha,
        "selection": selection,
        "model": {
            "market_constraint": "de-vigged O/U2.5 fixes P(T<=2) and P(T>=3)",
            "low_head": list(LOW),
            "high_head": list(HIGH),
            "features": "V6.25.7 strict-PIT dynamic shot/SOT state + market logit + market mean shift",
            "conditional_heads_preserve_market_partition_mass": True,
            "conditional_score_given_total_preserved": True,
            "low_model": {
                "conditional_training_count": low_model["conditional_training_count"],
                "epochs_completed": low_model["epochs_completed"],
            } if low_model else None,
            "high_model": {
                "conditional_training_count": high_model["conditional_training_count"],
                "epochs_completed": high_model["epochs_completed"],
            } if high_model else None,
        },
        "full_target": {
            "formal": formal_metric,
            "market_ou25": market_metric,
            "candidate": candidate_metric,
            "market_minus_formal": _delta(formal_metric, market_metric),
            "candidate_minus_market": _delta(market_metric, candidate_metric),
            "candidate_minus_formal": _delta(formal_metric, candidate_metric),
            "formal_total_log_loss": split._total_log_loss(rows, "formal_matrix"),
            "market_total_log_loss": split._total_log_loss(rows, "baseline_matrix"),
            "candidate_total_log_loss": split._total_log_loss(rows, "candidate_matrix"),
            "formal_mode_counts": _mode_counts(rows, "formal_matrix"),
            "market_mode_counts": _mode_counts(rows, "baseline_matrix"),
            "candidate_mode_counts": _mode_counts(rows, "candidate_matrix"),
        },
        "random100": {
            "seed": SEED,
            "count": sample_n,
            "formal": sample_formal,
            "market_ou25": sample_market,
            "candidate": sample_candidate,
            "market_minus_formal": _delta(sample_formal, sample_market),
            "candidate_minus_market": _delta(sample_market, sample_candidate),
            "candidate_minus_formal": _delta(sample_formal, sample_candidate),
            "formal_mode_counts": _mode_counts(sampled, "formal_matrix"),
            "market_mode_counts": _mode_counts(sampled, "baseline_matrix"),
            "candidate_mode_counts": _mode_counts(sampled, "candidate_matrix"),
        },
        "reports": reports,
        "unavailable_domains": unavailable,
        "failures": failures,
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "current_rule_change": False,
            "automatic_promotion": False,
            "historical_market_quotes_lack_original_timestamp": True,
            "historical_market_not_formal_snapshot": True,
            "nested_validation_strictly_prior_seasons": True,
            "target_results_used_for_training_or_alpha_selection": False,
            "market_partition_mass_preserved_exactly": True,
            "one_joint_matrix_only": True,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "status": payload["status"],
        "target_market_count": len(rows),
        "training_market_count": len(train),
        "selected_alpha": alpha,
        "selection": selection,
        "full_target": payload["full_target"],
        "random100": payload["random100"],
        "failures": failures,
    }, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
