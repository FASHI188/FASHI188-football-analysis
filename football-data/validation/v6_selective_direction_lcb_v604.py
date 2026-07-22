#!/usr/bin/env python3
"""V6.0.4 direction-balanced selective execution challenge.

Uses the frozen V6.0.1 probability model, pool weight and draw boundary. No model
parameter is changed. Validation-season confidence thresholds are learned separately
for home and away picks at fixed 20%, 10% and 5% within-direction coverage. Holdout
performance is compared with the original pooled V6.0.1 thresholds. Wilson lower
bounds and paired bootstrap are audit outputs, not tuning inputs.
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
OUT = ROOT / "manifests" / "v6_selective_direction_lcb_v604_status.json"
COVERAGES = (0.20, 0.10, 0.05)
BOOTSTRAP_REPS = 3000
BOOTSTRAP_SEED = 604
Z90 = 1.6448536269514722


def _wilson_lower(hits: int, count: int, z: float = Z90) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z2 = z * z
    denom = 1.0 + z2 / count
    center = p + z2 / (2.0 * count)
    spread = z * math.sqrt((p * (1.0 - p) + z2 / (4.0 * count)) / count)
    return (center - spread) / denom


def _details(
    rows: list[dict[str, Any]],
    models: dict[str, Any],
    pool_weight: float,
    draw_ratio: float,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in rows:
        formal = row["formal"]
        direct = base._direct_probability(row, models)
        q = base._log_pool(formal, direct, pool_weight)
        pick = v601._pick(q, draw_ratio)
        formal_pick = max(base.CLASSES, key=lambda key: float(formal[key]))
        truth = str(row["actual_result"])
        ordered = sorted((float(q[key]), key) for key in base.CLASSES)
        confidence = ordered[-1][0] - ordered[-2][0]
        output.append({
            "competition_id": str(row["competition_id"]),
            "pick": pick,
            "truth": truth,
            "hit": int(pick == truth),
            "agreement": int(pick == formal_pick),
            "confidence": confidence,
            "home_probability": float(q["home"]),
            "draw_probability": float(q["draw"]),
            "away_probability": float(q["away"]),
        })
    return output


def _threshold(values: list[float], coverage: float) -> float:
    if not values:
        return float("inf")
    ordered = sorted(values, reverse=True)
    n = max(1, int(round(len(ordered) * coverage)))
    return float(ordered[n - 1])


def _validation_thresholds(details: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [item for item in details if item["pick"] != "draw" and item["agreement"] == 1]
    output: dict[str, Any] = {}
    for coverage in COVERAGES:
        name = f"top_{int(coverage * 100)}pct"
        pooled = _threshold([float(item["confidence"]) for item in eligible], coverage)
        by_direction = {
            direction: _threshold(
                [float(item["confidence"]) for item in eligible if item["pick"] == direction],
                coverage,
            )
            for direction in ("home", "away")
        }
        output[name] = {
            "coverage_target": coverage,
            "pooled_threshold": pooled,
            "direction_thresholds": by_direction,
        }
    return output


def _select(
    details: list[dict[str, Any]],
    thresholds: dict[str, Any],
    mode: str,
) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for item in details:
        if item["pick"] == "draw" or item["agreement"] != 1:
            continue
        if mode == "pooled":
            threshold = float(thresholds["pooled_threshold"])
        elif mode == "direction_balanced":
            threshold = float(thresholds["direction_thresholds"][item["pick"]])
        else:
            raise PlatformError(f"unknown selection mode: {mode}")
        if float(item["confidence"]) >= threshold:
            chosen.append(item)
    return chosen


def _summary(chosen: list[dict[str, Any]], total_rows: int) -> dict[str, Any]:
    hits = sum(int(item["hit"]) for item in chosen)
    by_direction: dict[str, Any] = {}
    for direction in ("home", "away"):
        rows = [item for item in chosen if item["pick"] == direction]
        direction_hits = sum(int(item["hit"]) for item in rows)
        by_direction[direction] = {
            "count": len(rows),
            "hits": direction_hits,
            "accuracy": direction_hits / len(rows) if rows else None,
            "wilson90_lower": _wilson_lower(direction_hits, len(rows)),
        }
    by_competition: dict[str, Counter] = {}
    for item in chosen:
        bucket = by_competition.setdefault(str(item["competition_id"]), Counter())
        bucket["count"] += 1
        bucket["hits"] += int(item["hit"])
    return {
        "count": len(chosen),
        "coverage": len(chosen) / total_rows if total_rows else 0.0,
        "hits": hits,
        "accuracy": hits / len(chosen) if chosen else None,
        "wilson90_lower": _wilson_lower(hits, len(chosen)),
        "by_direction": by_direction,
        "by_competition": {
            cid: {
                "count": int(bucket["count"]),
                "hits": int(bucket["hits"]),
                "accuracy": bucket["hits"] / bucket["count"] if bucket["count"] else None,
            }
            for cid, bucket in sorted(by_competition.items())
        },
    }


def _bootstrap_difference(
    details: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    n = len(details)
    if n == 0:
        raise PlatformError("no holdout rows for bootstrap")
    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        resampled = [details[rng.randrange(n)] for _ in range(n)]
        balanced = _select(resampled, thresholds, "direction_balanced")
        pooled = _select(resampled, thresholds, "pooled")
        if not balanced or not pooled:
            continue
        b_acc = sum(int(item["hit"]) for item in balanced) / len(balanced)
        p_acc = sum(int(item["hit"]) for item in pooled) / len(pooled)
        samples.append(b_acc - p_acc)
    if not samples:
        raise PlatformError("bootstrap produced no valid samples")
    samples.sort()
    m = len(samples)
    return {
        "repetitions_requested": BOOTSTRAP_REPS,
        "repetitions_valid": m,
        "seed": BOOTSTRAP_SEED,
        "ci90": [samples[int(0.05 * (m - 1))], samples[int(0.95 * (m - 1))]],
        "ci95": [samples[int(0.025 * (m - 1))], samples[int(0.975 * (m - 1))]],
        "probability_balanced_better": sum(1 for value in samples if value > 0.0) / m,
    }


def _direction_validation_audit(
    details: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for tier_name, tier in thresholds.items():
        output[tier_name] = {
            "pooled": _summary(_select(details, tier, "pooled"), len(details)),
            "direction_balanced": _summary(_select(details, tier, "direction_balanced"), len(details)),
        }
    return output


def main() -> int:
    receipt = load_json(V601_STATUS)
    selected = ((receipt.get("result") or {}).get("selected_candidate") or {})
    if receipt.get("status") != "PASS" or not selected:
        raise PlatformError("V6.0.1 PASS receipt and selected candidate are required")
    l2 = float(selected["l2"])
    pool_weight = float(selected["pool_weight"])
    draw_ratio = float(selected["draw_ratio"])

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
            chosen = seasons[-4:]
            rows_by_domain_season[cid] = base._build_domain_rows(cid, chosen)
            season_roles[cid] = {
                "fit_seasons": chosen[:2],
                "selection_validation_season": chosen[2],
                "development_holdout_season": chosen[3],
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        payload = {
            "schema_version": "V6.0.4-selective-direction-lcb-r1",
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
    validation_details = _details(validation_rows, fit_models, pool_weight, draw_ratio)
    thresholds = _validation_thresholds(validation_details)
    validation_audit = _direction_validation_audit(validation_details, thresholds)

    refit_models = base._fit_models(fit_rows + validation_rows, l2)
    holdout_details = _details(holdout_rows, refit_models, pool_weight, draw_ratio)
    holdout_audit: dict[str, Any] = {}
    nonworse_tiers = 0
    fail_reasons: list[str] = []
    for tier_name, tier in thresholds.items():
        pooled_rows = _select(holdout_details, tier, "pooled")
        balanced_rows = _select(holdout_details, tier, "direction_balanced")
        pooled_summary = _summary(pooled_rows, len(holdout_details))
        balanced_summary = _summary(balanced_rows, len(holdout_details))
        accuracy_delta = (
            float(balanced_summary["accuracy"]) - float(pooled_summary["accuracy"])
            if balanced_summary["accuracy"] is not None and pooled_summary["accuracy"] is not None
            else None
        )
        nonworse_tiers += int(accuracy_delta is not None and accuracy_delta >= -1e-12)
        holdout_audit[tier_name] = {
            "pooled": pooled_summary,
            "direction_balanced": balanced_summary,
            "accuracy_delta": accuracy_delta,
            "bootstrap_difference": _bootstrap_difference(holdout_details, tier),
        }

    top10 = holdout_audit["top_10pct"]
    balanced_top10 = top10["direction_balanced"]
    pooled_top10 = top10["pooled"]
    if top10["accuracy_delta"] is None or float(top10["accuracy_delta"]) < 0.0:
        fail_reasons.append("top-10% direction-balanced accuracy did not match or exceed pooled V6.0.1 selection")
    for direction in ("home", "away"):
        item = balanced_top10["by_direction"][direction]
        if int(item["count"]) < 30:
            fail_reasons.append(f"top-10% {direction} sample below 30")
        if item["wilson90_lower"] is None or float(item["wilson90_lower"]) < 0.65:
            fail_reasons.append(f"top-10% {direction} Wilson 90% lower bound below 65%")
    if nonworse_tiers < 2:
        fail_reasons.append("fewer than two of three coverage tiers were nonworse versus pooled selection")
    if float(top10["bootstrap_difference"]["ci90"][0]) < -0.005:
        fail_reasons.append("top-10% bootstrap 90% lower bound below -0.50 percentage point")
    pooled_away = int(pooled_top10["by_direction"]["away"]["count"])
    balanced_away = int(balanced_top10["by_direction"]["away"]["count"])
    if balanced_away < pooled_away:
        fail_reasons.append("top-10% away selection count decreased versus pooled threshold")

    result = {
        "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
        "challenge_gate_passed": not fail_reasons,
        "challenge_gate_fail_reasons": fail_reasons,
        "nonworse_tier_count": nonworse_tiers,
        "thresholds": thresholds,
        "validation_audit": validation_audit,
        "development_holdout_audit": holdout_audit,
    }

    payload = {
        "schema_version": "V6.0.4-selective-direction-lcb-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "probability_model": "frozen V6.0.1 direct-outcome model",
            "l2": l2,
            "pool_weight": pool_weight,
            "draw_ratio": draw_ratio,
            "selection_policy": "validation-frozen confidence thresholds, separately by home and away direction",
            "coverage_targets": list(COVERAGES),
            "agreement_required": True,
            "draws_excluded": True,
            "wilson_confidence": 0.90,
            "holdout_note": "development holdout already viewed; not a pristine forward test",
            "historical_market_odds_used": False,
            "manual_probability_adjustment": False,
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
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
