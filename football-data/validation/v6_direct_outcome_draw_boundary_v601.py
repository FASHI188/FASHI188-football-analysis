#!/usr/bin/env python3
"""V6.0.1 validation-selected draw/decisive decision boundary.

Builds the same leakage-safe rows and direct-outcome experts as V6.0.0, but decouples
probability estimation from the Top-1 decision rule. A draw is selected when its
probability reaches a validation-selected fraction of the stronger decisive side.
The threshold is selected only on the penultimate completed season, with a minimum
validation draw-count gate, then evaluated on the last completed development holdout.
"""
from __future__ import annotations

import json
import math
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
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
DRAW_RATIO_GRID = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00)
MIN_VALIDATION_DRAW_PICKS = 100


def _pick(q: dict[str, float], draw_ratio: float) -> str:
    strongest_side = "home" if float(q["home"]) >= float(q["away"]) else "away"
    if float(q["draw"]) >= float(draw_ratio) * float(q[strongest_side]):
        return "draw"
    return strongest_side


def _metrics(
    rows: list[dict[str, Any]],
    models: dict[str, Any] | None,
    pool_weight: float = 0.0,
    draw_ratio: float = 1.0,
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
        direct = formal if models is None else base._direct_probability(row, models)
        q = formal if models is None else base._log_pool(formal, direct, pool_weight)
        pick = _pick(q, draw_ratio)
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
        c1 = q["home"] - truth_vec[0]
        c2 = q["home"] + q["draw"] - truth_vec[0] - truth_vec[1]
        rps += (c1 * c1 + c2 * c2) / 2.0
        logloss -= math.log(max(base.EPS, q[truth]))
        detail.append({"hit": hit, "confidence": confidence, "agreement": agreement, "pick": pick})
        bucket = by_competition.setdefault(row["competition_id"], Counter())
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
        "draw_hit_count": sum(1 for item, row in zip(detail, rows) if item["pick"] == "draw" and row["actual_result"] == "draw"),
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


def _thresholds(validation_metrics: dict[str, Any]) -> dict[str, float]:
    non_draw = [item for item in validation_metrics["detail"] if item["pick"] != "draw" and item["agreement"] == 1]
    non_draw.sort(key=lambda item: float(item["confidence"]), reverse=True)
    thresholds: dict[str, float] = {}
    for coverage in (0.20, 0.10, 0.05):
        n = max(1, int(round(len(non_draw) * coverage)))
        thresholds[f"top_{int(coverage * 100)}pct"] = float(non_draw[n - 1]["confidence"])
    return thresholds


def _selective(metrics: dict[str, Any], thresholds: dict[str, float]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for name, threshold in thresholds.items():
        chosen = [
            item for item in metrics["detail"]
            if item["pick"] != "draw" and item["agreement"] == 1 and float(item["confidence"]) >= threshold
        ]
        output[name] = {
            "threshold": threshold,
            "count": len(chosen),
            "coverage": len(chosen) / len(metrics["detail"]) if metrics["detail"] else 0.0,
            "accuracy": sum(int(item["hit"]) for item in chosen) / len(chosen) if chosen else None,
            "requires_formal_direct_agreement": True,
            "draws_excluded_from_high_confidence_execution": True,
        }
    return output


def main() -> int:
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
            "schema_version": "V6.0.1-direct-outcome-draw-boundary-r1",
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

    baseline_validation = _metrics(validation_rows, None)
    baseline_holdout = _metrics(holdout_rows, None)
    candidates: list[dict[str, Any]] = []
    fit_cache: dict[float, dict[str, Any]] = {}
    for l2 in base.L2_GRID:
        try:
            models = base._fit_models(fit_rows, l2)
            fit_cache[l2] = models
            for pool_weight in base.POOL_GRID:
                for draw_ratio in DRAW_RATIO_GRID:
                    metrics = _metrics(validation_rows, models, pool_weight, draw_ratio)
                    proper_nonworse = (
                        float(metrics["mean_log_loss"]) <= float(baseline_validation["mean_log_loss"]) + 1e-12
                        and float(metrics["mean_brier"]) <= float(baseline_validation["mean_brier"]) + 1e-12
                        and float(metrics["mean_rps"]) <= float(baseline_validation["mean_rps"]) + 1e-12
                    )
                    candidates.append({
                        "l2": l2,
                        "pool_weight": pool_weight,
                        "draw_ratio": draw_ratio,
                        "validation": _strip(metrics),
                        "proper_scores_nonworse": proper_nonworse,
                        "draw_count_gate": int(metrics["draw_prediction_count"]) >= MIN_VALIDATION_DRAW_PICKS,
                    })
        except Exception as exc:
            candidates.append({
                "l2": l2,
                "status": "FAILED",
                "error": f"{type(exc).__name__}: {exc}",
                "proper_scores_nonworse": False,
                "draw_count_gate": False,
            })

    eligible = [
        item for item in candidates
        if item.get("proper_scores_nonworse") and item.get("draw_count_gate") and item.get("validation")
    ]
    eligible.sort(key=lambda item: (
        -float(item["validation"]["accuracy"]),
        float(item["validation"]["mean_log_loss"]),
        -int(item["validation"]["draw_prediction_count"]),
        float(item["l2"]),
        float(item["pool_weight"]),
        -float(item["draw_ratio"]),
    ))

    if not eligible:
        result = {
            "status": "NO_DRAW_CAPABLE_PROPER_SCORE_SAFE_CANDIDATE",
            "selected_candidate": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": ["no validation candidate preserved proper scores and predicted at least 100 draws"],
        }
    else:
        selected = eligible[0]
        refit_models = base._fit_models(fit_rows + validation_rows, float(selected["l2"]))
        validation_selected = _metrics(
            validation_rows,
            fit_cache[float(selected["l2"])],
            float(selected["pool_weight"]),
            float(selected["draw_ratio"]),
        )
        thresholds = _thresholds(validation_selected)
        holdout = _metrics(
            holdout_rows,
            refit_models,
            float(selected["pool_weight"]),
            float(selected["draw_ratio"]),
        )
        accuracy_gain_pp = 100.0 * (float(holdout["accuracy"]) - float(baseline_holdout["accuracy"]))
        fail_reasons: list[str] = []
        if accuracy_gain_pp < 1.0:
            fail_reasons.append("development holdout accuracy gain below 1 percentage point")
        if float(holdout["mean_log_loss"]) >= float(baseline_holdout["mean_log_loss"]):
            fail_reasons.append("development holdout log loss did not improve")
        if float(holdout["mean_brier"]) >= float(baseline_holdout["mean_brier"]):
            fail_reasons.append("development holdout Brier score did not improve")
        if float(holdout["mean_rps"]) >= float(baseline_holdout["mean_rps"]):
            fail_reasons.append("development holdout RPS did not improve")
        if int(holdout["draw_prediction_count"]) < 100:
            fail_reasons.append("development holdout draw prediction count below 100")
        result = {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "selected_candidate": {
                "l2": selected["l2"],
                "pool_weight": selected["pool_weight"],
                "draw_ratio": selected["draw_ratio"],
                "selection_validation": selected["validation"],
            },
            "refit_audit": refit_models,
            "holdout": _strip(holdout),
            "selective_holdout": _selective(holdout, thresholds),
            "accuracy_gain_pp": accuracy_gain_pp,
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
        }

    payload = {
        "schema_version": "V6.0.1-direct-outcome-draw-boundary-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "probability_model": "V6.0.0 two-expert direct outcome model",
            "decision_rule": "validation-selected draw probability ratio to strongest decisive side",
            "draw_ratio_grid": list(DRAW_RATIO_GRID),
            "minimum_validation_draw_picks": MIN_VALIDATION_DRAW_PICKS,
            "manual_draw_probability": False,
            "target_holdout_used_for_selection": False,
            "holdout_note": "development holdout, not pristine final test",
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
        "baseline": {
            "selection_validation": _strip(baseline_validation),
            "development_holdout": _strip(baseline_holdout),
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "candidate_summary": candidates,
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
    print(json.dumps({
        "status": payload["status"],
        "baseline_accuracy": baseline_holdout["accuracy"],
        "candidate_accuracy": ((result.get("holdout") or {}).get("accuracy")),
        "accuracy_gain_pp": result.get("accuracy_gain_pp"),
        "draw_predictions": ((result.get("holdout") or {}).get("draw_prediction_count")),
        "draw_hits": ((result.get("holdout") or {}).get("draw_hit_count")),
        "challenge_gate_passed": result.get("challenge_gate_passed"),
        "fail_reasons": result.get("challenge_gate_fail_reasons"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
