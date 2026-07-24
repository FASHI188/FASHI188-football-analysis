#!/usr/bin/env python3
"""V6.18.3 prospective freeze for the shot-informed direct total challenger.

This freeze is intentionally created BEFORE using V6.18.2 domain diagnostics for
selection. All eight V6.18.1 shot-data competitions remain in scope. No competition
may be dropped or promoted because of the post-hoc 2025/26 domain breakdown.

Frozen arms for 2026/27:
A formal P(T): formal runtime only, unchanged.
B calibration: multinomial residual on log formal P(T).
C shot: B plus the complete V6.18.1 lagged shots/SOT/corners feature vector.

Hyperparameter C=0.01 was selected on the 2024/25 validation fold in V6.18.1.
Models B/C are refit once on all completed 2022/23..2025/26 rows and serialized as
plain numeric parameters. They may not be refit after this freeze.

New-season governance:
- shot rolling histories reset at season boundary;
- each target team needs >=3 same-season prior matches before shot features exist;
- the formal 2026/27 baseline must independently pass its own same-season sample gate;
- no prior-season team-strength or current-match event stats may bypass those gates;
- only matches strictly after the freeze and frozen before kickoff are eligible.

Research only, formal_weight=0, no CURRENT/runtime probability change.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V = ROOT / "validation"
E = ROOT / "engine"
for p in (V, E):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import v6_total_shot_residual_v6181 as base
import v6_total_shot_residual_v6181a as fix
from platform_core import PlatformError, atomic_write_json, load_json

V6181 = ROOT / "manifests" / "v6_total_shot_residual_v6181_status.json"
FORMAL_READY = ROOT / "manifests" / "formal_next_season_runtime_readiness_v470_status.json"
OUT = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183_status.json"
FROZEN_C = 0.01
TRAIN_SEASONS = {"2022/23", "2023/24", "2024/25", "2025/26"}
TARGET_SEASON = "2026/27"
MIN_SAME_SEASON_TEAM_MATCHES_FOR_SHOT = 3
MIN_FORWARD_SETTLED_PER_DOMAIN_FOR_REVIEW = 150


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def serialize_pipeline(model):
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]
    return {
        "classes": [int(x) for x in clf.classes_],
        "scaler_mean": [float(x) for x in scaler.mean_],
        "scaler_scale": [float(x) for x in scaler.scale_],
        "coef": [[float(x) for x in row] for row in clf.coef_],
        "intercept": [float(x) for x in clf.intercept_],
        "n_features_in": int(clf.n_features_in_),
        "regularization_C": FROZEN_C,
        "solver": "lbfgs",
        "model_family": "multinomial_logistic_residual",
    }


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    forward_start = (now.date() + timedelta(days=1)).isoformat()
    receipt = load_json(V6181)
    if receipt.get("status") != "PASS" or receipt.get("schema_version") != "V6.18.1-shot-informed-direct-total-r2":
        raise PlatformError("V6.18.1 r2 PASS receipt required")
    second_shot = ((receipt.get("results") or {}).get("shot") or [None, None])[1]
    second_cal = ((receipt.get("results") or {}).get("calibration") or [None, None])[1]
    if not second_shot or not second_cal or second_shot.get("selected_C") != FROZEN_C or second_cal.get("selected_C") != FROZEN_C:
        raise PlatformError("V6.18.1 second-fold frozen C=0.01 selection required")

    raw, _ = base.raw_stat_matches()
    lookup, _ = fix.lagged_shot_lookup_fixed(raw)
    rows, _ = base.formal_rows(lookup)
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    if len(train) < 8000:
        raise PlatformError(f"insufficient completed pre-freeze rows: {len(train)}")
    shot_names, comps = base.feature_names(train)
    expected = sorted(base.COMPS)
    if sorted(comps) != expected:
        raise PlatformError(f"domain mismatch freeze={comps} expected={expected}")

    cal_model = base.fit_model(train, "calibration", FROZEN_C, shot_names, comps)
    shot_model = base.fit_model(train, "shot", FROZEN_C, shot_names, comps)

    formal = load_json(FORMAL_READY) if FORMAL_READY.exists() else {}
    reports = formal.get("reports") if isinstance(formal.get("reports"), dict) else {}
    domain_freeze = {}
    for cid in expected:
        rr = reports.get(cid, {})
        domain_freeze[cid] = {
            "included": True,
            "target_season": TARGET_SEASON,
            "routing_ready_at_freeze": bool(rr.get("routing_ready")),
            "sample_ready_at_freeze": bool(rr.get("sample_ready")),
            "formal_status_at_freeze": rr.get("status") or "FORMAL_2026_27_ROUTE_NOT_YET_REGISTERED",
            "eligibility_rule": "formal target-season sample gate PASS AND both teams have >=3 strictly prior same-season shot-history matches",
        }

    payload = {
        "schema_version": "V6.18.3-prospective-shot-total-freeze-r1",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "freeze_timestamp_utc": now.isoformat(),
        "forward_start_date_utc": forward_start,
        "target_season": TARGET_SEASON,
        "classification": "PRISTINE_PROSPECTIVE_RESEARCH_FREEZE_NO_BACKFILL",
        "strict_forward_rule": "Only fixtures strictly after the freeze, predicted before kickoff, and satisfying all same-season gates are eligible. Historical backfill is forbidden.",
        "frozen_domains": expected,
        "training_seasons": sorted(TRAIN_SEASONS),
        "training_rows": len(train),
        "frozen_regularization_C": FROZEN_C,
        "frozen_feature_names": shot_names,
        "same_season_shot_warmup": {
            "minimum_prior_matches_per_team": MIN_SAME_SEASON_TEAM_MATCHES_FOR_SHOT,
            "season_boundary_reset": True,
            "prior_season_shot_state_injection": False,
            "current_match_stats_as_features": False,
            "same_date_completed_match_leakage": False,
        },
        "frozen_models": {
            "arm_b_calibration": serialize_pipeline(cal_model),
            "arm_c_shot": serialize_pipeline(shot_model),
        },
        "domain_freeze": domain_freeze,
        "evaluation_contract": {
            "per_domain_minimum_settled_for_review": MIN_FORWARD_SETTLED_PER_DOMAIN_FOR_REVIEW,
            "primary_comparison": "arm_c_shot vs arm_b_calibration",
            "secondary_comparison": "arm_c_shot vs arm_a_formal",
            "metrics": ["direct_total_0_7plus_RPS", "direct_total_0_7plus_LogLoss", "exact_total_Top1"],
            "primary_direction_required": {
                "RPS_difference": "< 0",
                "LogLoss_difference": "< 0",
                "exact_total_Top1_difference": ">= 0",
            },
            "uncertainty": "paired chronological/block bootstrap required before any promotion review",
            "no_early_promotion": True,
            "no_aggregate_result_may_auto_promote_a_domain": True,
        },
        "source_integrity": {
            "v6181_receipt_sha256": sha256(V6181),
            "v6181_code_sha256": sha256(V / "v6_total_shot_residual_v6181.py"),
            "v6181_fix_code_sha256": sha256(V / "v6_total_shot_residual_v6181a.py"),
            "formal_readiness_sha256": sha256(FORMAL_READY) if FORMAL_READY.exists() else None,
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "model_refit_after_freeze_forbidden": True,
            "regularization_change_after_freeze_forbidden": True,
            "feature_selection_after_freeze_forbidden": True,
            "domain_selection_from_v6182_forbidden": True,
            "formal_same_season_gate_cannot_be_bypassed": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "forward_start_date_utc": forward_start,
        "training_rows": len(train),
        "domains": domain_freeze,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
