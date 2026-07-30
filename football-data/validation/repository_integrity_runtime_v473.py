#!/usr/bin/env python3
"""V4.7.3 runtime wrapper around the V4.7.1 repository integrity audit.

The underlying V4.7.1 scanner remains strict. This wrapper recognizes only a
small, explicit set of immutable migration/reconciliation provenance artifacts
as historical references to the retired source repository. It also resolves the
known Windows checkout CRLF false-negative for the hash-bound formal engine by
verifying the repository-text (LF-normalized) SHA256 before suppressing only the
two engine-byte findings caused by line-ending conversion.
"""
from __future__ import annotations

import argparse
import hashlib
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
ENGINE_LINE_ENDING_CODES = {
    "formal_engine_sha_mismatch",
    "formal_core_manifest_engine_sha_mismatch",
}


def repository_text_sha256(path: Path) -> str:
    """Hash repository text canonically so Windows CRLF checkout does not alter identity."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _engine_hash_binding() -> dict[str, Any]:
    bootstrap_path = BASE.FOOTBALL / "manifests" / "runtime_bootstrap.json"
    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    core_cfg = bootstrap.get("formal_core") or {}
    engine_path = BASE.ROOT / str(core_cfg.get("engine_path") or "")
    expected = str(core_cfg.get("expected_engine_sha256") or "")
    actual = repository_text_sha256(engine_path) if engine_path.is_file() else None
    return {
        "engine_path": str(core_cfg.get("engine_path") or ""),
        "expected_repository_text_sha256": expected,
        "actual_repository_text_sha256": actual,
        "repository_text_hash_matches": bool(expected and actual == expected),
        "normalization": "CRLF_TO_LF_BEFORE_SHA256",
    }


def audit() -> dict[str, Any]:
    report = BASE.audit()
    filtered_errors: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    engine_binding = _engine_hash_binding()

    for item in report.get("errors", []):
        code = item.get("code")
        if code in ENGINE_LINE_ENDING_CODES and engine_binding["repository_text_hash_matches"]:
            suppressed.append({
                "code": "windows_crlf_engine_hash_false_negative_suppressed",
                "original_code": code,
                "reason": "LF-normalized repository text hash exactly matches the frozen formal-engine SHA256",
            })
            continue

        if code != "active_legacy_repo_reference":
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
    details = report.setdefault("details", {})
    details["historical_legacy_provenance_allowlist"] = {
        "allowed_paths": sorted(ALLOWED_HISTORICAL_LEGACY_REFERENCES),
        "suppressed_findings": [row for row in suppressed if row.get("code") == "historical_legacy_provenance_allowed"],
        "policy": "Only immutable migration/reconciliation provenance may reference the retired source repository; active runtime authority may not.",
    }
    details["formal_engine_repository_text_binding"] = {
        **engine_binding,
        "suppressed_findings": [row for row in suppressed if row.get("code") == "windows_crlf_engine_hash_false_negative_suppressed"],
        "policy": "Only line-ending byte drift may be normalized. Any LF-normalized hash mismatch remains a hard failure.",
    }
    report["schema_version"] = "V4.7.3-repository-integrity-runtime-wrapper-r2"
    report["policy"] = (
        "Engineering integrity only. Historical migration provenance is explicitly separated from active runtime authority. "
        "Formal-engine identity is checked using LF-normalized repository text so Windows CRLF checkout cannot create a false mismatch. "
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
            "formal_engine_repository_text_binding": report.get("details", {}).get("formal_engine_repository_text_binding"),
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
