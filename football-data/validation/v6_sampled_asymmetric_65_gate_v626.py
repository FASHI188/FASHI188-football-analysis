#!/usr/bin/env python3
"""V6.2.6 asymmetric selective gate on the fixed V6.2.5 sampled panel.

Research-only fast challenge:
- exactly the same 17 domains and deterministic 50+50 sampled identities as V6.2.5 r2;
- older 850 only select home/away confidence thresholds;
- newer 850 only test the frozen thresholds;
- draws excluded from selective execution;
- formal/V6 direction agreement required;
- no CURRENT, formal-weight, runtime-probability, or V6.1 pristine-forward mutation.
"""
from __future__ import annotations

import json
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
import v6_sampled_17domain_gate_v625_r2 as v625
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json

OUT = ROOT / "manifests" / "v6_sampled_asymmetric_65_gate_v626_status.json"
V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
TARGET = 0.65
MIN_TOTAL = 120
MIN_DIRECTION = 20


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    count = len(rows)
    hits = sum(int(r["hit"]) for r in rows)
    by_direction: dict[str, Any] = {}
    for direction in ("home", "away"):
        part = [r for r in rows if r["pick"] == direction]
        ph = sum(int(r["hit"]) for r in part)
        by_direction[direction] = {
            "count": len(part),
            "hits": ph,
            "accuracy": ph / len(part) if part else None,
            "wilson90_lower": v625._wilson_lower(ph, len(part)),
        }
    return {
        "count": count,
        "hits": hits,
        "accuracy": hits / count if count else None,
        "wilson90_lower": v625._wilson_lower(hits, count),
        "coverage_of_850": count / 850.0,
        "predicted_direction_counts": dict(Counter(str(r["pick"]) for r in rows)),
        "by_direction": by_direction,
    }


