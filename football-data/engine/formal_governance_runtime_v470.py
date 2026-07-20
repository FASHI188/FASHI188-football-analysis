#!/usr/bin/env python3
"""Read-only governance normalization for final formal calculation artifacts.

The validated probability implementation intentionally retains its implementation
version/hash. Formal rule authority comes from the active project governance manifest
that mirrors the unique File Library CURRENT. This layer prevents final artifacts
from presenting a stale implementation/governance version as the current formal rule.

No probabilities, markets, prices, or model weights are changed.
"""
from __future__ import annotations

import copy
from typing import Any

from platform_core import ROOT, load_json, sha256_file

GOVERNANCE_STATUS = ROOT / "manifests" / "v480_upgrade_status.json"


def apply_formal_governance_runtime(calculation: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    previous_rule_version = output.get("rule_version")
    if not GOVERNANCE_STATUS.exists():
        output["formal_governance_audit"] = {
            "status": "不可用",
            "reason": "active governance status manifest missing",
            "probability_mutation": False,
        }
        return output
    governance = load_json(GOVERNANCE_STATUS)
    formal_rule_version = str(governance.get("formal_rule_version") or "").strip()
    governance_status = str(governance.get("status") or "").strip()
    if not formal_rule_version:
        output["formal_governance_audit"] = {
            "status": "失败",
            "reason": "formal_rule_version missing from active governance status",
            "probability_mutation": False,
        }
        return output
    if not governance_status.startswith("FORMALLY_ACTIVATED"):
        output["formal_governance_audit"] = {
            "status": "失败",
            "reason": f"governance manifest is not formally activated: {governance_status}",
            "probability_mutation": False,
        }
        return output

    output["implementation_rule_version"] = previous_rule_version
    output["rule_version"] = formal_rule_version
    output["formal_governance_audit"] = {
        "status": "通过",
        "formal_rule_version": formal_rule_version,
        "underlying_implementation_rule_version": previous_rule_version,
        "underlying_engine_version": output.get("engine_version"),
        "governance_status": governance_status,
        "governance_manifest_path": str(GOVERNANCE_STATUS.relative_to(ROOT)),
        "governance_manifest_sha256": sha256_file(GOVERNANCE_STATUS),
        "formal_rule_source": governance.get("formal_rule_source"),
        "active_rule_file": governance.get("active_rule_file"),
        "probability_mutation": False,
        "market_mutation": False,
        "price_mutation": False,
        "policy": "Rule authority is reported from the active unique File Library CURRENT; underlying validated implementation metadata is retained separately for auditability.",
    }
    return output
