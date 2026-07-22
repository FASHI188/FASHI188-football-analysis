#!/usr/bin/env python3
"""V6.2.5 fixed sampled 17-domain evaluation panel.

User-requested fast research gate:
- 17 competition domains
- latest two completed seasons per domain
- deterministic 50-match sample per season (target 1,700 matches)
- sampling uses only competition/season/date/team identity hash, never result/probability
- older sampled season is used only to select a 65%-target confidence cutoff
- newer sampled season is used to test that cutoff

This is a fast development panel, not pristine promotion evidence. It never mutates CURRENT,
formal weights, V6.1 pristine-forward predictions, or runtime probabilities.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
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

OUT = ROOT / "manifests" / "v6_sampled_17domain_gate_v625_status.json"
PANEL_OUT = ROOT / "manifests" / "v6_sampled_17domain_panel_v625.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
SAMPLE_SEED = "V6.2.5-17domain-2season-50-v1"
SAMPLE_PER_SEASON = 50
TARGET_ACCURACY = 0.65
Z90 = 1.6448536269514722


def _identity(row: dict[str, Any]) -> str:
    return "|".join([
        str(row["competition_id"]),
        str(row["season"]),
        str(row.get("date") or ""),
        str(row.get("home_team") or ""),
        str(row.get("away_team") or ""),
    ])


def _sample_key(row: dict[str, Any]) -> str:
    return hashlib.sha256((SAMPLE_SEED + "|" + _identity(row)).encode("utf-8")).hexdigest()


def _wilson_lower(hits: int, count: int, z: float = Z90) -> float | None:
    if count <= 0:
        return None
    p = hits / count
    denom = 1.0 + z * z / count
    centre = p + z * z / (2.0 * count)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * count)) / count)
    return (centre - radius) / denom


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    hits = sum(int(r["hit"]) for r in rows)
    by_pick = Counter(str(r["pick"]) for r in rows)
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "wilson90_lower": _wilson_lower(hits, count),
        "predicted_direction_counts": dict(by_pick),
    }


def _pick_confidence(row: dict[str, Any], model: dict[str, Any], pool_weight: float, draw_ratio: float) -> dict[str, Any]:
    direct = base._direct_probability(row, model)
    q = base._log_pool(row["formal"], direct, pool_weight)
    pick = v601._pick(q, draw_ratio)
    ordered = sorted((float(q[k]), k) for k in base.CLASSES)
    confidence = ordered[-1][0] - ordered[-2][0]
    formal_pick = max(base.CLASSES, key=lambda k: float(row["formal"][k]))
    truth = str(row["actual_result"])
    return {
        **row,
        "q": q,
        "pick": pick,
        "formal_pick": formal_pick,
        "confidence": confidence,
        "agreement": pick == formal_pick,
        "eligible_prior_selective": pick != "draw" and pick == formal_pick,
        "hit": pick == truth,
    }


def _select_threshold(calibration: list[dict[str, Any]], eligible_only: bool) -> dict[str, Any] | None:
    pool = [r for r in calibration if (r["eligible_prior_selective"] if eligible_only else True)]
    if not pool:
        return None
    thresholds = sorted({float(r["confidence"]) for r in pool})
    best: dict[str, Any] | None = None
    for threshold in thresholds:
        chosen = [r for r in pool if float(r["confidence"]) >= threshold]
        if len(chosen) < 50:
            continue
        m = _metric(chosen)
        if float(m["accuracy"]) + 1e-15 < TARGET_ACCURACY:
            continue
        candidate = {
            "threshold": threshold,
            "eligible_only": eligible_only,
            **m,
            "coverage_of_calibration": len(chosen) / len(calibration) if calibration else 0.0,
        }
        if best is None or candidate["count"] > best["count"] or (
            candidate["count"] == best["count"] and candidate["accuracy"] > best["accuracy"]
        ):
            best = candidate
    return best


def _apply_threshold(rows: list[dict[str, Any]], selection: dict[str, Any] | None) -> dict[str, Any]:
    if not selection:
        return {"status": "NO_65PCT_CALIBRATION_THRESHOLD"}
    eligible_only = bool(selection["eligible_only"])
    chosen = [
        r for r in rows
        if (r["eligible_prior_selective"] if eligible_only else True)
        and float(r["confidence"]) >= float(selection["threshold"])
    ]
    m = _metric(chosen)
    return {
        "status": "PASS" if chosen else "NO_TEST_SELECTIONS",
        "threshold": float(selection["threshold"]),
        "eligible_only": eligible_only,
        **m,
        "coverage_of_test": len(chosen) / len(rows) if rows else 0.0,
        "target_65_raw_accuracy_met": bool(chosen) and float(m["accuracy"]) >= TARGET_ACCURACY,
    }


def _strip_panel_row(row: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "competition_id": row["competition_id"],
        "season": row["season"],
        "role": role,
        "sample_hash": _sample_key(row),
        "date": row.get("date"),
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    formal_status = load_json(base.FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, found {len(domains)}")

    v601_status = load_json(V601_STATUS)
    selected = ((v601_status.get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected.get("l2", 1.0))
    pool_weight = float(selected.get("pool_weight", 0.75))
    draw_ratio = float(selected.get("draw_ratio", 0.80))

    calibration_sample: list[dict[str, Any]] = []
    test_sample: list[dict[str, Any]] = []
    panel_rows: list[dict[str, Any]] = []
    by_domain: dict[str, Any] = {}
    failures: dict[str, str] = {}

    for cid in domains:
        try:
            report = load_json(base.REPORT_ROOT / f"{cid}.json")
            completed = _completed_outer_seasons_last_complete_only(report)
            if len(completed) < 4:
                raise PlatformError(f"need >=4 completed seasons, got {completed}")
            seasons = completed[-4:]
            built = base._build_domain_rows(cid, seasons)
            ordered = sorted(built, key=_season_key)
            fit_seasons = ordered[:2]
            older_eval = ordered[2]
            newer_eval = ordered[3]

            fit_rows: list[dict[str, Any]] = []
            for season in fit_seasons:
                fit_rows.extend(built[season])
            validation_model = base._fit_models(fit_rows, l2)
            holdout_model = base._fit_models(fit_rows + built[older_eval], l2)

            domain_result: dict[str, Any] = {
                "fit_seasons": fit_seasons,
                "older_sample_season": older_eval,
                "newer_sample_season": newer_eval,
                "seasons": {},
            }
            for role, season, model, collector in (
                ("calibration", older_eval, validation_model, calibration_sample),
                ("test", newer_eval, holdout_model, test_sample),
            ):
                rows = list(built[season])
                if len(rows) < SAMPLE_PER_SEASON:
                    raise PlatformError(f"{cid} {season} only {len(rows)} rows; need {SAMPLE_PER_SEASON}")
                chosen = sorted(rows, key=_sample_key)[:SAMPLE_PER_SEASON]
                scored = [_pick_confidence(row, model, pool_weight, draw_ratio) for row in chosen]
                collector.extend(scored)
                panel_rows.extend(_strip_panel_row(row, role) for row in chosen)
                domain_result["seasons"][season] = {
                    "role": role,
                    "available_rows": len(rows),
                    "sampled_rows": len(chosen),
                    "sample_sha256": hashlib.sha256("\n".join(sorted(_identity(r) for r in chosen)).encode("utf-8")).hexdigest(),
                    "metrics": _metric(scored),
                }
            both = [r for r in calibration_sample + test_sample if r["competition_id"] == cid]
            domain_result["two_season_combined"] = _metric(both)
            by_domain[cid] = domain_result
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        payload = {
            "schema_version": "V6.2.5-fixed-sampled-17domain-gate-r1",
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_DATA_BUILD",
            "failures": failures,
            "governance": {"formal_weight_change": False, "runtime_probability_change": False, "current_rule_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    if len(calibration_sample) != 850 or len(test_sample) != 850:
        raise PlatformError(f"expected 850+850 samples, got {len(calibration_sample)}+{len(test_sample)}")

    calibration_metric = _metric(calibration_sample)
    test_metric = _metric(test_sample)
    overall_metric = _metric(calibration_sample + test_sample)

    selection_all = _select_threshold(calibration_sample, eligible_only=False)
    selection_prior = _select_threshold(calibration_sample, eligible_only=True)
    test_all = _apply_threshold(test_sample, selection_all)
    test_prior = _apply_threshold(test_sample, selection_prior)

    panel_rows.sort(key=lambda r: (r["competition_id"], r["season"], r["sample_hash"]))
    panel_payload = {
        "schema_version": "V6.2.5-fixed-sampled-17domain-panel-r1",
        "generated_at_utc": generated.isoformat(),
        "sample_seed": SAMPLE_SEED,
        "sampling_rule": "lowest SHA256(seed|competition|season|date|home|away), outcome-blind",
        "target_per_competition_season": SAMPLE_PER_SEASON,
        "count": len(panel_rows),
        "panel_sha256": hashlib.sha256("\n".join(r["sample_hash"] for r in panel_rows).encode("utf-8")).hexdigest(),
        "rows": panel_rows,
    }
    atomic_write_json(PANEL_OUT, panel_payload)

    payload = {
        "schema_version": "V6.2.5-fixed-sampled-17domain-gate-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "design": {
            "competition_count": 17,
            "two_latest_completed_eval_seasons_each": True,
            "sample_per_season": SAMPLE_PER_SEASON,
            "target_total": 1700,
            "actual_total": len(calibration_sample) + len(test_sample),
            "sample_seed": SAMPLE_SEED,
            "sample_outcome_blind": True,
            "panel_path": "manifests/v6_sampled_17domain_panel_v625.json",
            "panel_sha256": panel_payload["panel_sha256"],
            "v601_frozen_parameters": {"l2": l2, "pool_weight": pool_weight, "draw_ratio": draw_ratio},
        },
        "full_direction_metrics": {
            "older_season_sample_850": calibration_metric,
            "newer_season_sample_850": test_metric,
            "combined_1700": overall_metric,
        },
        "target_65_diagnostic": {
            "method": "select maximum-coverage confidence cutoff on older 850 only; apply unchanged to newer 850",
            "all_predictions": {"calibration_selection": selection_all, "newer_season_test": test_all},
            "prior_selective_eligibility_non_draw_formal_agreement": {"calibration_selection": selection_prior, "newer_season_test": test_prior},
        },
        "by_domain": by_domain,
        "governance": {
            "fast_development_gate_only": True,
            "not_pristine_promotion_evidence": True,
            "holdout_not_used_to_choose_sample_or_threshold": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "v610_v613_pristine_forward_untouched": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
