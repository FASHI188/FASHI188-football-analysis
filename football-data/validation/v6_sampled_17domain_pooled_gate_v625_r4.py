#!/usr/bin/env python3
"""V6.2.5 r4: identity-safe sampled gate with the exact pooled V6.0.1 architecture.

Corrects an audit issue in r2/r3: those sampled scripts fitted one model per competition,
while V6.0.1 actually pools all 17 competition domains before fitting the draw/side experts.

This r4 keeps the exact same V6.2.5 r2 outcome-blind sample seed and identities, but:
- builds the same four season-role rows for all 17 domains;
- fits ONE pooled model on all first-two-season rows for older-sample scoring;
- refits ONE pooled model on fit + full selection-validation rows for newer-sample scoring;
- applies the frozen V6.0.1 l2/pool/draw-ratio parameters;
- selects the 65%-target cutoff on older 850 only and tests unchanged on newer 850.

Research-only correction. No CURRENT/formal/runtime/V6.1 pristine-forward mutation.
"""
from __future__ import annotations

import hashlib
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
import v6_market_residual_fusion_v620 as v620
import v6_sampled_17domain_gate_v625_r2 as r2
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_sampled_17domain_pooled_gate_v625_r4_status.json"
CACHE_OUT = ROOT / "manifests" / "v6_sampled_17domain_pooled_scored_cache_v625_r4.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
EXPECTED_PANEL_SHA = "487ebc28be9e541f530f2baab865a5a7bb4599384cc059b75f2dc867f50962cf"
TARGET = 0.65
Z90 = 1.6448536269514722


def _wilson_lower(hits: int, count: int) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    z = Z90
    denom = 1.0 + z * z / count
    centre = p + z * z / (2.0 * count)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count)
    return (centre - radius) / denom


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    hits = sum(int(bool(r["hit"])) for r in rows)
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "wilson90_lower": _wilson_lower(hits, count),
        "predicted_direction_counts": dict(Counter(str(r["pick"]) for r in rows)),
    }


def _select_threshold(calibration: list[dict[str, Any]], eligible_only: bool) -> dict[str, Any] | None:
    pool = [r for r in calibration if (r["eligible_prior_selective"] if eligible_only else True)]
    best = None
    for threshold in sorted({float(r["confidence"]) for r in pool}):
        chosen = [r for r in pool if float(r["confidence"]) >= threshold]
        if len(chosen) < 50:
            continue
        m = _metric(chosen)
        if float(m["accuracy"]) + 1e-15 < TARGET:
            continue
        candidate = {
            "threshold": threshold,
            "eligible_only": eligible_only,
            **m,
            "coverage_of_calibration": len(chosen) / len(calibration),
        }
        if best is None or candidate["count"] > best["count"] or (
            candidate["count"] == best["count"] and candidate["accuracy"] > best["accuracy"]
        ):
            best = candidate
    return best


def _apply(rows: list[dict[str, Any]], selection: dict[str, Any] | None) -> dict[str, Any]:
    if not selection:
        return {"status": "NO_65PCT_CALIBRATION_THRESHOLD"}
    chosen = [
        r for r in rows
        if (r["eligible_prior_selective"] if selection["eligible_only"] else True)
        and float(r["confidence"]) >= float(selection["threshold"])
    ]
    m = _metric(chosen)
    return {
        "status": "PASS" if chosen else "NO_TEST_SELECTIONS",
        "threshold": float(selection["threshold"]),
        "eligible_only": bool(selection["eligible_only"]),
        **m,
        "coverage_of_test": len(chosen) / len(rows) if rows else 0.0,
        "target_65_raw_accuracy_met": bool(chosen) and float(m["accuracy"]) >= TARGET,
    }


