#!/usr/bin/env python3
"""Validate the V5.0 engineering package without pretending File Library activation.

This validator checks repository-side prerequisites, current V4.8 governance alignment,
V5 challenger registration, frozen LaLiga selective-threshold evidence and negative
research preservation. Formal activation intentionally remains pending until the exact
V5 CURRENT is the unique File Library CURRENT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import atomic_write_json, load_json, sha256_file  # noqa: E402

OUT = ROOT / "manifests" / "v500_upgrade_acceptance_status.json"

PATHS = {
    "formal_core": ROOT / "manifests" / "formal_core_v460_status.json",
    "oof": ROOT / "manifests" / "oof_matrix_calibration_v461_status.json",
    "maintenance": ROOT / "manifests" / "runtime_maintenance_v473_status.json",
    "v480": ROOT / "manifests" / "v480_upgrade_status.json",
    "v500": ROOT / "manifests" / "v500_upgrade_status.json",
    "registry": ROOT / "config" / "v500_multisource_challenger_registry.json",
    "selection": ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_selection.json",
    "promotion": ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500.json",
    "total_kl": ROOT / "manifests" / "total_residual_kl_tilt_rolling_oof_v470_status.json",
    "governance_smoke": ROOT / "manifests" / "formal_governance_runtime_v480_smoke.json",
    "selective_smoke": ROOT / "manifests" / "selective_direction_gate_v500_smoke.json",
}


def _optional_status(path: Path) -> str:
    if not path.exists():
        return "PENDING"
    try:
        return str(load_json(path).get("status") or "UNKNOWN")
    except Exception:
        return "INVALID"


def main() -> int:
    required = {key: path for key, path in PATHS.items() if key not in {"governance_smoke", "selective_smoke"}}
    missing = [str(path.relative_to(ROOT)) for path in required.values() if not path.exists()]
    if missing:
        payload = {"schema_version": "V5.0.0-upgrade-acceptance-r1", "status": "FAIL", "missing": missing}
        atomic_write_json(OUT, payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    formal_core = load_json(PATHS["formal_core"])
    oof = load_json(PATHS["oof"])
    maintenance = load_json(PATHS["maintenance"])
    v480 = load_json(PATHS["v480"])
    v500 = load_json(PATHS["v500"])
    registry = load_json(PATHS["registry"])
    selection = load_json(PATHS["selection"])
    promotion = load_json(PATHS["promotion"])
    total_kl = load_json(PATHS["total_kl"])

    module_weights = [
        int(item.get("formal_weight", -1))
        for item in (registry.get("modules") or {}).values()
        if isinstance(item, dict)
    ]
    checks = {
        "formal_core_17_of_17": formal_core.get("formal_core_available_count") == 17 and formal_core.get("competition_count_failed") == 0,
        "oof_calibrator_17_of_17": oof.get("calibrator_available_count") == 17 and oof.get("competition_count_failed") == 0,
        "runtime_maintenance_pass": maintenance.get("status") == "PASS" and maintenance.get("hard_error_count") == 0,
        "v480_current_governance_active": str(v480.get("status") or "").startswith("FORMALLY_ACTIVATED") and v480.get("formal_rule_version") == "V4.8.0",
        "v500_staged_not_early_activated": v500.get("status") == "ENGINEERING_ACCEPTANCE_PASS_FORMAL_ACTIVATION_PENDING",
        "v500_document_qa_pass": (v500.get("document_qa") or {}).get("status") == "PASS" and (v500.get("document_qa") or {}).get("all_rendered_pages_visually_inspected") is True,
        "v500_candidate_sha_present": len(str(v500.get("candidate_rule_sha256") or "")) == 64,
        "v500_challenger_default_weight_zero": v500.get("new_challenger_default_formal_weight") == 0 and all(weight == 0 for weight in module_weights),
        "single_joint_matrix_required": registry.get("single_joint_matrix_required") is True,
        "competition_isolation_required": registry.get("competition_isolation_required") is True,
        "laliga_threshold_selection_pass": selection.get("status") == "PASS" and selection.get("selected_threshold") == 0.3 and selection.get("target_season") == "2026/27",
        "laliga_target_outcomes_not_used": (promotion.get("target_season_threshold_selection") or {}).get("target_season_outcomes_used") is False,
        "laliga_promotion_pending_activation_only": promotion.get("status") == "APPROVED_PENDING_V5_FORMAL_ACTIVATION" and promotion.get("probability_mutation") is False,
        "total_kl_negative_result_preserved": total_kl.get("status") == "PASS" and (total_kl.get("rolling_oof_research_candidates") or []) == [],
        "existing_mls_promotion_preserved": any(
            item.get("competition_id") == "USA_MLS" and item.get("formal_weight") == 1.0
            for item in (v500.get("existing_formal_promotions_preserved") or [])
        ),
    }

    governance_smoke_status = _optional_status(PATHS["governance_smoke"])
    selective_smoke_status = _optional_status(PATHS["selective_smoke"])
    smoke_checks = {
        "v480_governance_smoke": governance_smoke_status,
        "v500_selective_gate_smoke": selective_smoke_status,
    }

    hard_pass = all(checks.values())
    smoke_pass = governance_smoke_status == "PASS" and selective_smoke_status == "PASS"
    if hard_pass and smoke_pass:
        status = "ENGINEERING_ACCEPTANCE_PASS_FORMAL_ACTIVATION_PENDING"
    elif hard_pass:
        status = "ENGINEERING_CORE_PASS_WAITING_SMOKE_RECEIPTS"
    else:
        status = "FAIL"

    payload = {
        "schema_version": "V5.0.0-upgrade-acceptance-r1",
        "status": status,
        "checks": checks,
        "smoke_checks": smoke_checks,
        "formal_activation": {
            "status": "PENDING_FILE_LIBRARY_UNIQUE_CURRENT_REPLACEMENT",
            "current_formal_rule_version": "V4.8.0",
            "candidate_formal_rule_version": "V5.0.0",
            "candidate_rule_file": v500.get("candidate_rule_file"),
            "candidate_rule_sha256": v500.get("candidate_rule_sha256"),
            "activation_does_not_auto_promote_probability_challengers": True,
        },
        "selective_direction_candidate": {
            "competition_id": "ESP_LaLiga",
            "target_season": "2026/27",
            "threshold": selection.get("selected_threshold"),
            "forward_oof_pooled_accuracy": (selection.get("forward_oof_evidence") or {}).get("pooled_accuracy"),
            "formal_runtime_activation": "PENDING_V5_ACTIVATION",
            "probability_mutation": False,
        },
        "source_hashes": {
            key: sha256_file(path)
            for key, path in PATHS.items()
            if path.exists()
        },
        "policy": "PASS here means the V5 engineering package is internally consistent and fail-closed. It is not formal activation; the unique File Library CURRENT must first be replaced and re-verified."
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
