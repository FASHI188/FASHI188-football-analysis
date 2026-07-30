#!/usr/bin/env python3
"""Build hash-bound next-season formal hyperparameter rollforward receipts.

This rolls only validated hyperparameters into a future target season. Team strength
never rolls forward: the unchanged formal engine still requires same-season history
and its hard competition/team sample gates before any probability can be produced.
Repository-text identity uses one shared LF-normalized hash implementation for the
engine, config, source model, and source validation report so Windows CRLF checkout
conversion cannot create a false cross-platform mismatch.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from football_v460_engine import ENGINE_PATH
from platform_core import ROOT, load_json, sha256_json
from repository_text_hash import repository_text_sha256

CONFIG_PATH = ROOT / "config" / "formal_core_v460.json"
OUT = ROOT / "manifests" / "formal_next_season_parameter_rollforward_v470_status.json"
TARGETS = {
    "ESP_LaLiga": {"source_season": "2025/26", "target_season": "2026/27"},
    "NED_Eredivisie": {"source_season": "2025/26", "target_season": "2026/27"},
}


def build_one(competition_id: str, spec: dict[str, str]) -> dict[str, Any]:
    model_path = ROOT / "models" / "formal_core_v460" / competition_id / "model.json"
    report_path = ROOT / "validation" / "reports" / "formal_core_v460" / f"{competition_id}.json"
    model = load_json(model_path)
    report = load_json(report_path)
    params = model.get("selected_parameters")
    if not isinstance(params, dict) or not params:
        raise RuntimeError("validated selected_parameters missing")
    if model.get("operational_status") != "NON_A_FORMAL_CORE_AVAILABLE":
        raise RuntimeError(f"formal core is not operational: {model.get('operational_status')}")

    engine_sha = repository_text_sha256(ENGINE_PATH)
    if model.get("engine_sha256") != engine_sha:
        raise RuntimeError("model engine hash mismatch after canonical repository-text verification")
    if model.get("validation_report_sha256") != sha256_json(report):
        raise RuntimeError("model validation report hash mismatch")
    if str(model.get("live_target_season") or "") != spec["source_season"]:
        raise RuntimeError("source live_target_season does not match completed source season")

    return {
        "competition_id": competition_id,
        "status": "NEXT_SEASON_PARAMETERS_FROZEN",
        "source_season": spec["source_season"],
        "target_season": spec["target_season"],
        "selected_parameters": params,
        "parameter_sha256": sha256_json(params),
        "engine_sha256": engine_sha,
        "config_sha256": repository_text_sha256(CONFIG_PATH),
        "source_model_path": str(model_path.relative_to(ROOT)),
        "source_model_sha256": repository_text_sha256(model_path),
        "source_validation_report_path": str(report_path.relative_to(ROOT)),
        "source_validation_report_sha256": repository_text_sha256(report_path),
        "repository_text_hash_normalization": "CRLF_AND_CR_TO_LF_BEFORE_SHA256",
        "formal_weight_change": False,
        "team_strength_rollforward": False,
        "probability_change": False,
        "policy": (
            "Hyperparameters selected after the completed source season may be frozen for the next target season. "
            "Target-team strength remains same-season-only and all existing minimum sample gates remain mandatory."
        ),
    }


def main() -> int:
    reports = {}
    failures = []
    for cid, spec in TARGETS.items():
        try:
            reports[cid] = build_one(cid, spec)
        except Exception as exc:
            failures.append(cid)
            reports[cid] = {
                "competition_id": cid,
                "status": "FAILED",
                "reason": str(exc),
                "formal_weight_change": False,
                "team_strength_rollforward": False,
                "probability_change": False,
            }
    out = {
        "schema_version": "V4.7.0-formal-next-season-parameter-rollforward-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not failures else "PARTIAL",
        "failed_competitions": failures,
        "formal_weight_change": False,
        "team_strength_rollforward": False,
        "probability_change": False,
        "reports": reports,
        "policy": (
            "Fail closed on source-model, validation-report, engine, config, parameter, or season mismatches. "
            "All repository-text file bindings use one shared cross-platform LF-canonical SHA256 implementation."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
