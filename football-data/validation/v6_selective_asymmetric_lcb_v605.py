#!/usr/bin/env python3
"""V6.0.5 asymmetric lower-confidence-bound execution challenge.

The V6.0.1 probability model remains frozen. Home and away execution coverage are
selected independently on the validation season from a pre-registered coverage grid.
The largest coverage meeting a direction-specific Wilson 90% lower-bound floor is
frozen, then compared on the development holdout with a pooled threshold selecting the
same validation sample count. This is research-only and cannot change runtime outputs.
"""
from __future__ import annotations

import json
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
import v6_selective_direction_lcb_v604 as v604
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
OUT = ROOT / "manifests" / "v6_selective_asymmetric_lcb_v605_status.json"
COVERAGE_GRID = (0.10, 0.05, 0.025, 0.01)
DIRECTION_FLOORS = {
    "home": {"wilson90_lower": 0.75, "minimum_count": 50},
    "away": {"wilson90_lower": 0.65, "minimum_count": 30},
}
BOOTSTRAP_REPS = 2000
BOOTSTRAP_SEED = 605


def _eligible(details: list[dict[str, Any]], direction: str | None = None) -> list[dict[str, Any]]:
    rows = [item for item in details if item["pick"] != "draw" and item["agreement"] == 1]
    if direction is not None:
        rows = [item for item in rows if item["pick"] == direction]
    return rows


