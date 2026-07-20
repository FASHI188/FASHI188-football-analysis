#!/usr/bin/env python3
"""Audit next-season formal base-core runtime readiness separately from challengers.

The receipt distinguishes routing readiness (hyperparameters + OOF calibration) from
same-season data sufficiency. A preseason zero-sample target remains blocked even
when all next-season routing artifacts are valid.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from platform_core import ROOT, sha256_file

OUT = ROOT / "manifests" / "formal_next_season_runtime_readiness_v470_status.json"
PARAM = ROOT / "manifests" / "formal_next_season_parameter_rollforward_v470_status.json"
PARAM_SMOKE = ROOT / "manifests" / "formal_next_season_parameter_runtime_v470_smoke.json"
OOF = ROOT / "manifests" / "oof_next_season_rollforward_v470_status.json"
OOF_SMOKE = ROOT / "manifests" / "oof_next_season_runtime_v470_smoke.json"
REGISTRY = ROOT / "config" / "platform_registry.json"
CONFIG = ROOT / "config" / "formal_core_v460.json"
ENGINE = ROOT / "engine" / "football_v460_engine.py"
RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
TARGETS = ("ESP_LaLiga", "NED_Eredivisie")
TARGET_SEASON = "2026/27"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    param = load(PARAM)
    param_smoke = load(PARAM_SMOKE)
    oof = load(OOF)
    oof_smoke = load(OOF_SMOKE)
    registry = load(REGISTRY)
    config = load(CONFIG)
    reg = {item["competition_id"]: item for item in registry["competitions"]}
    reports = {}
    for cid in TARGETS:
        pr = (param.get("reports") or {}).get(cid) or {}
        orr = (oof.get("reports") or {}).get(cid) or {}
        pchecks = param_smoke.get("checks") or {}
        ochecks = oof_smoke.get("checks") or {}
        current_status = str((reg.get(cid) or {}).get("current_season_status") or "")
        checks = {
            "parameter_rollforward_valid": pr.get("status") == "NEXT_SEASON_PARAMETERS_FROZEN" and pr.get("target_season") == TARGET_SEASON,
            "parameter_runtime_smoke_passed": bool(pchecks.get(f"{cid}_parameter_bridge_passes")),
            "team_strength_rollforward_disabled": pr.get("team_strength_rollforward") is False,
            "zero_sample_gate_preserved": bool(pchecks.get(f"{cid}_zero_sample_still_rejected")),
            "oof_rollforward_valid": orr.get("status") == "NEXT_SEASON_OOF_CALIBRATOR_FROZEN" and orr.get("target_season") == TARGET_SEASON,
            "oof_guardrail_passed": orr.get("guardrail_passed") is True,
            "oof_runtime_smoke_passed": bool(ochecks.get(f"{cid}_unchanged_calibration_function_passes")),
            "engine_sha_matches_parameter_receipt": pr.get("engine_sha256") == sha256_file(ENGINE),
            "engine_sha_matches_oof_receipt": orr.get("engine_sha256") == sha256_file(ENGINE),
            "current_season_sample_started": "preseason_no_completed_matches" not in current_status,
        }
        routing_ready = all(value for key, value in checks.items() if key != "current_season_sample_started")
        sample_ready = checks["current_season_sample_started"]
        reports[cid] = {
            "competition_id": cid,
            "target_season": TARGET_SEASON,
            "status": "BASE_CORE_LIVE_READY" if routing_ready and sample_ready else "BASE_CORE_SAMPLE_BLOCKED" if routing_ready else "BASE_CORE_ROUTING_BLOCKED",
            "routing_ready": routing_ready,
            "sample_ready": sample_ready,
            "current_season_status": current_status,
            "sample_thresholds": {
                "minimum_competition_history_matches": int(config["minimum_competition_history_matches"]),
                "minimum_team_raw_matches_per_relevant_venue": int(config["minimum_team_raw_matches"]),
            },
            "checks": checks,
            "blockers": [key for key, value in checks.items() if not value],
            "probability_change": False,
            "policy": "Valid next-season routing does not waive same-season data sufficiency. No prior-season team strength is injected.",
        }
    out = {
        "schema_version": "V4.7.0-formal-next-season-runtime-readiness-r1",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS",
        "engine_sha256": sha256_file(ENGINE),
        "actionable_runner_sha256": sha256_file(RUNNER),
        "routing_ready_competitions": [cid for cid, report in reports.items() if report["routing_ready"]],
        "live_ready_competitions": [cid for cid, report in reports.items() if report["status"] == "BASE_CORE_LIVE_READY"],
        "sample_blocked_competitions": [cid for cid, report in reports.items() if report["status"] == "BASE_CORE_SAMPLE_BLOCKED"],
        "probability_change": False,
        "reports": reports,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