def _build_samples() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    domains = sorted((load_json(base.FORMAL_STATUS).get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 domains, found {len(domains)}")

    selected = ((load_json(V601_STATUS).get("result") or {}).get("selected_candidate") or {})
    l2 = float(selected.get("l2", 1.0))
    pool_weight = float(selected.get("pool_weight", 0.75))
    draw_ratio = float(selected.get("draw_ratio", 0.80))

    calibration: list[dict[str, Any]] = []
    test: list[dict[str, Any]] = []
    audit: dict[str, Any] = {}

    for cid in domains:
        report = load_json(base.REPORT_ROOT / f"{cid}.json")
        completed = _completed_outer_seasons_last_complete_only(report)
        if len(completed) < 4:
            raise PlatformError(f"{cid}: need >=4 completed seasons")
        seasons = completed[-4:]
        built = v620._build_domain_rows_with_identity(cid, seasons)
        ordered = sorted(built, key=_season_key)
        fit_seasons, older_eval, newer_eval = ordered[:2], ordered[2], ordered[3]

        fit_rows: list[dict[str, Any]] = []
        for season in fit_seasons:
            fit_rows.extend(built[season])
        older_model = base._fit_models(fit_rows, l2)
        newer_model = base._fit_models(fit_rows + built[older_eval], l2)

        domain_audit = {"older": older_eval, "newer": newer_eval, "older_sample": [], "newer_sample": []}
        for role, season, model, collector in (
            ("older", older_eval, older_model, calibration),
            ("newer", newer_eval, newer_model, test),
        ):
            available = list(built[season])
            chosen = sorted(available, key=v625._sample_key)[:v625.SAMPLE_PER_SEASON]
            if len(chosen) != 50:
                raise PlatformError(f"{cid} {season}: expected 50 sampled rows, got {len(chosen)}")
            scored = [v625._score_row(row, model, pool_weight, draw_ratio) for row in chosen]
            collector.extend(scored)
            domain_audit[f"{role}_sample"] = [v625._identity(row) for row in chosen]
        audit[cid] = domain_audit

    if len(calibration) != 850 or len(test) != 850:
        raise PlatformError(f"expected 850+850, got {len(calibration)}+{len(test)}")
    if len({v625._identity(r) for r in calibration + test}) != 1700:
        raise PlatformError("sample identities are not unique")
    return calibration, test, audit


def _eligible(row: dict[str, Any]) -> bool:
    return bool(row["eligible_prior_selective"]) and row["pick"] in ("home", "away")


def _threshold_grid(rows: list[dict[str, Any]], direction: str) -> list[float]:
    values = sorted({float(r["confidence"]) for r in rows if _eligible(r) and r["pick"] == direction})
    # Exact observed thresholds are deterministic and calibration-only. Add +inf sentinel via max+1.
    return values + ([max(values) + 1.0] if values else [])


def _choose(calibration: list[dict[str, Any]]) -> dict[str, Any] | None:
    home_grid = _threshold_grid(calibration, "home")
    away_grid = _threshold_grid(calibration, "away")
    best: dict[str, Any] | None = None

    # Search only on older 850. The objective is maximum coverage subject to >=65% raw accuracy.
    # Direction minimums stop a solution from silently becoming a one-direction-only rule.
    for hthr in home_grid:
        home = [r for r in calibration if _eligible(r) and r["pick"] == "home" and float(r["confidence"]) >= hthr]
        if len(home) < MIN_DIRECTION:
            continue
        for athr in away_grid:
            away = [r for r in calibration if _eligible(r) and r["pick"] == "away" and float(r["confidence"]) >= athr]
            if len(away) < MIN_DIRECTION:
                continue
            chosen = home + away
            if len(chosen) < MIN_TOTAL:
                continue
            metric = _metric(chosen)
            if float(metric["accuracy"]) + 1e-15 < TARGET:
                continue
            candidate = {
                "home_threshold": hthr,
                "away_threshold": athr,
                **metric,
            }
            if best is None:
                best = candidate
                continue
            # Pre-registered ranking: coverage first, then Wilson lower bound, then raw accuracy.
            ranking = (candidate["count"], candidate["wilson90_lower"] or -1.0, candidate["accuracy"] or -1.0)
            incumbent = (best["count"], best["wilson90_lower"] or -1.0, best["accuracy"] or -1.0)
            if ranking > incumbent:
                best = candidate
    return best


def _apply(rows: list[dict[str, Any]], rule: dict[str, Any] | None) -> dict[str, Any]:
    if not rule:
        return {"status": "NO_CALIBRATION_RULE"}
    chosen = [
        r for r in rows
        if _eligible(r)
        and (
            (r["pick"] == "home" and float(r["confidence"]) >= float(rule["home_threshold"]))
            or (r["pick"] == "away" and float(r["confidence"]) >= float(rule["away_threshold"]))
        )
    ]
    metric = _metric(chosen)
    return {
        "status": "PASS" if chosen else "NO_TEST_SELECTIONS",
        "home_threshold": float(rule["home_threshold"]),
        "away_threshold": float(rule["away_threshold"]),
        **metric,
        "target_65_raw_accuracy_met": bool(chosen) and float(metric["accuracy"]) >= TARGET,
    }


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    calibration, test, audit = _build_samples()
    rule = _choose(calibration)
    test_result = _apply(test, rule)
    baseline = load_json(ROOT / "manifests" / "v6_sampled_17domain_gate_v625_status.json")
    previous = (((baseline.get("target_65_diagnostic") or {}).get("prior_selective_eligibility_non_draw_formal_agreement") or {}).get("newer_season_test") or {})

    payload = {
        "schema_version": "V6.2.6-sampled-asymmetric-65-gate-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "design": {
            "panel": "V6.2.5 r2 fixed 17-domain 50+50 outcome-blind sample",
            "calibration_count": 850,
            "test_count": 850,
            "selection": "home/away confidence thresholds selected only on older 850",
            "eligibility": "non-draw + V6/formal direction agreement",
            "target_accuracy": TARGET,
            "min_total_calibration_selection": MIN_TOTAL,
            "min_per_direction_calibration_selection": MIN_DIRECTION,
        },
        "calibration_selected_rule": rule,
        "newer_850_test": test_result,
        "comparison_to_v625_pooled_gate": {
            "v625_test_count": previous.get("count"),
            "v625_test_accuracy": previous.get("accuracy"),
            "v625_test_coverage": previous.get("coverage_of_test"),
            "v626_test_count": test_result.get("count"),
            "v626_test_accuracy": test_result.get("accuracy"),
            "v626_test_coverage": test_result.get("coverage_of_850"),
            "accuracy_delta_pp": (
                100.0 * (float(test_result["accuracy"]) - float(previous["accuracy"]))
                if test_result.get("accuracy") is not None and previous.get("accuracy") is not None else None
            ),
        },
        "sample_identity_audit": {
            "domains": len(audit),
            "unique_1700": len({v625._identity(r) for r in calibration + test}),
            "sample_seed": v625.SAMPLE_SEED,
        },
        "governance": {
            "fast_development_gate_only": True,
            "newer_850_not_used_for_threshold_selection": True,
            "not_pristine_promotion_evidence": True,
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
