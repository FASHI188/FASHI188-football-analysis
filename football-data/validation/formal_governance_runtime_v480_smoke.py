#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from formal_governance_runtime_v470 import apply_formal_governance_runtime  # noqa: E402
from platform_core import atomic_write_json, sha256_file  # noqa: E402

OUT = ROOT / "manifests" / "formal_governance_runtime_v480_smoke.json"
RUNTIME = ROOT / "engine" / "formal_governance_runtime_v470.py"
GOVERNANCE = ROOT / "manifests" / "v480_upgrade_status.json"


def main() -> int:
    baseline = {
        "rule_version": "V4.6.4-implementation",
        "engine_version": "V4.6.x",
        "probabilities": {"1x2": {"home": 0.5, "draw": 0.25, "away": 0.25}},
    }
    output = apply_formal_governance_runtime(baseline)
    audit = output.get("formal_governance_audit") or {}
    checks = {
        "audit_passed": audit.get("status") == "通过",
        "formal_rule_version_v480": output.get("rule_version") == "V4.8.0",
        "implementation_version_preserved": output.get("implementation_rule_version") == "V4.6.4-implementation",
        "probabilities_unchanged": output.get("probabilities") == baseline.get("probabilities"),
        "probability_mutation_false": audit.get("probability_mutation") is False,
        "governance_manifest_is_v480": audit.get("governance_manifest_path") == "manifests/v480_upgrade_status.json",
    }
    payload = {
        "schema_version": "V4.8.0-formal-governance-runtime-smoke-r1",
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "runtime_sha256": sha256_file(RUNTIME),
        "governance_sha256": sha256_file(GOVERNANCE),
        "reported_formal_rule_version": output.get("rule_version"),
        "formal_governance_audit": audit,
    }
    atomic_write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
