#!/usr/bin/env python3
"""V6.0.3 low-dimensional nonlinear interaction challenge.

The V6.0.1 direct-outcome model is linear in several useful signals. This challenge
adds only pre-match interaction/nonlinear terms designed to represent close, low-total
matches and formal-probability uncertainty. Feature families, L2 and pool weight are
selected on the penultimate completed season; the V6.0.1 draw boundary remains fixed.
The last completed season remains a viewed development holdout, not a pristine test.
No CURRENT, formal weight, runtime probability, market or EV path is changed.
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
OUT = ROOT / "manifests" / "v6_direct_outcome_interactions_v603_status.json"
FEATURE_FAMILIES = ("interaction_core", "nonlinear_core", "hybrid")
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 603
MIN_VALIDATION_DRAW_PICKS = 100


def _entropy(probabilities: dict[str, float]) -> float:
    return -sum(float(p) * math.log(max(base.EPS, float(p))) for p in probabilities.values())


def _enriched_draw_x(row: dict[str, Any], family: str) -> list[float]:
    x = [float(v) for v in row["draw_x"]]
    formal = {key: float(row["formal"][key]) for key in base.CLASSES}
    logit_draw = x[1]
    side_gap = x[2]
    abs_elo = x[3]
    draw_mean_centered = x[6]
    league_draw_centered = x[7]
    total_vs_league = x[8]
    total_centered = x[9]
    strongest_side = max(formal["home"], formal["away"])
    log_draw_ratio = math.log(max(base.EPS, formal["draw"])) - math.log(max(base.EPS, strongest_side))
    formal_margin = formal["draw"] - strongest_side
    entropy_centered = _entropy(formal) - math.log(3.0)

    interaction_core = [
        side_gap * total_centered,
        draw_mean_centered * total_centered,
        league_draw_centered * total_centered,
        logit_draw * side_gap,
        logit_draw * total_centered,
    ]
    nonlinear_core = [
        side_gap * side_gap,
        total_centered * total_centered,
        total_vs_league * total_vs_league,
        abs_elo * abs_elo,
        log_draw_ratio,
        formal_margin,
        entropy_centered,
    ]
    if family == "interaction_core":
        return x + interaction_core
    if family == "nonlinear_core":
        return x + nonlinear_core
    if family == "hybrid":
        return x + interaction_core + nonlinear_core
    raise PlatformError(f"unknown feature family: {family}")


def _attach_family(rows: list[dict[str, Any]], family: str) -> None:
    for row in rows:
        row["draw_x_v603"] = _enriched_draw_x(row, family)


def _fit_models(rows: list[dict[str, Any]], l2: float) -> dict[str, Any]:
    draw_model = base._fit_binary(rows, "draw_x_v603", "draw_y", l2)
    decisive = [row for row in rows if row["is_decisive"]]
    side_model = base._fit_binary(decisive, "side_x", "side_y", l2)
    return {"draw_model": draw_model, "side_model": side_model, "l2": l2}


def _direct_probability(row: dict[str, Any], models: dict[str, Any]) -> dict[str, float]:
    p_draw = base._clip(base._predict_binary(models["draw_model"], row["draw_x_v603"]), 1e-6, 1.0 - 1e-6)
    p_home_decisive = base._clip(base._predict_binary(models["side_model"], row["side_x"]), 1e-6, 1.0 - 1e-6)
    remaining = 1.0 - p_draw
    return {
        "home": remaining * p_home_decisive,
        "draw": p_draw,
        "away": remaining * (1.0 - p_home_decisive),
    }


def _metrics(
    rows: list[dict[str, Any]],
    models: dict[str, Any] | None,
    pool_weight: float,
    draw_ratio: float,
    use_enriched: bool,
) -> dict[str, Any]:
    count = hits = 0
    brier = rps = logloss = 0.0
    predicted = Counter()
    actual = Counter()
    agreement_count = agreement_hits = 0
    detail: list[dict[str, Any]] = []
    by_competition: dict[str, Counter] = {}
    for row in rows:
        formal = row["formal"]
        if models is None:
            direct = formal
        elif use_enriched:
            direct = _direct_probability(row, models)
        else:
            direct = base._direct_probability(row, models)
        q = formal if models is None else base._log_pool(formal, direct, pool_weight)
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
        cid = str(row["competition_id"])
        detail.append({
            "competition_id": cid,
            "hit": hit,
            "confidence": confidence,
            "agreement": agreement,
            "pick": pick,
            "truth": truth,
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


def _thresholds(metrics: dict[str, Any]) -> dict[str, float]:
    items = [item for item in metrics["detail"] if item["pick"] != "draw" and item["agreement"] == 1]
    items.sort(key=lambda item: float(item["confidence"]), reverse=True)
    output: dict[str, float] = {}
    for coverage in (0.20, 0.10, 0.05):
        n = max(1, int(round(len(items) * coverage)))
        output[f"top_{int(coverage * 100)}pct"] = float(items[n - 1]["confidence"])
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


def _paired_bootstrap(candidate: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    c = candidate["detail"]
    a = anchor["detail"]
    if len(c) != len(a):
        raise PlatformError("paired rows are not aligned")
    diffs = [int(x["hit"]) - int(y["hit"]) for x, y in zip(c, a)]
    n = len(diffs)
    observed = sum(diffs) / n if n else 0.0
    rng = random.Random(BOOTSTRAP_SEED)
    samples = []
    for _ in range(BOOTSTRAP_REPS):
        samples.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    samples.sort()
    return {
        "repetitions": BOOTSTRAP_REPS,
        "seed": BOOTSTRAP_SEED,
        "observed_accuracy_delta": observed,
        "ci90": [samples[int(0.05 * (BOOTSTRAP_REPS - 1))], samples[int(0.95 * (BOOTSTRAP_REPS - 1))]],
        "ci95": [samples[int(0.025 * (BOOTSTRAP_REPS - 1))], samples[int(0.975 * (BOOTSTRAP_REPS - 1))]],
        "candidate_only_hits": sum(1 for d in diffs if d > 0),
        "anchor_only_hits": sum(1 for d in diffs if d < 0),
        "ties": sum(1 for d in diffs if d == 0),
    }


def _domain_comparison(candidate: dict[str, Any], anchor: dict[str, Any]) -> dict[str, Any]:
    rows = {}
    nonworse = 0
    worst = 0.0
    for cid, c in candidate["by_competition"].items():
        a = anchor["by_competition"][cid]
        delta = float(c["accuracy"]) - float(a["accuracy"])
        nonworse += int(delta >= -1e-12)
        worst = min(worst, delta)
        rows[cid] = {
            "candidate_accuracy": c["accuracy"],
            "v601_accuracy": a["accuracy"],
            "accuracy_delta": delta,
            "candidate_draw_picks": c["predicted_draw"],
            "v601_draw_picks": a["predicted_draw"],
        }
    return {
        "nonworse_domain_count": nonworse,
        "domain_count": len(rows),
        "worst_domain_accuracy_delta": worst,
        "by_competition": rows,
    }


def main() -> int:
    status601 = load_json(V601_STATUS)
    selected601 = ((status601.get("result") or {}).get("selected_candidate") or {})
    if status601.get("status") != "PASS" or not selected601:
        raise PlatformError("V6.0.1 PASS receipt and selected candidate are required")
    anchor_l2 = float(selected601["l2"])
    anchor_pool = float(selected601["pool_weight"])
    draw_ratio = float(selected601["draw_ratio"])

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
            "schema_version": "V6.0.3-interactions-r1",
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

    anchor_fit_models = base._fit_models(fit_rows, anchor_l2)
    anchor_validation = _metrics(
        validation_rows, anchor_fit_models, anchor_pool, draw_ratio, use_enriched=False
    )

    candidates: list[dict[str, Any]] = []
    model_cache: dict[tuple[str, float], dict[str, Any]] = {}
    all_rows = fit_rows + validation_rows + holdout_rows
    for family in FEATURE_FAMILIES:
        _attach_family(all_rows, family)
        for l2 in base.L2_GRID:
            try:
                models = _fit_models(fit_rows, l2)
                model_cache[(family, l2)] = models
                for pool_weight in base.POOL_GRID:
                    metrics = _metrics(
                        validation_rows, models, pool_weight, draw_ratio, use_enriched=True
                    )
                    proper_nonworse = (
                        float(metrics["mean_log_loss"]) <= float(anchor_validation["mean_log_loss"]) + 1e-12
                        and float(metrics["mean_brier"]) <= float(anchor_validation["mean_brier"]) + 1e-12
                        and float(metrics["mean_rps"]) <= float(anchor_validation["mean_rps"]) + 1e-12
                    )
                    candidates.append({
                        "feature_family": family,
                        "l2": l2,
                        "pool_weight": pool_weight,
                        "validation": _strip(metrics),
                        "proper_scores_nonworse": proper_nonworse,
                        "draw_count_gate": int(metrics["draw_prediction_count"]) >= MIN_VALIDATION_DRAW_PICKS,
                        "anchor_accuracy_nonworse": float(metrics["accuracy"]) >= float(anchor_validation["accuracy"]) - 1e-12,
                    })
            except Exception as exc:
                candidates.append({
                    "feature_family": family,
                    "l2": l2,
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                    "proper_scores_nonworse": False,
                    "draw_count_gate": False,
                    "anchor_accuracy_nonworse": False,
                })

    eligible = [
        item for item in candidates
        if item.get("proper_scores_nonworse")
        and item.get("draw_count_gate")
        and item.get("anchor_accuracy_nonworse")
        and item.get("validation")
    ]
    eligible.sort(key=lambda item: (
        -float(item["validation"]["accuracy"]),
        float(item["validation"]["mean_log_loss"]),
        FEATURE_FAMILIES.index(str(item["feature_family"])),
        float(item["l2"]),
        float(item["pool_weight"]),
    ))

    if not eligible:
        result = {
            "status": "NO_VALIDATION_SAFE_INTERACTION_CANDIDATE",
            "selected_candidate": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": [
                "no enriched feature candidate matched V6.0.1 validation accuracy, proper scores and draw-count gate"
            ],
        }
    else:
        selected = eligible[0]
        family = str(selected["feature_family"])
        _attach_family(all_rows, family)
        refit_models = _fit_models(fit_rows + validation_rows, float(selected["l2"]))
        anchor_refit_models = base._fit_models(fit_rows + validation_rows, anchor_l2)
        selected_validation = _metrics(
            validation_rows,
            model_cache[(family, float(selected["l2"]))],
            float(selected["pool_weight"]),
            draw_ratio,
            use_enriched=True,
        )
        candidate_holdout = _metrics(
            holdout_rows,
            refit_models,
            float(selected["pool_weight"]),
            draw_ratio,
            use_enriched=True,
        )
        anchor_holdout = _metrics(
            holdout_rows,
            anchor_refit_models,
            anchor_pool,
            draw_ratio,
            use_enriched=False,
        )
        formal_holdout = _metrics(holdout_rows, None, 0.0, 1.0, use_enriched=False)
        candidate_thresholds = _thresholds(selected_validation)
        anchor_thresholds = _thresholds(anchor_validation)
        candidate_selective = _selective(candidate_holdout, candidate_thresholds)
        anchor_selective = _selective(anchor_holdout, anchor_thresholds)
        paired = _paired_bootstrap(candidate_holdout, anchor_holdout)
        domains_result = _domain_comparison(candidate_holdout, anchor_holdout)

        vs_anchor_pp = 100.0 * (
            float(candidate_holdout["accuracy"]) - float(anchor_holdout["accuracy"])
        )
        vs_formal_pp = 100.0 * (
            float(candidate_holdout["accuracy"]) - float(formal_holdout["accuracy"])
        )
        fail_reasons: list[str] = []
        if vs_anchor_pp < 0.25:
            fail_reasons.append("development holdout gain versus V6.0.1 below 0.25 percentage point")
        if vs_formal_pp < 1.50:
            fail_reasons.append("development holdout gain versus formal baseline below 1.50 percentage points")
        if float(candidate_holdout["mean_log_loss"]) > float(anchor_holdout["mean_log_loss"]) + 1e-12:
            fail_reasons.append("development holdout log loss worsened versus V6.0.1")
        if float(candidate_holdout["mean_brier"]) > float(anchor_holdout["mean_brier"]) + 1e-12:
            fail_reasons.append("development holdout Brier worsened versus V6.0.1")
        if float(candidate_holdout["mean_rps"]) > float(anchor_holdout["mean_rps"]) + 1e-12:
            fail_reasons.append("development holdout RPS worsened versus V6.0.1")
        if int(candidate_holdout["draw_prediction_count"]) < 100:
            fail_reasons.append("development holdout draw prediction count below 100")
        if int(domains_result["nonworse_domain_count"]) < 9:
            fail_reasons.append("fewer than 9 of 17 domains were nonworse versus V6.0.1")
        if float(paired["ci90"][0]) < -0.001:
            fail_reasons.append("paired bootstrap 90% lower bound below -0.10 percentage point")
        c_top10 = candidate_selective["top_10pct"]["accuracy"]
        a_top10 = anchor_selective["top_10pct"]["accuracy"]
        if c_top10 is None or a_top10 is None or float(c_top10) < float(a_top10) - 1e-12:
            fail_reasons.append("top-10% selective accuracy worsened versus V6.0.1")

        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {
                "feature_family": family,
                "l2": selected["l2"],
                "pool_weight": selected["pool_weight"],
                "draw_ratio": draw_ratio,
                "selection_validation": selected["validation"],
            },
            "refit_audit": refit_models,
            "formal_holdout": _strip(formal_holdout),
            "v601_holdout": _strip(anchor_holdout),
            "candidate_holdout": _strip(candidate_holdout),
            "candidate_selective_holdout": candidate_selective,
            "v601_selective_holdout": anchor_selective,
            "paired_bootstrap": paired,
            "domain_comparison": domains_result,
            "candidate_vs_v601_accuracy_gain_pp": vs_anchor_pp,
            "candidate_vs_formal_accuracy_gain_pp": vs_formal_pp,
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V6.0.3-interactions-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "anchor": "V6.0.1 direct-outcome model and fixed draw boundary",
            "feature_families": list(FEATURE_FAMILIES),
            "feature_policy": "low-dimensional deterministic interactions from pre-match formal and dynamic signals",
            "selection_data": "penultimate completed season only",
            "holdout_note": "development holdout, not pristine final test",
            "historical_market_odds_used": False,
            "xg_used": False,
            "lineups_used": False,
            "manual_probability_adjustment": False,
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
        "v601_anchor": {
            "l2": anchor_l2,
            "pool_weight": anchor_pool,
            "draw_ratio": draw_ratio,
            "validation": _strip(anchor_validation),
        },
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
