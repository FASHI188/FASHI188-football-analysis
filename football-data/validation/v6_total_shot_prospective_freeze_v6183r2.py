#!/usr/bin/env python3
"""V6.18.3 r2 prospective freeze with explicit competition-effect control.

Supersedes V6.18.3 r1 BEFORE any eligible 2026/27 prediction. The formal next-season
readiness receipt still reports zero same-season completed sample, so no target-season
prediction can have been eligible under r1.

Frozen forward arms:
A formal P(T)
B calibration: log formal P(T)
C competition: B + competition one-hot
D shot: C + strictly lagged same-season shots/SOT/corners

Primary causal/incremental comparison is D-C. B remains useful to quantify competition
fixed-effect gain, and A is the formal benchmark. All residual arms use the same frozen
C=0.01 and are refit exactly once on completed 2022/23..2025/26 rows.
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
import v6_total_shot_increment_control_v6181b as control
from platform_core import PlatformError, atomic_write_json, load_json

R1 = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183_status.json"
V6181 = ROOT / "manifests" / "v6_total_shot_residual_v6181_status.json"
FORMAL_READY = ROOT / "manifests" / "formal_next_season_runtime_readiness_v470_status.json"
OUT = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183r2_status.json"
FROZEN_C = 0.01
TRAIN_SEASONS = {"2022/23", "2023/24", "2024/25", "2025/26"}
TARGET_SEASON = "2026/27"
MIN_TEAM_PRIOR = 3


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def serialize_pipeline(model, mode):
    scaler = model.named_steps["standardscaler"]
    clf = model.named_steps["logisticregression"]
    return {
        "mode": mode,
        "classes": [int(x) for x in clf.classes_],
        "scaler_mean": [float(x) for x in scaler.mean_],
        "scaler_scale": [float(x) for x in scaler.scale_],
        "coef": [[float(x) for x in row] for row in clf.coef_],
        "intercept": [float(x) for x in clf.intercept_],
        "n_features_in": int(clf.n_features_in_),
        "regularization_C": FROZEN_C,
        "solver": "lbfgs",
    }


def main():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    r1 = load_json(R1)
    receipt = load_json(V6181)
    readiness = load_json(FORMAL_READY)
    if r1.get("status") != "PASS" or r1.get("target_season") != TARGET_SEASON:
        raise PlatformError("V6.18.3 r1 PASS freeze required")
    if receipt.get("status") != "PASS":
        raise PlatformError("V6.18.1 PASS required")
    reports = readiness.get("reports") or {}
    if any(bool(v.get("sample_ready")) for v in reports.values() if isinstance(v, dict)):
        raise PlatformError("cannot supersede after a 2026/27 formal domain becomes sample-ready")
    if any((v.get("current_season_status") or "") != "2026_27_preseason_no_completed_matches" for v in reports.values() if isinstance(v, dict)):
        raise PlatformError("cannot supersede after 2026/27 same-season completed sample starts")

    raw, _ = base.raw_stat_matches()
    lookup, _ = fix.lagged_shot_lookup_fixed(raw)
    rows, _ = base.formal_rows(lookup)
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    shot_names, comps = base.feature_names(train)
    if len(train) != int(r1.get("training_rows") or -1):
        raise PlatformError(f"training-row drift r1={r1.get('training_rows')} now={len(train)}")
    if sorted(comps) != sorted(r1.get("frozen_domains") or []):
        raise PlatformError("domain drift from r1")
    if shot_names != list(r1.get("frozen_feature_names") or []):
        raise PlatformError("feature drift from r1")

    # Use the controlled feature construction for all residual arms.
    b = control.fit(train, "calibration", FROZEN_C, shot_names, comps)
    c = control.fit(train, "competition", FROZEN_C, shot_names, comps)
    d = control.fit(train, "shot", FROZEN_C, shot_names, comps)

    next_day = (now.date() + timedelta(days=1)).isoformat()
    r1_start = str(r1["forward_start_date_utc"])
    forward_start = max(r1_start, next_day)
    domain_freeze = {}
    for cid in sorted(comps):
        rr = reports.get(cid, {}) if isinstance(reports, dict) else {}
        domain_freeze[cid] = {
            "included": True,
            "target_season": TARGET_SEASON,
            "routing_ready_at_r2_freeze": bool(rr.get("routing_ready")),
            "sample_ready_at_r2_freeze": bool(rr.get("sample_ready")),
            "formal_status_at_r2_freeze": rr.get("status") or "FORMAL_2026_27_ROUTE_NOT_YET_REGISTERED",
            "eligibility_rule": "formal 2026/27 same-season sample gate PASS AND both teams have >=3 strictly prior same-season shot-history matches",
        }

    payload = {
        "schema_version": "V6.18.3-prospective-shot-total-freeze-r2",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "supersedes": "V6.18.3-prospective-shot-total-freeze-r1",
        "supersession_reason": "r1 lacked competition-only control; r2 created before any target-season sample was eligible",
        "r1_had_eligible_predictions": False,
        "freeze_timestamp_utc": now.isoformat(),
        "forward_start_date_utc": forward_start,
        "target_season": TARGET_SEASON,
        "classification": "PRISTINE_PROSPECTIVE_RESEARCH_FREEZE_NO_BACKFILL",
        "strict_forward_rule": "Only fixtures on/after forward_start, frozen pre-kickoff, with formal same-season gate PASS and shot same-season warmup PASS are eligible. No historical backfill.",
        "frozen_domains": sorted(comps),
        "training_seasons": sorted(TRAIN_SEASONS),
        "training_rows": len(train),
        "frozen_C": FROZEN_C,
        "frozen_shot_features": shot_names,
        "frozen_models": {
            "arm_b_calibration": serialize_pipeline(b, "formal_logP_only"),
            "arm_c_competition": serialize_pipeline(c, "formal_logP_plus_competition_onehot"),
            "arm_d_shot": serialize_pipeline(d, "formal_logP_plus_competition_onehot_plus_lagged_shots_SOT_corners"),
        },
        "same_season_gates": {
            "minimum_prior_matches_per_team_for_shot": MIN_TEAM_PRIOR,
            "shot_state_resets_at_season_boundary": True,
            "prior_season_shot_state_injection": False,
            "prior_season_team_strength_bypass": False,
            "current_match_stats_as_features": False,
            "same_date_outcome_or_event_leakage": False,
        },
        "evaluation_contract": {
            "primary_increment": "arm_d_shot minus arm_c_competition",
            "secondary_increment": "arm_c_competition minus arm_b_calibration",
            "formal_comparison": "arm_d_shot minus arm_a_formal",
            "metrics": ["direct_total_0_7plus_RPS", "direct_total_0_7plus_LogLoss", "exact_total_Top1"],
            "per_domain_minimum_settled_before_review": 150,
            "required_primary_directions": {"RPS": "<0", "LogLoss": "<0", "Top1": ">=0"},
            "paired_block_bootstrap_required": True,
            "no_early_promotion": True,
            "no_aggregate_auto_promotion": True,
        },
        "domain_freeze": domain_freeze,
        "source_integrity": {
            "r1_freeze_sha256": sha256(R1),
            "v6181_receipt_sha256": sha256(V6181),
            "v6181_code_sha256": sha256(V / "v6_total_shot_residual_v6181.py"),
            "date_key_fix_sha256": sha256(V / "v6_total_shot_residual_v6181a.py"),
            "confound_control_code_sha256": sha256(V / "v6_total_shot_increment_control_v6181b.py"),
            "formal_readiness_sha256": sha256(FORMAL_READY),
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "model_refit_after_r2_freeze_forbidden": True,
            "C_change_after_r2_freeze_forbidden": True,
            "feature_change_after_r2_freeze_forbidden": True,
            "domain_selection_from_2025_26_diagnostics_forbidden": True,
        },
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": "PASS", "forward_start_date_utc": forward_start, "training_rows": len(train), "domains": domain_freeze}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
