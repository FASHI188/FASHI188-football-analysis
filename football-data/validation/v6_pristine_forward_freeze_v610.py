#!/usr/bin/env python3
"""V6.1.0 pristine forward-test freeze bundle.

Freezes the V6.0.1 direct-outcome architecture and the V6.0.5 validation-selected
execution thresholds before any eligible forward match. The pooled direct model is
refit once on all four completed development seasons. Per-competition formal score
parameters and temperatures are frozen from the last completed outer season.

Forward eligibility begins on the next UTC calendar date after this bundle is created.
No match on or before the forward start date minus one day may enter the forward test.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
VALIDATION = ROOT / "validation"
for path in (ENGINE, VALIDATION):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import v6_direct_outcome_mvp_v600 as base
from backtest_last_complete_season_all_domains_v470 import _fold_for_season, _target_season_temperature
from draw_recalibration_kl_v5535 import _season_key
from draw_recalibration_kl_v5535_r2 import _completed_outer_seasons_last_complete_only
from platform_core import PlatformError, atomic_write_json, load_json, read_processed_matches

V601_STATUS = ROOT / "manifests" / "v6_direct_outcome_draw_boundary_v601_status.json"
V604_STATUS = ROOT / "manifests" / "v6_selective_direction_lcb_v604_status.json"
V605_STATUS = ROOT / "manifests" / "v6_selective_asymmetric_lcb_v605_status.json"
OUT = ROOT / "manifests" / "v6_pristine_forward_freeze_v610_status.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe_model(model: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(model))


def main() -> int:
    generated = datetime.now(timezone.utc).replace(microsecond=0)
    forward_start = (generated.date() + timedelta(days=1)).isoformat()

    receipt601 = load_json(V601_STATUS)
    receipt604 = load_json(V604_STATUS)
    receipt605 = load_json(V605_STATUS)
    selected601 = ((receipt601.get("result") or {}).get("selected_candidate") or {})
    policy605 = ((receipt605.get("result") or {}).get("direction_policy") or {})
    thresholds604 = ((receipt604.get("result") or {}).get("thresholds") or {})
    if receipt601.get("status") != "PASS" or not selected601:
        raise PlatformError("V6.0.1 PASS receipt and selected candidate are required")
    if receipt604.get("status") != "PASS" or "top_5pct" not in thresholds604:
        raise PlatformError("V6.0.4 PASS receipt with top-5 threshold is required")
    if receipt605.get("status") != "PASS" or not policy605:
        raise PlatformError("V6.0.5 PASS receipt with frozen direction policy is required")

    home_selected = ((policy605.get("home") or {}).get("selected") or {})
    away_selected = ((policy605.get("away") or {}).get("selected") or {})
    if not home_selected or not away_selected:
        raise PlatformError("V6.0.5 must contain enabled home and away thresholds")

    l2 = float(selected601["l2"])
    pool_weight = float(selected601["pool_weight"])
    draw_ratio = float(selected601["draw_ratio"])
    pooled_top5 = float(thresholds604["top_5pct"]["pooled_threshold"])
    home_threshold = float(home_selected["threshold"])
    away_threshold = float(away_selected["threshold"])

    formal_status = load_json(base.FORMAL_STATUS)
    domains = sorted((formal_status.get("reports") or {}).keys())
    if len(domains) != 17:
        raise PlatformError(f"expected 17 formal domains, found {len(domains)}")

    all_training_rows: list[dict[str, Any]] = []
    domain_freeze: dict[str, Any] = {}
    failures: dict[str, str] = {}
    for cid in domains:
        try:
            report_path = base.REPORT_ROOT / f"{cid}.json"
            report = load_json(report_path)
            seasons = _completed_outer_seasons_last_complete_only(report)
            if len(seasons) < 4:
                raise PlatformError(f"need at least four completed outer seasons for {cid}")
            chosen = seasons[-4:]
            rows_by_season = base._build_domain_rows(cid, chosen)
            for season in sorted(rows_by_season, key=_season_key):
                all_training_rows.extend(rows_by_season[season])
            last_complete = chosen[-1]
            fold = _fold_for_season(report, last_complete)
            params = fold.get("selected_parameters")
            if not isinstance(params, dict):
                raise PlatformError(f"missing selected parameters for {cid} {last_complete}")
            temperature = float(_target_season_temperature(cid, last_complete)[0])
            matches = sorted(read_processed_matches(cid), key=lambda match: (match.date, match.home_team, match.away_team))
            last_match_date = matches[-1].date.isoformat() if matches else None
            domain_freeze[cid] = {
                "training_seasons": chosen,
                "formal_parameter_source_season": last_complete,
                "formal_selected_parameters": params,
                "temperature": temperature,
                "report_sha256": _sha256(report_path),
                "historical_match_count_at_freeze": len(matches),
                "historical_last_match_datetime": last_match_date,
            }
        except Exception as exc:
            failures[cid] = f"{type(exc).__name__}: {exc}"

    if failures:
        payload = {
            "schema_version": "V6.1.0-pristine-forward-freeze-r1",
            "generated_at_utc": generated.isoformat(),
            "status": "FAIL_DATA_BUILD",
            "failures": failures,
            "governance": {"formal_weight_change": False, "runtime_probability_change": False},
        }
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    refit_models = base._fit_models(all_training_rows, l2)
    payload = {
        "schema_version": "V6.1.0-pristine-forward-freeze-r1",
        "generated_at_utc": generated.isoformat(),
        "status": "PASS",
        "freeze_timestamp_utc": generated.isoformat(),
        "forward_start_date_utc": forward_start,
        "strict_forward_rule": "Only matches with match date on or after forward_start_date_utc are eligible; no historical backfill.",
        "competition_count": len(domains),
        "training_row_count": len(all_training_rows),
        "frozen_probability_model": {
            "architecture": "V6.0.1 draw-vs-decisive plus home-vs-away conditional experts",
            "l2": l2,
            "pool_weight": pool_weight,
            "draw_ratio": draw_ratio,
            "models": _json_safe_model(refit_models),
        },
        "frozen_arms": {
            "arm_a_v605_asymmetric": {
                "home_enabled": True,
                "away_enabled": True,
                "home_confidence_threshold": home_threshold,
                "away_confidence_threshold": away_threshold,
                "agreement_required": True,
                "draws_excluded": True,
            },
            "arm_b_home_only": {
                "home_enabled": True,
                "away_enabled": False,
                "home_confidence_threshold": home_threshold,
                "agreement_required": True,
                "draws_excluded": True,
            },
            "benchmark_v601_pooled_top5": {
                "home_enabled": True,
                "away_enabled": True,
                "pooled_confidence_threshold": pooled_top5,
                "agreement_required": True,
                "draws_excluded": True,
            },
        },
        "forward_evaluation_gates": {
            "minimum_completed_forward_matches": 1500,
            "minimum_arm_a_selections": 100,
            "minimum_arm_b_selections": 60,
            "minimum_benchmark_selections": 80,
            "minimum_competitions_represented": 8,
            "arm_a_primary": {
                "accuracy_nonworse_than_benchmark": True,
                "wilson90_lower_minimum": 0.78,
                "paired_bootstrap90_lower_minimum": 0.0,
            },
            "arm_b_secondary": {
                "wilson90_lower_minimum": 0.80,
                "paired_bootstrap90_lower_minimum": 0.0,
            },
            "no_early_promotion": True,
        },
        "domain_freeze": domain_freeze,
        "source_integrity": {
            "v600_code_sha256": _sha256(VALIDATION / "v6_direct_outcome_mvp_v600.py"),
            "v601_code_sha256": _sha256(VALIDATION / "v6_direct_outcome_draw_boundary_v601.py"),
            "v604_code_sha256": _sha256(VALIDATION / "v6_selective_direction_lcb_v604.py"),
            "v605_code_sha256": _sha256(VALIDATION / "v6_selective_asymmetric_lcb_v605.py"),
            "v601_receipt_sha256": _sha256(V601_STATUS),
            "v604_receipt_sha256": _sha256(V604_STATUS),
            "v605_receipt_sha256": _sha256(V605_STATUS),
        },
        "governance": {
            "research_forward_test_only": True,
            "formal_weight_change": False,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "threshold_changes_after_freeze_forbidden": True,
            "model_refit_after_freeze_forbidden": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
