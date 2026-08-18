#!/usr/bin/env python3
"""V5.0 competition-season selective 1X2 direction gate.

This module never changes any probability, score cell, market or price. It only
annotates whether the final 1X2 Top-1 direction is eligible to be presented as a
formal direction, otherwise the correct action is ABSTAIN.

Runtime activation is fail-closed: V5 governance must be formally activated and a
matching promotion receipt must exist. Until then this module reports 未启用.
"""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

from platform_core import ROOT, load_json, sha256_file

V500_STATUS = ROOT / "manifests" / "v500_upgrade_status.json"
V501_STATUS = ROOT / "manifests" / "v501_upgrade_status.json"
PROMOTION = ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500.json"
ACTIVATION = ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_runtime_activation.json"
MODULE_PATH = Path(__file__).resolve()
ACTIONABLE_RUNNER = ROOT / "engine" / "run_formal_prediction_actionable.py"
SELECTION = ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_selection.json"


def _git_blob_sha(path: Path) -> str:
    """Return the repository blob id consistently on LF and CRLF checkouts."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def _activation_error() -> str | None:
    required = {
        "selection_receipt": SELECTION,
        "promotion_receipt": PROMOTION,
        "runtime_gate": MODULE_PATH,
        "actionable_runner": ACTIONABLE_RUNNER,
        "v500_governance": V500_STATUS,
        "v501_governance": V501_STATUS,
    }
    if not ACTIVATION.exists():
        return "runtime activation receipt missing"
    for key, path in required.items():
        if not path.exists():
            return f"runtime activation bound file missing: {key}"

    activation = load_json(ACTIVATION)
    if activation.get("status") != "ACTIVE_HASH_BOUND":
        return f"runtime activation is not ACTIVE_HASH_BOUND: {activation.get('status')}"
    if activation.get("competition_id") != "ESP_LaLiga":
        return "runtime activation competition mismatch"
    if str(activation.get("target_season") or "") != "2026/27":
        return "runtime activation target-season mismatch"
    binding = activation.get("formal_rule_binding") or {}
    if binding.get("version") != "V5.0.1":
        return "runtime activation is not bound to CURRENT V5.0.1"

    expected_paths = activation.get("bound_paths") or {}
    expected_hashes = activation.get("bound_git_blob_sha") or {}
    for key, path in required.items():
        expected_path = str(path.relative_to(ROOT.parent)).replace("\\", "/")
        if str(expected_paths.get(key) or "") != expected_path:
            return f"runtime activation path mismatch: {key}"
        actual = _git_blob_sha(path)
        if str(expected_hashes.get(key) or "") != actual:
            return f"runtime activation hash mismatch: {key}"
    return None


def _rank_1x2(one: dict[str, Any]) -> list[tuple[str, float]]:
    required = ("home", "draw", "away")
    if any(key not in one for key in required):
        raise ValueError("final 1X2 probabilities missing home/draw/away")
    values = [(key, float(one[key])) for key in required]
    if abs(sum(value for _, value in values) - 1.0) > 1e-6:
        raise ValueError("final 1X2 probabilities do not sum to 1")
    return sorted(values, key=lambda item: (-item[1], item[0]))


def evaluate_gate(context: dict[str, Any], calculation: dict[str, Any], promotion: dict[str, Any]) -> dict[str, Any]:
    identity = context.get("match_identity") or calculation.get("match_identity") or {}
    competition_id = str(identity.get("competition_id") or "")
    season = str(identity.get("season") or "")
    expected_competition = str(promotion.get("competition_id") or "")
    expected_season = str(promotion.get("target_season") or "")
    if competition_id != expected_competition or season != expected_season:
        return {
            "status": "不适用",
            "competition_id": competition_id,
            "target_season": season,
            "probability_mutation": False,
            "formal_direction_allowed": None,
            "reason": "promotion does not apply to this competition-season",
        }

    probabilities = calculation.get("probabilities") or {}
    one = probabilities.get("1x2")
    if not isinstance(one, dict):
        return {
            "status": "不可用",
            "competition_id": competition_id,
            "target_season": season,
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": "final 1X2 probabilities missing",
        }
    try:
        ranking = _rank_1x2(one)
    except (TypeError, ValueError) as exc:
        return {
            "status": "失败",
            "competition_id": competition_id,
            "target_season": season,
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": str(exc),
        }

    threshold = float(promotion["selected_threshold"])
    gap = ranking[0][1] - ranking[1][1]
    allowed = gap + 1e-15 >= threshold
    return {
        "status": "通过" if allowed else "弃权",
        "competition_id": competition_id,
        "target_season": season,
        "probability_mutation": False,
        "formal_direction_allowed": allowed,
        "top1_direction": ranking[0][0],
        "top1_probability": ranking[0][1],
        "top2_direction": ranking[1][0],
        "top2_probability": ranking[1][1],
        "gap": gap,
        "threshold": threshold,
        "decision": ranking[0][0] if allowed else "ABSTAIN",
        "policy": "selection gate only; probabilities unchanged",
    }


def apply_selective_direction_gate(context: dict[str, Any], calculation: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(calculation)
    if not V500_STATUS.exists() or not V501_STATUS.exists() or not PROMOTION.exists():
        output["selective_direction_gate_v500_audit"] = {
            "status": "不可用",
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": "V5.0.0 promotion governance, V5.0.1 CURRENT, or promotion receipt missing",
        }
        return output

    promotion = load_json(PROMOTION)
    identity = context.get("match_identity") or calculation.get("match_identity") or {}
    if (
        str(identity.get("competition_id") or "") != str(promotion.get("competition_id") or "")
        or str(identity.get("season") or "") != str(promotion.get("target_season") or "")
    ):
        audit = evaluate_gate(context, output, promotion)
        audit["promotion_receipt_path"] = str(PROMOTION.relative_to(ROOT))
        audit["promotion_receipt_sha256"] = sha256_file(PROMOTION)
        output["selective_direction_gate_v500_audit"] = audit
        return output

    governance = load_json(V500_STATUS)
    if not str(governance.get("status") or "").startswith("FORMALLY_ACTIVATED"):
        output["selective_direction_gate_v500_audit"] = {
            "status": "未启用",
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": "V5 is staged but not the formally activated unique CURRENT",
            "v500_status": governance.get("status"),
        }
        return output

    current = load_json(V501_STATUS)
    if (
        not str(current.get("status") or "").startswith("FORMALLY_ACTIVATED")
        or str(current.get("formal_rule_version") or "") != "V5.0.1"
    ):
        output["selective_direction_gate_v500_audit"] = {
            "status": "未启用",
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": "V5.0.1 is not the formally activated unique CURRENT",
            "v501_status": current.get("status"),
        }
        return output

    if str(promotion.get("status") or "") not in {"FORMALLY_ACTIVATED", "PROMOTED_ACTIVE_HASH_BOUND"}:
        output["selective_direction_gate_v500_audit"] = {
            "status": "未启用",
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": "promotion receipt is not formally activated",
            "promotion_status": promotion.get("status"),
        }
        return output

    activation_error = _activation_error()
    if activation_error:
        output["selective_direction_gate_v500_audit"] = {
            "status": "不可用",
            "probability_mutation": False,
            "formal_direction_allowed": False,
            "reason": activation_error,
            "method": "hash_bound_runtime_activation_gate",
        }
        return output

    audit = evaluate_gate(context, output, promotion)
    audit["runtime_activation_status"] = "ACTIVE_HASH_BOUND"
    audit["runtime_activation_path"] = str(ACTIVATION.relative_to(ROOT))
    audit["promotion_receipt_path"] = str(PROMOTION.relative_to(ROOT))
    audit["promotion_receipt_sha256"] = sha256_file(PROMOTION)
    output["selective_direction_gate_v500_audit"] = audit
    return output
