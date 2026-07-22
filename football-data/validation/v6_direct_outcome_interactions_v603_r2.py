#!/usr/bin/env python3
"""V6.0.3-r2 interaction challenge with frozen V6.0.1 hyperparameters.

Only three pre-registered feature families are compared. L2, pool weight and draw
boundary are inherited unchanged from the V6.0.1 receipt. This removes repeated
hyperparameter fitting and reduces development-holdout oversearch.
"""
from __future__ import annotations

import json
import sys
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
import v6_direct_outcome_interactions_v603 as ext
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_direct_outcome_interactions_v603_status.json"


def main() -> int:
    status601 = load_json(ext.V601_STATUS)
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
            "schema_version": "V6.0.3-interactions-r2",
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
    all_rows = fit_rows + validation_rows + holdout_rows

    anchor_fit_models = base._fit_models(fit_rows, anchor_l2)
    anchor_validation = ext._metrics(
        validation_rows, anchor_fit_models, anchor_pool, draw_ratio, use_enriched=False
    )

    candidates: list[dict[str, Any]] = []
    model_cache: dict[str, dict[str, Any]] = {}
    validation_cache: dict[str, dict[str, Any]] = {}
    for family in ext.FEATURE_FAMILIES:
        try:
            ext._attach_family(all_rows, family)
            models = ext._fit_models(fit_rows, anchor_l2)
            metrics = ext._metrics(
                validation_rows, models, anchor_pool, draw_ratio, use_enriched=True
            )
            model_cache[family] = models
            validation_cache[family] = metrics
            proper_nonworse = (
                float(metrics["mean_log_loss"]) <= float(anchor_validation["mean_log_loss"]) + 1e-12
                and float(metrics["mean_brier"]) <= float(anchor_validation["mean_brier"]) + 1e-12
                and float(metrics["mean_rps"]) <= float(anchor_validation["mean_rps"]) + 1e-12
            )
            candidates.append({
                "feature_family": family,
                "l2": anchor_l2,
                "pool_weight": anchor_pool,
                "draw_ratio": draw_ratio,
                "validation": ext._strip(metrics),
                "proper_scores_nonworse": proper_nonworse,
                "draw_count_gate": int(metrics["draw_prediction_count"]) >= ext.MIN_VALIDATION_DRAW_PICKS,
                "anchor_accuracy_nonworse": float(metrics["accuracy"]) >= float(anchor_validation["accuracy"]) - 1e-12,
            })
        except Exception as exc:
            candidates.append({
                "feature_family": family,
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
        ext.FEATURE_FAMILIES.index(str(item["feature_family"])),
    ))

    if not eligible:
        result = {
            "status": "NO_VALIDATION_SAFE_INTERACTION_CANDIDATE",
            "selected_candidate": None,
            "challenge_gate_passed": False,
            "challenge_gate_fail_reasons": [
                "no fixed-parameter feature family matched V6.0.1 validation accuracy, proper scores and draw-count gate"
            ],
        }
    else:
        selected = eligible[0]
        family = str(selected["feature_family"])
        ext._attach_family(all_rows, family)
        refit_models = ext._fit_models(fit_rows + validation_rows, anchor_l2)
        anchor_refit_models = base._fit_models(fit_rows + validation_rows, anchor_l2)
        selected_validation = validation_cache[family]
        candidate_holdout = ext._metrics(
            holdout_rows, refit_models, anchor_pool, draw_ratio, use_enriched=True
        )
        anchor_holdout = ext._metrics(
            holdout_rows, anchor_refit_models, anchor_pool, draw_ratio, use_enriched=False
        )
        formal_holdout = ext._metrics(holdout_rows, None, 0.0, 1.0, use_enriched=False)
        candidate_selective = ext._selective(
            candidate_holdout, ext._thresholds(selected_validation)
        )
        anchor_selective = ext._selective(
            anchor_holdout, ext._thresholds(anchor_validation)
        )
        paired = ext._paired_bootstrap(candidate_holdout, anchor_holdout)
        domains_result = ext._domain_comparison(candidate_holdout, anchor_holdout)

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
                "l2": anchor_l2,
                "pool_weight": anchor_pool,
                "draw_ratio": draw_ratio,
                "selection_validation": selected["validation"],
            },
            "refit_audit": refit_models,
            "formal_holdout": ext._strip(formal_holdout),
            "v601_holdout": ext._strip(anchor_holdout),
            "candidate_holdout": ext._strip(candidate_holdout),
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
        "schema_version": "V6.0.3-interactions-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "competition_count_requested": 17,
        "competition_count_completed": 17,
        "season_roles": season_roles,
        "method": {
            "anchor": "V6.0.1 direct-outcome model, L2, pool weight and draw boundary",
            "feature_families": list(ext.FEATURE_FAMILIES),
            "selection_policy": "three pre-registered feature families only; no hyperparameter grid",
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
            "validation": ext._strip(anchor_validation),
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
