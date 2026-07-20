#!/usr/bin/env python3
"""Runtime verifier for hash-bound next-season formal hyperparameter rollforward.

This module is intentionally outside the frozen formal engine file so its use does
not change the validated engine SHA. It may supply hyperparameters only when the
formal model lacks a target-season point-in-time entry. The unchanged engine then
still enforces same-season history and all minimum sample gates.
"""
from __future__ import annotations

from typing import Any

from football_v460_engine import ENGINE_PATH
from platform_core import ROOT, PlatformError, load_json, sha256_file, sha256_json

CONFIG_PATH = ROOT / "config" / "formal_core_v460.json"
ROLLFORWARD_PATH = ROOT / "manifests" / "formal_next_season_parameter_rollforward_v470_status.json"


def _report_for(competition_id: str, target_season: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not ROLLFORWARD_PATH.exists():
        raise PlatformError("next-season formal parameter rollforward receipt missing")
    receipt = load_json(ROLLFORWARD_PATH)
    if receipt.get("status") not in {"PASS", "PARTIAL"}:
        raise PlatformError(f"next-season parameter rollforward receipt is not operational: {receipt.get('status')}")
    report = (receipt.get("reports") or {}).get(competition_id)
    if not isinstance(report, dict) or report.get("status") != "NEXT_SEASON_PARAMETERS_FROZEN":
        raise PlatformError("competition has no valid next-season parameter rollforward")
    if str(report.get("target_season") or "") != target_season:
        raise PlatformError("next-season parameter rollforward target season mismatch")
    model_path = ROOT / str(report.get("source_model_path") or "")
    validation_path = ROOT / str(report.get("source_validation_report_path") or "")
    if not model_path.exists() or not validation_path.exists():
        raise PlatformError("next-season parameter rollforward source artifact missing")
    model = load_json(model_path)
    validation = load_json(validation_path)
    checks = {
        "engine_sha_match": report.get("engine_sha256") == sha256_file(ENGINE_PATH),
        "config_sha_match": report.get("config_sha256") == sha256_file(CONFIG_PATH),
        "source_model_sha_match": report.get("source_model_sha256") == sha256_file(model_path),
        "source_validation_report_sha_match": report.get("source_validation_report_sha256") == sha256_file(validation_path),
        "model_validation_binding_match": model.get("validation_report_sha256") == sha256_json(validation),
        "parameter_sha_match": report.get("parameter_sha256") == sha256_json(report.get("selected_parameters") or {}),
        "model_selected_parameters_match": model.get("selected_parameters") == report.get("selected_parameters"),
        "source_season_match": str(model.get("live_target_season") or "") == str(report.get("source_season") or ""),
        "team_strength_rollforward_false": report.get("team_strength_rollforward") is False,
    }
    if not all(checks.values()):
        raise PlatformError(f"next-season parameter rollforward hash/invariant failure: {checks}")
    return report, model, checks


def select_rollforward_parameters(artifact: dict[str, Any], target_season: str) -> dict[str, Any]:
    competition_id = str(artifact.get("competition_id") or "")
    if not competition_id:
        raise PlatformError("formal model artifact missing competition_id")
    report, model, _ = _report_for(competition_id, target_season)
    if artifact != model:
        # Object equality makes sure the engine-loaded artifact is exactly the source
        # whose file hash was bound by the receipt, not a stale in-memory variant.
        raise PlatformError("engine-loaded model artifact differs from rollforward source model")
    params = report.get("selected_parameters")
    if not isinstance(params, dict) or not params:
        raise PlatformError("next-season rollforward parameter set missing")
    return dict(params)


def audit_rollforward_parameters(competition_id: str, target_season: str) -> dict[str, Any]:
    try:
        report, _, checks = _report_for(competition_id, target_season)
    except PlatformError as exc:
        return {
            "status": "不可用",
            "competition_id": competition_id,
            "target_season": target_season,
            "probability_mutation": False,
            "team_strength_rollforward": False,
            "reason": str(exc),
        }
    return {
        "status": "通过",
        "competition_id": competition_id,
        "source_season": report["source_season"],
        "target_season": target_season,
        "parameter_sha256": report["parameter_sha256"],
        "checks": checks,
        "probability_mutation": False,
        "team_strength_rollforward": False,
        "policy": "Hyperparameters only; unchanged formal engine still enforces same-season current-strength sample gates.",
    }