def _cache_row(row: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "competition_id": row["competition_id"],
        "season": row["season"],
        "role": role,
        "date": row["date"],
        "home_team": row["home_team"],
        "away_team": row["away_team"],
        "identity": r2._identity(row),
        "sample_hash": r2._sample_key(row),
        "formal": {k: float(row["formal"][k]) for k in base.CLASSES},
        "q": {k: float(row["q"][k]) for k in base.CLASSES},
        "pick": row["pick"],
        "formal_pick": row["formal_pick"],
        "confidence": float(row["confidence"]),
        "eligible_prior_selective": bool(row["eligible_prior_selective"]),
        "actual_result": row["actual_result"],
        "hit": bool(row["hit"]),
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    v601 = load_json(V601_STATUS)
    selected = ((v601.get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected.get("l2", 1.0))
    pool_weight = float(selected.get("pool_weight", 0.75))
    draw_ratio = float(selected.get("draw_ratio", 0.80))

    domains = sorted((load_json(base.FORMAL_STATUS).get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, found {len(domains)}")

    built_by_domain: dict[str, dict[str, list[dict[str, Any]]]] = {}
    roles: dict[str, dict[str, Any]] = {}
    pooled_fit: list[dict[str, Any]] = []
    pooled_validation: list[dict[str, Any]] = []

    for cid in domains:
        report = load_json(base.REPORT_ROOT / f"{cid}.json")
        completed = _completed_outer_seasons_last_complete_only(report)
        if len(completed) < 4:
            raise PlatformError(f"{cid}: insufficient completed seasons")
        seasons = completed[-4:]
        built = v620._build_domain_rows_with_identity(cid, seasons)
        ordered = sorted(built, key=_season_key)
        fit_seasons, older_eval, newer_eval = ordered[:2], ordered[2], ordered[3]
        built_by_domain[cid] = built
        roles[cid] = {"fit": fit_seasons, "older": older_eval, "newer": newer_eval}
        for season in fit_seasons:
            pooled_fit.extend(built[season])
        pooled_validation.extend(built[older_eval])

    expected_counts = v601.get("row_counts") or {}
    if len(pooled_fit) != int(expected_counts.get("fit", -1)):
        raise PlatformError(f"pooled fit count drift: {len(pooled_fit)} vs {expected_counts.get('fit')}")
    if len(pooled_validation) != int(expected_counts.get("selection_validation", -1)):
        raise PlatformError(
            f"pooled validation count drift: {len(pooled_validation)} vs {expected_counts.get('selection_validation')}"
        )

    older_model = base._fit_models(pooled_fit, l2)
    newer_model = base._fit_models(pooled_fit + pooled_validation, l2)

    older_sample: list[dict[str, Any]] = []
    newer_sample: list[dict[str, Any]] = []
    cache_rows: list[dict[str, Any]] = []
    by_domain: dict[str, Any] = {}

    for cid in domains:
        built = built_by_domain[cid]
        older_eval = str(roles[cid]["older"])
        newer_eval = str(roles[cid]["newer"])
        domain = {"older_season": older_eval, "newer_season": newer_eval, "seasons": {}}
        for role, season, model, collector in (
            ("older", older_eval, older_model, older_sample),
            ("newer", newer_eval, newer_model, newer_sample),
        ):
            available = list(built[season])
            chosen = sorted(available, key=r2._sample_key)[:r2.SAMPLE_PER_SEASON]
            if len(chosen) != 50 or len({r2._identity(r) for r in chosen}) != 50:
                raise PlatformError(f"{cid} {season}: invalid fixed sample")
            scored = [r2._score_row(r, model, pool_weight, draw_ratio) for r in chosen]
            collector.extend(scored)
            cache_rows.extend(_cache_row(r, role) for r in scored)
            domain["seasons"][season] = {
                "role": role,
                "sampled_rows": 50,
                "metrics": _metric(scored),
            }
        by_domain[cid] = domain

    if len(older_sample) != 850 or len(newer_sample) != 850:
        raise PlatformError(f"expected 850+850, got {len(older_sample)}+{len(newer_sample)}")
    if len({r2._identity(r) for r in older_sample + newer_sample}) != 1700:
        raise PlatformError("sample identity uniqueness failure")

    cache_rows.sort(key=lambda r: (r["competition_id"], r["season"], r["sample_hash"]))
    panel_sha = hashlib.sha256("\n".join(r["sample_hash"] for r in cache_rows).encode("utf-8")).hexdigest()
    if panel_sha != EXPECTED_PANEL_SHA:
        raise PlatformError(f"fixed panel drift: {panel_sha} != {EXPECTED_PANEL_SHA}")

    select_all = _select_threshold(older_sample, False)
    select_prior = _select_threshold(older_sample, True)
    full_older = _metric(older_sample)
    full_newer = _metric(newer_sample)
    full_combined = _metric(older_sample + newer_sample)

    cache_digest = hashlib.sha256(
        "\n".join(
            f'{r["identity"]}|{r["q"]["home"]:.17g}|{r["q"]["draw"]:.17g}|{r["q"]["away"]:.17g}|{r["actual_result"]}'
            for r in cache_rows
        ).encode("utf-8")
    ).hexdigest()
    atomic_write_json(CACHE_OUT, {
        "schema_version": "V6.2.5-fixed-sampled-pooled-scored-cache-r4",
        "generated_at_utc": generated.isoformat(),
        "architecture": "exact V6.0.1 pooled 17-domain draw/side experts",
        "panel_sha256": panel_sha,
        "content_sha256": cache_digest,
        "count": 1700,
        "roles": {"older": 850, "newer": 850},
        "v601_frozen_parameters": {"l2": l2, "pool_weight": pool_weight, "draw_ratio": draw_ratio},
        "rows": cache_rows,
    })

    payload = {
        "schema_version": "V6.2.5-fixed-sampled-17domain-pooled-gate-r4",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "correction": {
            "r2_r3_issue": "sampled gate fitted one direct-outcome model per competition",
            "v601_actual_architecture": "single pooled model across all 17 domains",
            "r4_action": "same fixed 1700 identities rescored with exact pooled V6.0.1 architecture",
            "sample_changed": False,
            "sample_seed_changed": False,
            "v601_hyperparameters_changed": False,
        },
        "design": {
            "competition_count": 17,
            "sample_per_eval_season": 50,
            "actual_total": 1700,
            "panel_sha256": panel_sha,
            "pooled_fit_count": len(pooled_fit),
            "pooled_validation_count": len(pooled_validation),
            "v601_frozen_parameters": {"l2": l2, "pool_weight": pool_weight, "draw_ratio": draw_ratio},
        },
        "full_direction_metrics": {
            "older_850": full_older,
            "newer_850": full_newer,
            "combined_1700": full_combined,
        },
        "target_65_diagnostic": {
            "method": "older 850 selects maximum-coverage cutoff at >=65%; newer 850 tests unchanged",
            "all_predictions": {
                "calibration_selection": select_all,
                "newer_test": _apply(newer_sample, select_all),
            },
            "non_draw_formal_agreement": {
                "calibration_selection": select_prior,
                "newer_test": _apply(newer_sample, select_prior),
            },
        },
        "by_domain": by_domain,
        "cache": {
            "path": "manifests/v6_sampled_17domain_pooled_scored_cache_v625_r4.json",
            "content_sha256": cache_digest,
        },
        "governance": {
            "research_correction_only": True,
            "not_pristine_promotion_evidence": True,
            "holdout_not_used_for_sample_or_threshold_selection": True,
            "current_rule_change": False,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
