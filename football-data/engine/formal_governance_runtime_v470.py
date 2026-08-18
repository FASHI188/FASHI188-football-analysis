#!/usr/bin/env python3
"""Compatibility normalization for historically activated formal-rule receipts.

This adapter preserves the legacy output contract expected by the versioned V4/V5
runtime chain, including the historically activated rule-version label. It does
NOT discover, mirror, or certify the project's current formal CURRENT. Current
formal-rule authority remains external to GitHub and must be verified from the
unique project-scoped CURRENT before a caller treats any compatibility label as
present-day scientific authority.

No probabilities, markets, prices, thresholds, or model weights are changed.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from platform_core import ROOT, load_json, sha256_file

V501_GOVERNANCE_STATUS = ROOT / "manifests" / "v501_upgrade_status.json"
V500_GOVERNANCE_STATUS = ROOT / "manifests" / "v500_upgrade_status.json"
V480_GOVERNANCE_STATUS = ROOT / "manifests" / "v480_upgrade_status.json"


def _historically_activated_governance_path() -> Path:
    """Select the newest legacy activation receipt for compatibility only."""
    for path in (
        V501_GOVERNANCE_STATUS,
        V500_GOVERNANCE_STATUS,
        V480_GOVERNANCE_STATUS,
    ):
        if not path.exists():
            continue
        candidate = load_json(path)
        if str(candidate.get("status") or "").startswith("FORMALLY_ACTIVATED"):
            return path
    return V501_GOVERNANCE_STATUS


def apply_formal_governance_runtime(calculation: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    previous_rule_version = output.get("rule_version")
    governance_path = _historically_activated_governance_path()
    if not governance_path.exists():
        output["formal_governance_audit"] = {
            "status": "不可用",
            "reason": "historical activation receipt missing",
            "probability_mutation": False,
            "current_project_rule_authority": False,
        }
        return output
    governance = load_json(governance_path)
    formal_rule_version = str(governance.get("formal_rule_version") or "").strip()
    governance_status = str(governance.get("status") or "").strip()
    if not formal_rule_version:
        output["formal_governance_audit"] = {
            "status": "失败",
            "reason": "formal_rule_version missing from historical activation receipt",
            "probability_mutation": False,
            "current_project_rule_authority": False,
        }
        return output
    if not governance_status.startswith("FORMALLY_ACTIVATED"):
        output["formal_governance_audit"] = {
            "status": "失败",
            "reason": f"historical governance receipt is not formally activated: {governance_status}",
            "probability_mutation": False,
            "current_project_rule_authority": False,
        }
        return output

    # Backward-compatible version label only. Do not interpret this assignment as
    # present-day CURRENT selection; that authority is deliberately absent here.
    output["implementation_rule_version"] = previous_rule_version
    output["rule_version"] = formal_rule_version
    output["rule_version_authority"] = "HISTORICAL_ACTIVATION_COMPATIBILITY_ONLY"
    output["formal_governance_audit"] = {
        "status": "通过",
        "formal_rule_version": formal_rule_version,
        "underlying_implementation_rule_version": previous_rule_version,
        "underlying_engine_version": output.get("engine_version"),
        "governance_status": governance_status,
        "governance_manifest_path": str(governance_path.relative_to(ROOT)),
        "governance_manifest_sha256": sha256_file(governance_path),
        "historical_formal_rule_source": governance.get("formal_rule_source")
        or "legacy project activation receipt",
        "historical_rule_file": governance.get("active_rule_file")
        or governance.get("candidate_rule_file")
        or governance.get("formal_rule_file"),
        "current_project_rule_authority": False,
        "requires_external_current_verification_for_present_day_formal_use": True,
        "probability_mutation": False,
        "market_mutation": False,
        "price_mutation": False,
        "policy": (
            "Historical activation compatibility only. This GitHub manifest does not identify the present-day "
            "project CURRENT; present-day formal use requires separate verification of the unique project-scoped CURRENT."
        ),
    }
    return output