def _direction_policy(validation_details: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for direction in ("home", "away"):
        rows = _eligible(validation_details, direction)
        floor = DIRECTION_FLOORS[direction]
        candidates: list[dict[str, Any]] = []
        selected: dict[str, Any] | None = None
        for coverage in COVERAGE_GRID:
            threshold = v604._threshold([float(item["confidence"]) for item in rows], coverage)
            chosen = [item for item in rows if float(item["confidence"]) >= threshold]
            hits = sum(int(item["hit"]) for item in chosen)
            audit = {
                "coverage_target": coverage,
                "threshold": threshold,
                "count": len(chosen),
                "hits": hits,
                "accuracy": hits / len(chosen) if chosen else None,
                "wilson90_lower": v604._wilson_lower(hits, len(chosen)),
            }
            audit["gate_passed"] = (
                int(audit["count"]) >= int(floor["minimum_count"])
                and audit["wilson90_lower"] is not None
                and float(audit["wilson90_lower"]) >= float(floor["wilson90_lower"])
            )
            candidates.append(audit)
            if selected is None and audit["gate_passed"]:
                selected = audit
        output[direction] = {
            "floor": floor,
            "candidates": candidates,
            "selected": selected,
            "enabled": selected is not None,
        }
    return output


def _apply_policy(details: list[dict[str, Any]], policy: dict[str, Any]) -> list[dict[str, Any]]:
    chosen: list[dict[str, Any]] = []
    for item in details:
        direction = str(item["pick"])
        if direction not in ("home", "away") or int(item["agreement"]) != 1:
            continue
        selected = policy[direction]["selected"]
        if selected is None:
            continue
        if float(item["confidence"]) >= float(selected["threshold"]):
            chosen.append(item)
    return chosen


def _pooled_threshold_for_count(validation_details: list[dict[str, Any]], count: int) -> float:
    rows = sorted(_eligible(validation_details), key=lambda item: float(item["confidence"]), reverse=True)
    if count <= 0 or not rows:
        return float("inf")
    index = min(len(rows), count) - 1
    return float(rows[index]["confidence"])


def _apply_pooled(details: list[dict[str, Any]], threshold: float) -> list[dict[str, Any]]:
    return [
        item for item in _eligible(details)
        if float(item["confidence"]) >= float(threshold)
    ]


def _max_competition_share(chosen: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(str(item["competition_id"]) for item in chosen)
    if not chosen:
        return {"competition_id": None, "count": 0, "share": 0.0}
    cid, count = counts.most_common(1)[0]
    return {"competition_id": cid, "count": count, "share": count / len(chosen)}


def _bootstrap(
    details: list[dict[str, Any]],
    policy: dict[str, Any],
    pooled_threshold: float,
) -> dict[str, Any]:
    n = len(details)
    rng = random.Random(BOOTSTRAP_SEED)
    samples: list[float] = []
    for _ in range(BOOTSTRAP_REPS):
        sample = [details[rng.randrange(n)] for _ in range(n)]
        policy_rows = _apply_policy(sample, policy)
        pooled_rows = _apply_pooled(sample, pooled_threshold)
        if not policy_rows or not pooled_rows:
            continue
        policy_acc = sum(int(item["hit"]) for item in policy_rows) / len(policy_rows)
        pooled_acc = sum(int(item["hit"]) for item in pooled_rows) / len(pooled_rows)
        samples.append(policy_acc - pooled_acc)
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
        "probability_policy_better": sum(1 for value in samples if value > 0.0) / m,
    }


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
            "schema_version": "V6.0.5-selective-asymmetric-lcb-r1",
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
    validation_details = v604._details(validation_rows, fit_models, pool_weight, draw_ratio)
    policy = _direction_policy(validation_details)
    validation_policy_rows = _apply_policy(validation_details, policy)
    matched_pooled_threshold = _pooled_threshold_for_count(validation_details, len(validation_policy_rows))
    validation_pooled_rows = _apply_pooled(validation_details, matched_pooled_threshold)

    refit_models = base._fit_models(fit_rows + validation_rows, l2)
    holdout_details = v604._details(holdout_rows, refit_models, pool_weight, draw_ratio)
    holdout_policy_rows = _apply_policy(holdout_details, policy)
    holdout_pooled_rows = _apply_pooled(holdout_details, matched_pooled_threshold)

    validation_policy_summary = v604._summary(validation_policy_rows, len(validation_details))
    validation_pooled_summary = v604._summary(validation_pooled_rows, len(validation_details))
    holdout_policy_summary = v604._summary(holdout_policy_rows, len(holdout_details))
    holdout_pooled_summary = v604._summary(holdout_pooled_rows, len(holdout_details))
    accuracy_delta = (
        float(holdout_policy_summary["accuracy"]) - float(holdout_pooled_summary["accuracy"])
        if holdout_policy_summary["accuracy"] is not None and holdout_pooled_summary["accuracy"] is not None
        else None
    )
    bootstrap = _bootstrap(holdout_details, policy, matched_pooled_threshold)
    concentration = _max_competition_share(holdout_policy_rows)

    fail_reasons: list[str] = []
    if int(holdout_policy_summary["count"]) < 100:
        fail_reasons.append("development holdout execution count below 100")
    if accuracy_delta is None or float(accuracy_delta) < 0.0:
        fail_reasons.append("development holdout accuracy did not match or exceed matched pooled threshold")
    if holdout_policy_summary["wilson90_lower"] is None or float(holdout_policy_summary["wilson90_lower"]) < 0.78:
        fail_reasons.append("combined development holdout Wilson 90% lower bound below 78%")
    if float(bootstrap["ci90"][0]) < 0.0:
        fail_reasons.append("bootstrap 90% lower bound did not remain nonnegative")
    if float(concentration["share"]) > 0.35:
        fail_reasons.append("single-competition execution concentration above 35%")
    if policy["away"]["enabled"]:
        away = holdout_policy_summary["by_direction"]["away"]
        if int(away["count"]) < 15:
            fail_reasons.append("enabled away execution produced fewer than 15 holdout selections")
        if away["wilson90_lower"] is None or float(away["wilson90_lower"]) < 0.50:
            fail_reasons.append("away holdout Wilson 90% lower bound below 50%")

    payload = {
        "schema_version": "V6.0.5-selective-asymmetric-lcb-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "probability_model": "frozen V6.0.1 direct-outcome model",
            "coverage_grid": list(COVERAGE_GRID),
            "direction_floors": DIRECTION_FLOORS,
            "selection_rule": "largest validation coverage satisfying direction-specific Wilson 90% lower-bound floor",
            "benchmark": "pooled validation threshold matched to policy validation sample count",
            "agreement_required": True,
            "draws_excluded": True,
            "holdout_note": "development holdout already viewed; not a pristine forward test",
            "historical_market_odds_used": False,
            "manual_probability_adjustment": False,
        },
        "row_counts": {
            "fit": len(fit_rows),
            "selection_validation": len(validation_rows),
            "development_holdout": len(holdout_rows),
        },
        "result": {
            "status": "CHALLENGE_GATE_PASS" if not fail_reasons else "CHALLENGE_GATE_FAIL",
            "challenge_gate_passed": not fail_reasons,
            "challenge_gate_fail_reasons": fail_reasons,
            "direction_policy": policy,
            "matched_pooled_threshold": matched_pooled_threshold,
            "validation_policy": validation_policy_summary,
            "validation_matched_pooled": validation_pooled_summary,
            "development_holdout_policy": holdout_policy_summary,
            "development_holdout_matched_pooled": holdout_pooled_summary,
            "accuracy_delta": accuracy_delta,
            "bootstrap_difference": bootstrap,
            "maximum_competition_concentration": concentration,
        },
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
