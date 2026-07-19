#!/usr/bin/env python3
"""V4.7.3 runtime wrapper around the V4.7.1 repository integrity audit.

The underlying V4.7.1 scanner remains strict. This wrapper recognizes only a
small, explicit set of immutable migration/reconciliation provenance artifacts
as historical references to the retired source repository. Active workflows,
runtime code, configs, and current manifests are not exempted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "repository_integrity_v471.py"
SPEC = importlib.util.spec_from_file_location("repository_integrity_v471_base", BASE_PATH)
BASE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

ALLOWED_HISTORICAL_LEGACY_REFERENCES = {
    "football-data/manifests/repository_reconciliation_v472_status.json",
}


def audit() -> dict[str, Any]:
    report = BASE.audit()
    filtered_errors: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []

    for item in report.get("errors", []):
        if item.get("code") != "active_legacy_repo_reference":
            filtered_errors.append(item)
            continue

        paths = list(item.get("paths") or [])
        remaining = [p for p in paths if p not in ALLOWED_HISTORICAL_LEGACY_REFERENCES]
        allowed = [p for p in paths if p in ALLOWED_HISTORICAL_LEGACY_REFERENCES]
        if allowed:
            suppressed.append({
                "code": "historical_legacy_provenance_allowed",
                "paths": allowed,
                "reason": "completed migration/reconciliation receipt retained as immutable audit provenance",
            })
        if remaining:
            updated = dict(item)
            updated["paths"] = remaining
            filtered_errors.append(updated)

    report["errors"] = filtered_errors
    report["hard_error_count"] = len(filtered_errors)
    report["status"] = "PASS" if not filtered_errors else "FAIL"
    report.setdefault("details", {})["historical_legacy_provenance_allowlist"] = {
        "allowed_paths": sorted(ALLOWED_HISTORICAL_LEGACY_REFERENCES),
        "suppressed_findings": suppressed,
        "policy": "Only immutable migration/reconciliation provenance may reference the retired source repository; active runtime authority may not.",
    }
    report["schema_version"] = "V4.7.3-repository-integrity-runtime-wrapper"
    report["policy"] = (
        "Engineering integrity only. Historical migration provenance is explicitly separated from active runtime authority. "
        "This audit cannot modify CURRENT or formal model weights."
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = audit()
    if args.write_receipt:
        BASE.OUT.parent.mkdir(parents=True, exist_ok=True)
        BASE.OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "hard_error_count": report["hard_error_count"],
            "warning_count": report.get("warning_count", 0),
            "errors": report["errors"],
            "historical_legacy_provenance": report.get("details", {}).get("historical_legacy_provenance_allowlist"),
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
