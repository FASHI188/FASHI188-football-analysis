#!/usr/bin/env python3
"""V6.0.2 competition-adaptive draw boundary with hierarchical prior shrinkage.

This challenge keeps the V6.0.1 probability model and global draw boundary intact,
then adapts only the Top-1 draw decision threshold by competition. Competition draw
rates are estimated from pre-holdout seasons and shrunk toward the pooled rate. Two
low-dimensional hyperparameters (shrinkage strength and elasticity) are selected on
the penultimate completed season and evaluated on the last completed development
holdout. No formal/runtime probability, model weight, CURRENT rule, or EV path changes.
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
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
import v6_direct_outcome_draw_boundary_v601 as v601
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
OUT = ROOT / "manifests" / "v6_direct_outcome_domain_draw_prior_v602_status.json"
GAMMA_GRID = (0.25, 0.50, 0.75, 1.00, 1.25)
PRIOR_STRENGTH_GRID = (50.0, 100.0, 200.0, 400.0)
RATIO_MIN = 0.55
RATIO_MAX = 1.05
MIN_VALIDATION_DRAW_PICKS = 100
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 602


def _clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _draw_rate_stats(rows: list[dict[str, Any]]) -> tuple[float, dict[str, dict[str, float]]]:
    total = len(rows)
    total_draws = sum(1 for row in rows if str(row["actual_result"]) == "draw")
    pooled = total_draws / total if total else 0.0
    buckets: dict[str, Counter] = {}
    for row in rows:
        cid = str(row["competition_id"])
        bucket = buckets.setdefault(cid, Counter())
        bucket["count"] += 1
        if str(row["actual_result"]) == "draw":
            bucket["draws"] += 1
    stats = {
        cid: {
            "count": int(bucket["count"]),
            "draws": int(bucket["draws"]),
            "raw_draw_rate": bucket["draws"] / bucket["count"] if bucket["count"] else pooled,
        }
        for cid, bucket in sorted(buckets.items())
    }
    return pooled, stats


def _ratio_map(
    history_rows: list[dict[str, Any]],
    global_ratio: float,
    gamma: float,
    prior_strength: float,
) -> tuple[dict[str, float], dict[str, Any]]:
    pooled, stats = _draw_rate_stats(history_rows)
    if pooled <= 0.0:
        raise PlatformError("pooled draw rate must be positive")
    ratios: dict[str, float] = {}
    audit: dict[str, Any] = {
        "pooled_draw_rate": pooled,
        "global_draw_ratio": global_ratio,
        "gamma": gamma,
        "prior_strength": prior_strength,
        "ratio_min": RATIO_MIN,
        "ratio_max": RATIO_MAX,
        "by_competition": {},
    }
    for cid, item in stats.items():
        count = float(item["count"])
        draws = float(item["draws"])
        shrunk = (draws + prior_strength * pooled) / (count + prior_strength)
        ratio = _clip(global_ratio * (pooled / max(shrunk, base.EPS)) ** gamma, RATIO_MIN, RATIO_MAX)
        ratios[cid] = ratio
        audit["by_competition"][cid] = {
            **item,
            "shrunk_draw_rate": shrunk,
            "draw_ratio": ratio,
            "ratio_delta_vs_global": ratio - global_ratio,
        }
    return ratios, audit


def _metrics(
    rows: list[dict[str, Any]],
    models: dict[str, Any] | None,
    pool_weight: float,
    ratio_map: dict[str, float] | None = None,
    global_ratio: float = 1.0,
) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    actual = Counter()
    agreement_count = agreement_hits = 0
    detail: list[dict[str, Any]] = []
    by_competition: dict[str, Counter] = {}
    for row in rows:
        cid = str(row["competition_id"])
        formal = row["formal"]
        direct = formal if models is None else base._direct_probability(row, models)
        q = formal if models is None else base._log_pool(formal, direct, pool_weight)
        draw_ratio = float(ratio_map.get(cid, global_ratio)) if ratio_map is not None else float(global_ratio)
        pick = v601._pick(q, draw_ratio)
        formal_pick = max(base.CLASSES, key=lambda key: float(formal[key]))
        truth = str(row["actual_result"])
        hit = int(pick == truth)
        ordered = sorted((float(q[key]), key) for key in base.CLASSES)
        confidence = ordered[-1][0] - ordered[-2][0]
        agreement = int(pick == formal_pick)
        count += 1
        hits += hit
        predicted[pick] += 1
        actual[truth] += 1
        agreement_count += agreement
        agreement_hits += agreement * hit
        brier += sum((float(q[key]) - (1.0 if truth == key else 0.0)) ** 2 for key in base.CLASSES)
        truth_vec = {"home": (1.0, 0.0, 0.0), "draw": (0.0, 1.0, 0.0), "away": (0.0, 0.0, 1.0)}[truth]
        c1 = float(q["home"]) - truth_vec[0]
        c2 = float(q["home"]) + float(q["draw"]) - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(base.EPS, float(q[truth])))
        detail.append({
            "competition_id": cid,
            "hit": hit,
            "confidence": confidence,
            "agreement": agreement,
            "pick": pick,
            "truth": truth,
            "draw_ratio": draw_ratio,
        })
        bucket = by_competition.setdefault(cid, Counter())
        bucket["count"] += 1
        bucket["hits"] += hit
        bucket[f"predicted_{pick}"] += 1
        bucket[f"actual_{truth}"] += 1
    return {
        "count": count,
        "hit_count": hits,
        "accuracy": hits / count if count else None,
        "mean_brier": brier / count if count else None,
        "mean_rps": rps / count if count else None,
        "mean_log_loss": logloss / count if count else None,
        "predicted_direction_counts": dict(predicted),
        "actual_direction_counts": dict(actual),
        "draw_prediction_count": int(predicted["draw"]),
        "draw_hit_count": sum(1 for item in detail if item["pick"] == "draw" and item["truth"] == "draw"),
        "agreement_count": agreement_count,
        "agreement_accuracy": agreement_hits / agreement_count if agreement_count else None,
        "detail": detail,
        "by_competition": {
            cid: {
                "count": int(bucket["count"]),
                "hits": int(bucket["hits"]),
                "accuracy": bucket["hits"] / bucket["count"] if bucket["count"] else None,
                "predicted_home": int(bucket["predicted_home"]),
                "predicted_draw": int(bucket["predicted_draw"]),
                "predicted_away": int(bucket["predicted_away"]),
                "actual_draw": int(bucket["actual_draw"]),
            }
            for cid, bucket in sorted(by_competition.items())
        },
    }


def _strip(metrics: dict[str, Any]) -> dict[str, Any]:
    item = dict(metrics)
    item.pop("detail", None)
    return item


def _selective_thresholds(metrics: dict[str, Any]) -> dict[str, float]:
    chosen = [item for item in metrics["detail"] if item["pick"] != "draw" and item["agreement"] == 1]
    chosen.sort(key=lambda item: float(item["confidence"]), reverse=True)
    output: dict[str, float] = {}
    for coverage in (0.20, 0.10, 0.05):
        n = max(1, int(round(len(chosen) * coverage)))
        output[f"top_{int(coverage * 100)}pct"] = float(chosen[n - 1]["confidence"])
    return output


def _selective(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    total = len(metrics["detail"])
    for name, threshold in thresholds.items():
        chosen = [
            item for item in metrics["detail"]
            if item["pick"] != "draw" and item["agreement"] == 1 and float(item["confidence"]) >= threshold
        ]
        output[name] = {
            "threshold": threshold,
            "count": len(chosen),
            "coverage": len(chosen) / total if total else 0.0,
            "accuracy": sum(int(item["hit"]) for item in chosen) / len(chosen) if chosen else None,
            "requires_formal_direct_agreement": True,
            "draws_excluded_from_high_confidence_execution": True,
        }
    return output


def _paired_bootstrap(adaptive: dict[str, Any], global_rule: dict[str, Any]) -> dict[str, Any]:
    a = adaptive["detail"]
    g = global_rule["detail"]
    if len(a) != len(g):
        raise PlatformError("paired bootstrap requires aligned rows")
    diffs = [int(x["hit"]) - int(y["hit"]) for x, y in zip(a, g)]
    n = len(diffs)
    observed = sum(diffs) / n if n else 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        samples.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    lo90 = samples[int(0.05 * (BOOTSTRAP_REPS - 1))]
    hi90 = samples[int(0.95 * (BOOTSTRAP_REPS - 1))]
    lo95 = samples[int(0.025 * (BOOTSTRAP_REPS - 1))]
    hi95 = samples[int(0.975 * (BOOTSTRAP_REPS - 1))]
    discordant_wins = sum(1 for d in diffs if d > 0)
    discordant_losses = sum(1 for d in diffs if d < 0)
    return {
        "repetitions": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
        "observed_accuracy_delta": observed,
        "ci90": [lo90, hi90],
        "ci95": [lo95, hi95],
        "discordant_adaptive_wins": discordant_wins,
        "discordant_global_wins": discordant_losses,
        "ties": n - discordant_wins - discordant_losses,
    }


def _domain_comparison(adaptive: dict[str, Any], global_rule: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    nonworse = 0
    worst_delta = 0.0
    for cid, a in adaptive["by_competition"].items():
        g = global_rule["by_competition"][cid]
        delta = float(a["accuracy"]) - float(g["accuracy"])
        nonworse += int(delta >= -1e-12)
        worst_delta = min(worst_delta, delta)
        rows[cid] = {
            "adaptive_accuracy": a["accuracy"],
            "global_accuracy": g["accuracy"],
            "accuracy_delta": delta,
            "adaptive_draw_picks": a["predicted_draw"],
            "global_draw_picks": g["predicted_draw"],
        }
    return {
        "nonworse_domain_count": nonworse,
        "domain_count": len(rows),
        "worst_domain_accuracy_delta": worst_delta,
        "by_competition": rows,
    }


def main() -> int:
    v601_status = load_json(V601_STATUS)
    if v601_status.get("status") != "PASS":
        raise PlatformError("V6.0.1 status must be PASS")
    selected_v601 = ((v601_status.get("result") or {}).get("selected_candidate") or {})
    if not selected_v601:
        raise PlatformError("V6.0.1 selected candidate missing")
    l2 = float(selected_v601["l2"])
    pool_weight = float(selected_v601["pool_weight"])
    global_ratio = float(selected_v601["draw_ratio"])

    formal_status = load_json(base.FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    rows_by_domain_season: dict[str, dict[str, list[dict[str, Any]]]] = {}
    season_roles: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in domains:
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            seasons = _completed_outer_seasons_last_complete_only(report)
            if len(seasons) < 4:
                raise PlatformError(f"need at least four completed outer seasons for {cid}")
            selected = seasons[-4:]
            rows_by_domain_season[cid] = base._build_domain_rows(cid, selected)
            season_roles[cid] = {
                "fit_seasons": selected[:2],
                "selection_validation_season": selected[2],
                "development_holdout_season": selected[3],
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        payload = {
            "schema_version": "V6.0.2-domain-draw-prior-r1",
            "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "status": "FAIL_DATA_BUILD",
            "failures": failures,
            "governance": {"formal_weight_change": False, "runtime_probability_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    fit_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    holdout_rows: list[dict[str, Any]] = []
    for cid in domains:
        seasons = sorted(rows_by_domain_season[cid], key=_season_key)
        for season in seasons[:2]:
            fit_rows.extend(rows_by_domain_season[cid][season])
        validation_rows.extend(rows_by_domain_season[cid][seasons[2]])
        holdout_rows.extend(rows_by_domain_season[cid][seasons[3]])

    fit_models = base._fit_models(fit_rows, l2)
    baseline_validation = _metrics(validation_rows, None, 0.0, global_ratio=1.0)
    global_validation = _metrics(validation_rows, fit_models, pool_weight, global_ratio=global_ratio)

    candidates: list[dict[str, Any]] = []
    for gamma in GAMMA_GRID:
        for prior_strength in PRIOR_STRENGTH_GRID:
            ratios, ratio_audit = _ratio_map(fit_rows, global_ratio, gamma, prior_strength)
            metrics = _metrics(validation_rows, fit_models, pool_weight, ratio_map=ratios, global_ratio=global_ratio)
            candidates.append({
                "gamma": gamma,
                "prior_strength": prior_strength,
                "ratio_audit": ratio_audit,
                "validation": _strip(metrics),
                "draw_count_gate": int(metrics["draw_prediction_count"]) >= MIN_VALIDATION_DRAW_PICKS,
                "global_validation_nonworse": float(metrics["accuracy"]) >= float(global_validation["accuracy"]) - 1e-12,
            })

    eligible = [item for item in candidates if item["draw_count_gate"] and item["global_validation_nonworse"]]
    eligible.sort(key=lambda item: (
        -float(item["validation"]["accuracy"]),
        abs(float(item["gamma"])),
        -float(item["prior_strength"]),
    ))

    if not eligible:
        result = {
            "status": "NO_VALIDATION_SAFE_DOMAIN_ADAPTIVE_CANDIDATE",
            "selected_candidate": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": ["no domain-adaptive candidate matched global validation accuracy with at least 100 draw picks"],
        }
    else:
        selected = eligible[0]
        refit_models = base._fit_models(fit_rows + validation_rows, l2)
        adaptive_ratios, adaptive_ratio_audit = _ratio_map(
            fit_rows + validation_rows,
            global_ratio,
            float(selected["gamma"]),
            float(selected["prior_strength"]),
        )
        validation_ratios, validation_ratio_audit = _ratio_map(
            fit_rows,
            global_ratio,
            float(selected["gamma"]),
            float(selected["prior_strength"]),
        )
        adaptive_validation = _metrics(
            validation_rows, fit_models, pool_weight, ratio_map=validation_ratios, global_ratio=global_ratio
        )
        formal_holdout = _metrics(holdout_rows, None, 0.0, global_ratio=1.0)
        global_holdout = _metrics(holdout_rows, refit_models, pool_weight, global_ratio=global_ratio)
        adaptive_holdout = _metrics(
            holdout_rows, refit_models, pool_weight, ratio_map=adaptive_ratios, global_ratio=global_ratio
        )

        adaptive_thresholds = _selective_thresholds(adaptive_validation)
        global_thresholds = _selective_thresholds(global_validation)
        adaptive_selective = _selective(adaptive_holdout, adaptive_thresholds)
        global_selective = _selective(global_holdout, global_thresholds)
        paired = _paired_bootstrap(adaptive_holdout, global_holdout)
        domain_comparison = _domain_comparison(adaptive_holdout, global_holdout)

        adaptive_vs_global_pp = 100.0 * (
            float(adaptive_holdout["accuracy"]) - float(global_holdout["accuracy"])
        )
        adaptive_vs_formal_pp = 100.0 * (
            float(adaptive_holdout["accuracy"]) - float(formal_holdout["accuracy"])
        )
        fail_reasons: list[str] = []
        if adaptive_vs_global_pp < 0.25:
            fail_reasons.append("development holdout gain versus V6.0.1 global boundary below 0.25 percentage point")
        if adaptive_vs_formal_pp < 1.50:
            fail_reasons.append("development holdout gain versus formal baseline below 1.50 percentage points")
        if int(adaptive_holdout["draw_prediction_count"]) < 100:
            fail_reasons.append("development holdout draw prediction count below 100")
        if int(domain_comparison["nonworse_domain_count"]) < 9:
            fail_reasons.append("fewer than 9 of 17 domains were nonworse versus the V6.0.1 global boundary")
        if float(paired["ci90"][0]) < -0.001:
            fail_reasons.append("paired bootstrap 90% lower bound below -0.10 percentage point")
        adaptive_top10 = adaptive_selective["top_10pct"]["accuracy"]
        global_top10 = global_selective["top_10pct"]["accuracy"]
        if adaptive_top10 is None or global_top10 is None or float(adaptive_top10) < float(global_top10) - 1e-12:
            fail_reasons.append("top-10% selective holdout accuracy worsened versus V6.0.1 global boundary")

        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {
                "l2": l2,
                "pool_weight": pool_weight,
                "global_draw_ratio": global_ratio,
                "gamma": selected["gamma"],
                "prior_strength": selected["prior_strength"],
                "selection_validation": selected["validation"],
                "validation_ratio_audit": validation_ratio_audit,
            },
            "refit_ratio_audit": adaptive_ratio_audit,
            "formal_holdout": _strip(formal_holdout),
            "global_v601_holdout": _strip(global_holdout),
            "adaptive_holdout": _strip(adaptive_holdout),
            "adaptive_selective_holdout": adaptive_selective,
            "global_v601_selective_holdout": global_selective,
            "paired_bootstrap": paired,
            "domain_comparison": domain_comparison,
            "adaptive_vs_global_accuracy_gain_pp": adaptive_vs_global_pp,
            "adaptive_vs_formal_accuracy_gain_pp": adaptive_vs_formal_pp,
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V6.0.2-domain-draw-prior-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "probability_model": "V6.0.1 direct-outcome probability model and global boundary",
            "decision_extension": "competition draw-rate prior with pooled hierarchical shrinkage",
            "gamma_grid": list(GAMMA_GRID),
            "prior_strength_grid": list(PRIOR_STRENGTH_GRID),
            "ratio_bounds": [RATIO_MIN, RATIO_MAX],
            "selection_data": "penultimate completed season only",
            "holdout_note": "development holdout, not pristine final test",
            "historical_market_odds_used": False,
            "xg_used": False,
            "lineups_used": False,
            "manual_draw_probability": False,
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
        "v601_anchor": {
            "l2": l2,
            "pool_weight": pool_weight,
            "global_draw_ratio": global_ratio,
        },
        "baseline_validation": _strip(baseline_validation),
        "global_v601_validation": _strip(global_validation),
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "candidates": candidates,
        "result": result,
        "governance": {
            "research_challenge_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "promotion_requires_new_pristine_forward_test": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
