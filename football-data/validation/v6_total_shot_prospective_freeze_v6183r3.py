#!/usr/bin/env python3
"""V6.18.3 r3 strict-daily-PIT prospective freeze.

Supersedes invalid V6.18.3 r1/r2. The candidate design is unchanged except that all
training rows are rebuilt by the PASS V6.18.1c strict calendar-day PIT builder.

Frozen 2026/27 research arms:
A formal P(T)
B calibration = log formal P(T)
C competition = B + competition one-hot
D shot = C + strictly lagged same-season shots/SOT/corners

All residual arms use C=0.01, selected before this freeze. All eight domains remain in
scope; no domain is selected from 2025/26 diagnostics. New-season shot state resets at
the season boundary and both teams need >=3 strictly prior same-season matches. Formal
same-season runtime gates remain independent and cannot be bypassed.
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
import v6_total_shot_residual_v6181a as datefix
import v6_total_shot_increment_control_v6181b as control
import v6_strict_daily_pit_rows_v6181c as strict
from platform_core import PlatformError, atomic_write_json, load_json

STRICT_RECEIPT = ROOT / "manifests" / "v6_strict_daily_pit_total_v6181c_status.json"
R2_RECEIPT = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183r2_status.json"
FORMAL_READY = ROOT / "manifests" / "formal_next_season_runtime_readiness_v470_status.json"
OUT = ROOT / "manifests" / "v6_total_shot_prospective_freeze_v6183r3_status.json"
FROZEN_C = 0.01
TRAIN_SEASONS = {"2022/23", "2023/24", "2024/25", "2025/26"}
TARGET_SEASON = "2026/27"
MIN_PRIOR_TEAM_MATCHES = 3
MIN_SETTLED_PER_DOMAIN = 150


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def serialize(model, mode):
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
        "solver": "lbfgs"
    }


def main() -> int:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    sr = load_json(STRICT_RECEIPT)
    if sr.get("status") != "PASS" or sr.get("schema_version") != "V6.18.1c-strict-daily-pit-total-r1":
        raise PlatformError("PASS V6.18.1c strict-PIT receipt required")
    r2 = load_json(R2_RECEIPT) if R2_RECEIPT.exists() else {}
    if r2 and r2.get("status") != "FAIL_CLOSED_PIT_LEAK_SUPERSEDED":
        raise PlatformError("V6.18.3 r2 must be fail-closed before r3")

    readiness = load_json(FORMAL_READY) if FORMAL_READY.exists() else {}
    reports = readiness.get("reports") if isinstance(readiness.get("reports"), dict) else {}
    # Fail closed: r3 may only be frozen before any registered 2026/27 domain becomes sample-ready.
    if any(bool(v.get("sample_ready")) for v in reports.values() if isinstance(v, dict)):
        raise PlatformError("target-season formal sample already ready; pristine r3 freeze no longer allowed")

    raw, _ = base.raw_stat_matches()
    lookup, _ = datefix.lagged_shot_lookup_fixed(raw)
    rows, strict_meta = strict.strict_total_rows(lookup)
    train = [r for r in rows if r["season"] in TRAIN_SEASONS]
    if len(train) != int((sr.get("rows") or {}).get("all") or -1):
        raise PlatformError(f"strict row-count drift receipt={(sr.get('rows') or {}).get('all')} now={len(train)}")
    shot_names, comps = base.feature_names(train)
    if sorted(comps) != sorted(base.COMPS):
        raise PlatformError(f"domain mismatch {comps}")

    b = control.fit(train, "calibration", FROZEN_C, shot_names, comps)
    c = control.fit(train, "competition", FROZEN_C, shot_names, comps)
    d = control.fit(train, "shot", FROZEN_C, shot_names, comps)

    domain_freeze = {}
    for cid in sorted(comps):
        rr = reports.get(cid, {}) if isinstance(reports, dict) else {}
        domain_freeze[cid] = {
            "included": True,
            "target_season": TARGET_SEASON,
            "routing_ready_at_freeze": bool(rr.get("routing_ready")),
            "sample_ready_at_freeze": bool(rr.get("sample_ready")),
            "formal_status_at_freeze": rr.get("status") or "FORMAL_2026_27_ROUTE_NOT_YET_REGISTERED",
            "eligibility_rule": "formal same-season gate PASS AND both teams have >=3 strictly prior same-season shot-history matches"
        }

    payload = {
        "schema_version": "V6.18.3-prospective-shot-total-freeze-r3-strict-daily-pit",
        "generated_at_utc": now.isoformat(),
        "status": "PASS",
        "formal_current_version": "V5.0.1",
        "supersedes": [
            "V6.18.3-prospective-shot-total-freeze-r1",
            "V6.18.3-prospective-shot-total-freeze-r2-invalidated"
        ],
        "freeze_timestamp_utc": now.isoformat(),
        "forward_start_date_utc": (now.date() + timedelta(days=1)).isoformat(),
        "target_season": TARGET_SEASON,
        "classification": "PRISTINE_PROSPECTIVE_RESEARCH_FREEZE_STRICT_DAILY_PIT_NO_BACKFILL",
        "strict_forward_rule": "Only fixtures on/after forward_start, frozen before kickoff, with all formal and same-season shot warmup gates PASS are eligible; historical backfill is forbidden.",
        "frozen_domains": sorted(comps),
        "training_seasons": sorted(TRAIN_SEASONS),
        "training_rows": len(train),
        "strict_daily_pit": True,
        "frozen_C": FROZEN_C,
        "frozen_shot_features": shot_names,
        "frozen_models": {
            "arm_b_calibration": serialize(b, "formal_logP_only"),
            "arm_c_competition": serialize(c, "formal_logP_plus_competition_onehot"),
            "arm_d_shot": serialize(d, "formal_logP_plus_competition_onehot_plus_lagged_shots_SOT_corners")
        },
        "same_season_gates": {
            "minimum_prior_matches_per_team_for_shot": MIN_PRIOR_TEAM_MATCHES,
            "shot_state_resets_at_season_boundary": True,
            "prior_season_shot_state_injection": False,
            "current_match_stats_as_features": False,
            "same_date_result_or_event_leakage": False,
            "formal_same_date_history_frozen": True
        },
        "evaluation_contract": {
            "primary_increment": "arm_d_shot minus arm_c_competition",
            "secondary_increment": "arm_c_competition minus arm_b_calibration",
            "formal_comparison": "arm_d_shot minus arm_a_formal",
            "metrics": ["direct_total_0_7plus_RPS", "direct_total_0_7plus_LogLoss", "exact_total_Top1"],
            "per_domain_minimum_settled_before_review": MIN_SETTLED_PER_DOMAIN,
            "required_primary_directions": {"RPS": "<0", "LogLoss": "<0", "Top1": ">=0"},
            "paired_block_bootstrap_required": True,
            "no_early_promotion": True,
            "no_aggregate_auto_promotion": True
        },
        "domain_freeze": domain_freeze,
        "strict_meta_at_freeze": strict_meta,
        "source_integrity": {
            "strict_receipt_sha256": sha256(STRICT_RECEIPT),
            "strict_builder_sha256": sha256(V / "v6_strict_daily_pit_rows_v6181c.py"),
            "date_key_fix_sha256": sha256(V / "v6_total_shot_residual_v6181a.py"),
            "control_code_sha256": sha256(V / "v6_total_shot_increment_control_v6181b.py"),
            "formal_readiness_sha256": sha256(FORMAL_READY) if FORMAL_READY.exists() else None
        },
        "governance": {
            "research_only": True,
            "formal_weight": 0,
            "runtime_probability_change": False,
            "current_rule_change": False,
            "automatic_promotion": False,
            "model_refit_after_freeze_forbidden": True,
            "C_change_after_freeze_forbidden": True,
            "feature_change_after_freeze_forbidden": True,
            "domain_selection_from_2025_26_forbidden": True,
            "no_backfill": True
        }
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({
        "status": payload["status"],
        "forward_start_date_utc": payload["forward_start_date_utc"],
        "training_rows": len(train),
        "domains": domain_freeze
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
