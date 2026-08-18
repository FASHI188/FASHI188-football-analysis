#!/usr/bin/env python3
"""Build or verify the hash-bound LaLiga selective-direction activation.

`--check` is read-only. Running without it materializes an activation receipt and
therefore belongs under runtime/activation rather than validation. This file has
no project-state, CURRENT-selection or user-authorization authority.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = ROOT.parent
OUT = ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_runtime_activation.json"

PATHS = {
    "selection_receipt": ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_selection.json",
    "promotion_receipt": ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500.json",
    "runtime_gate": ROOT / "engine" / "selective_direction_gate_v500.py",
    "actionable_runner": ROOT / "engine" / "run_formal_prediction_actionable.py",
    "v500_governance": ROOT / "manifests" / "v500_upgrade_status.json",
    "v501_governance": ROOT / "manifests" / "v501_upgrade_status.json",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def build_payload() -> dict:
    missing = [key for key, path in PATHS.items() if not path.exists()]
    if missing:
        raise SystemExit(f"missing activation inputs: {missing}")
    promotion = load_json(PATHS["promotion_receipt"])
    v500 = load_json(PATHS["v500_governance"])
    v501 = load_json(PATHS["v501_governance"])
    runner_text = PATHS["actionable_runner"].read_text(encoding="utf-8")
    checks = {
        "historical_v501_binding_verified": (
            str(v501.get("status") or "").startswith("FORMALLY_ACTIVATED")
            and v501.get("formal_rule_version") == "V5.0.1"
        ),
        "v500_promotion_governance_activated": str(v500.get("status") or "").startswith("FORMALLY_ACTIVATED"),
        "promotion_status_active": promotion.get("status") == "PROMOTED_ACTIVE_HASH_BOUND",
        "competition_match": promotion.get("competition_id") == "ESP_LaLiga",
        "target_season_match": promotion.get("target_season") == "2026/27",
        "module_match": promotion.get("module") == "selective_direction_gate_v500",
        "probability_mutation_prohibited": promotion.get("probability_mutation") is False,
        "selected_threshold_is_0_30": float(promotion.get("selected_threshold", -1)) == 0.30,
        "runner_import_wired": "from selective_direction_gate_v500 import apply_selective_direction_gate" in runner_text,
        "runner_final_call_wired": "return apply_selective_direction_gate(context, governed)" in runner_text,
    }
    active = all(checks.values())
    return {
        "schema_version": "V5.0.1-selective-direction-runtime-activation-r3",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "ACTIVE_HASH_BOUND" if active else "INACTIVE_FAIL_CLOSED",
        "competition_id": "ESP_LaLiga",
        "target_season": "2026/27",
        "module": "selective_direction_gate_v500",
        "activation_order": "after_final_probability_matrix_market_coordination_total_diagnostics_and_formal_governance",
        "selected_threshold": 0.30,
        "formal_probability_weight": 0,
        "probability_mutation": False,
        "direction_semantics": "ALLOW_FINAL_1X2_TOP1_IF_TOP1_MINUS_TOP2_GAP_GTE_0.30_ELSE_ABSTAIN",
        "bound_git_blob_sha": {key: git_blob_sha(path) for key, path in PATHS.items()},
        "bound_paths": {key: path.relative_to(REPO_ROOT).as_posix() for key, path in PATHS.items()},
        "historical_formal_rule_binding": {
            "version_at_activation_freeze": "V5.0.1",
            "filename": v501.get("formal_rule_file"),
            "sha256": v501.get("formal_rule_sha256"),
            "current_authority": False
        },
        "checks": checks,
        "policy": "Fail closed on any bound artifact mismatch. Historical formal-rule binding is provenance only; this script never identifies the project's current CURRENT.",
    }


def critical_view(payload: dict) -> dict:
    return {key: payload.get(key) for key in (
        "schema_version", "status", "competition_id", "target_season", "module",
        "activation_order", "selected_threshold", "formal_probability_weight",
        "probability_mutation", "direction_semantics", "bound_git_blob_sha",
        "bound_paths", "checks",
    )}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = build_payload()
    if args.check:
        if not OUT.exists():
            print(json.dumps({"status": "FAIL", "reason": "activation missing"}, indent=2))
            return 2
        current = load_json(OUT)
        passed = critical_view(current) == critical_view(expected)
        print(json.dumps({
            "status": "PASS" if passed else "FAIL_STALE_ACTIVATION",
            "expected_status": expected["status"],
            "bound_git_blob_sha": expected["bound_git_blob_sha"],
        }, ensure_ascii=False, indent=2))
        return 0 if passed and expected["status"] == "ACTIVE_HASH_BOUND" else 2
    OUT.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(expected, ensure_ascii=False, indent=2))
    return 0 if expected["status"] == "ACTIVE_HASH_BOUND" else 2


if __name__ == "__main__":
    raise SystemExit(main())
