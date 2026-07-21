#!/usr/bin/env python3
"""Hash-bound smoke for the V5 LaLiga selective-direction runtime gate."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

FOOTBALL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FOOTBALL_ROOT.parent
ENGINE = FOOTBALL_ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from platform_core import atomic_write_json, load_json
from selective_direction_gate_v500 import apply_selective_direction_gate

ACTIVATION = FOOTBALL_ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_runtime_activation.json"
OUT = FOOTBALL_ROOT / "manifests" / "promotions" / "ESP_LaLiga_selective_direction_v500_runtime_smoke.json"


def git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def main() -> int:
    errors: list[str] = []
    activation = load_json(ACTIVATION)
    root_paths = activation.get("bound_paths") or {}
    expected = activation.get("bound_git_blob_sha") or {}

    key_to_path = {
        "selection_receipt": REPO_ROOT / str(root_paths.get("selection_receipt") or ""),
        "promotion_receipt": REPO_ROOT / str(root_paths.get("promotion_receipt") or ""),
        "runtime_gate": REPO_ROOT / str(root_paths.get("runtime_gate") or ""),
        "actionable_runner": REPO_ROOT / str(root_paths.get("actionable_runner") or ""),
        "v500_governance": REPO_ROOT / str(root_paths.get("v500_governance") or ""),
    }
    actual_hashes = {}
    for key, path in key_to_path.items():
        if not path.exists():
            errors.append(f"missing bound file: {key} {path}")
            continue
        actual_hashes[key] = git_blob_sha(path)
        if actual_hashes[key] != str(expected.get(key) or ""):
            errors.append(f"hash mismatch: {key} expected={expected.get(key)} actual={actual_hashes[key]}")

    runner_text = key_to_path["actionable_runner"].read_text(encoding="utf-8") if key_to_path["actionable_runner"].exists() else ""
    import_marker = "from selective_direction_gate_v500 import apply_selective_direction_gate"
    call_marker = "return apply_selective_direction_gate(context, governed)"
    if import_marker not in runner_text:
        errors.append("actionable runner missing selective gate import")
    if call_marker not in runner_text:
        errors.append("actionable runner missing final selective gate call")
    if "governed = apply_formal_governance_runtime(diagnosed)" not in runner_text:
        errors.append("selective gate is not demonstrably ordered after formal governance")

    strong_context = {"match_identity": {"competition_id": "ESP_LaLiga", "season": "2026/27"}}
    strong_calc = {"probabilities": {"1x2": {"home": 0.66, "draw": 0.20, "away": 0.14}}}
    weak_context = {"match_identity": {"competition_id": "ESP_LaLiga", "season": "2026/27"}}
    weak_calc = {"probabilities": {"1x2": {"home": 0.47, "draw": 0.29, "away": 0.24}}}
    other_context = {"match_identity": {"competition_id": "ENG_PremierLeague", "season": "2026/27"}}
    other_calc = {"probabilities": {"1x2": {"home": 0.66, "draw": 0.20, "away": 0.14}}}

    strong_out = apply_selective_direction_gate(strong_context, strong_calc)
    weak_out = apply_selective_direction_gate(weak_context, weak_calc)
    other_out = apply_selective_direction_gate(other_context, other_calc)
    strong_audit = strong_out.get("selective_direction_gate_v500_audit") or {}
    weak_audit = weak_out.get("selective_direction_gate_v500_audit") or {}
    other_audit = other_out.get("selective_direction_gate_v500_audit") or {}

    if strong_audit.get("status") != "通过" or strong_audit.get("formal_direction_allowed") is not True:
        errors.append(f"strong signal not allowed: {strong_audit}")
    if weak_audit.get("status") != "弃权" or weak_audit.get("formal_direction_allowed") is not False:
        errors.append(f"weak signal did not abstain: {weak_audit}")
    if other_audit.get("status") != "不适用":
        errors.append(f"non-LaLiga domain not marked not-applicable: {other_audit}")
    if strong_out.get("probabilities") != strong_calc.get("probabilities"):
        errors.append("strong case probabilities mutated")
    if weak_out.get("probabilities") != weak_calc.get("probabilities"):
        errors.append("weak case probabilities mutated")
    if float(strong_audit.get("threshold", -1)) != 0.30:
        errors.append(f"unexpected threshold: {strong_audit.get('threshold')}")

    payload = {
        "schema_version": "V5.0.0-selective-direction-runtime-smoke-r2",
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "PASS" if not errors else "FAIL",
        "competition_id": "ESP_LaLiga",
        "target_season": "2026/27",
        "activation_status": activation.get("status"),
        "bound_hashes_expected": expected,
        "bound_hashes_actual": actual_hashes,
        "checks": {
            "all_bound_hashes_match": not any(item.startswith("hash mismatch") or item.startswith("missing bound") for item in errors),
            "runner_import_wired": import_marker in runner_text,
            "runner_final_call_wired": call_marker in runner_text,
            "strong_signal_allowed": strong_audit.get("status") == "通过" and strong_audit.get("formal_direction_allowed") is True,
            "weak_signal_abstains": weak_audit.get("status") == "弃权" and weak_audit.get("formal_direction_allowed") is False,
            "other_domain_not_applicable": other_audit.get("status") == "不适用",
            "probabilities_unchanged": strong_out.get("probabilities") == strong_calc.get("probabilities") and weak_out.get("probabilities") == weak_calc.get("probabilities"),
            "threshold_is_0_30": float(strong_audit.get("threshold", -1)) == 0.30,
        },
        "strong_case_audit": strong_audit,
        "weak_case_audit": weak_audit,
        "other_domain_audit": other_audit,
        "errors": errors,
        "policy": "Direction/abstention gate only; no probability mutation. Any bound hash mismatch fails closed."
    }
    atomic_write_json(OUT, payload)
    print(json.dumps({"status": payload["status"], "errors": errors}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
