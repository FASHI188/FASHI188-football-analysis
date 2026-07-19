#!/usr/bin/env python3
"""V4.7.3 scheduled maintenance audit for the dedicated football runtime repo.

This is engineering maintenance only. It does not modify CURRENT, probabilities,
model weights, promotion states, or competition conclusions.

The maintenance pass composes the repository-integrity runtime wrapper and
asset-topology gates, then adds runtime asset coverage checks for all registered
competitions.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "runtime_maintenance_v473_status.json"

INTEGRITY_SCRIPT = FOOTBALL / "validation" / "repository_integrity_runtime_v473.py"
TOPOLOGY_SCRIPT = FOOTBALL / "validation" / "repository_asset_topology_v472.py"
INTEGRITY_RECEIPT = FOOTBALL / "manifests" / "repository_integrity_v471_status.json"
TOPOLOGY_RECEIPT = FOOTBALL / "manifests" / "repository_asset_topology_v472_status.json"
RECONCILIATION_RECEIPT = FOOTBALL / "manifests" / "repository_reconciliation_v472_status.json"
REGISTRY = FOOTBALL / "config" / "platform_registry.json"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_audit(script: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, str(script), "--write-receipt", "--print-summary"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return {
        "script": script.relative_to(ROOT).as_posix(),
        "returncode": proc.returncode,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def audit_runtime_assets() -> dict[str, Any]:
    registry = load_json(REGISTRY)
    competitions = [
        item["competition_id"]
        for item in registry.get("competitions", [])
        if isinstance(item, dict) and item.get("competition_id")
    ]

    missing_profiles: list[str] = []
    missing_processed_domains: list[str] = []
    empty_processed_domains: list[str] = []
    per_competition: dict[str, Any] = {}

    for cid in competitions:
        profile = FOOTBALL / "league_profiles" / cid / "profile.json"
        processed_dir = FOOTBALL / "processed" / cid
        processed_files = sorted(p for p in processed_dir.rglob("*") if p.is_file()) if processed_dir.exists() else []

        if not profile.is_file():
            missing_profiles.append(cid)
        if not processed_dir.exists():
            missing_processed_domains.append(cid)
        elif not processed_files:
            empty_processed_domains.append(cid)

        per_competition[cid] = {
            "profile_present": profile.is_file(),
            "processed_directory_present": processed_dir.exists(),
            "processed_file_count": len(processed_files),
            "processed_total_bytes": sum(p.stat().st_size for p in processed_files),
        }

    errors = []
    if len(competitions) != 17:
        errors.append({"code": "registered_competition_count_not_17", "actual": len(competitions)})
    if missing_profiles:
        errors.append({"code": "registered_profiles_missing", "competitions": missing_profiles})
    if missing_processed_domains:
        errors.append({"code": "processed_competition_directories_missing", "competitions": missing_processed_domains})
    if empty_processed_domains:
        errors.append({"code": "processed_competition_directories_empty", "competitions": empty_processed_domains})

    return {
        "status": "PASS" if not errors else "FAIL",
        "registered_competition_count": len(competitions),
        "missing_profiles": missing_profiles,
        "missing_processed_domains": missing_processed_domains,
        "empty_processed_domains": empty_processed_domains,
        "errors": errors,
        "per_competition": per_competition,
    }


def build_report() -> dict[str, Any]:
    integrity_run = run_audit(INTEGRITY_SCRIPT)
    topology_run = run_audit(TOPOLOGY_SCRIPT)

    integrity = load_json(INTEGRITY_RECEIPT) if INTEGRITY_RECEIPT.is_file() else {"status": "MISSING"}
    topology = load_json(TOPOLOGY_RECEIPT) if TOPOLOGY_RECEIPT.is_file() else {"status": "MISSING"}
    reconciliation = load_json(RECONCILIATION_RECEIPT) if RECONCILIATION_RECEIPT.is_file() else {"status": "MISSING"}
    assets = audit_runtime_assets()

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if integrity.get("status") != "PASS":
        errors.append({"code": "repository_integrity_not_pass", "status": integrity.get("status")})
    if topology.get("status") != "PASS":
        errors.append({"code": "repository_asset_topology_not_pass", "status": topology.get("status")})
    if assets["status"] != "PASS":
        errors.extend(assets["errors"])

    rec_status = str(reconciliation.get("status") or "MISSING")
    if rec_status.startswith("FAIL_"):
        errors.append({"code": "last_cross_repository_reconciliation_failed", "status": rec_status})
    elif rec_status not in {"PASS_NO_UNEXPLAINED_SOURCE_ONLY_ASSETS", "PASS", "MISSING"}:
        warnings.append({"code": "cross_repository_reconciliation_status_review", "status": rec_status})
    elif rec_status == "MISSING":
        warnings.append({"code": "cross_repository_reconciliation_receipt_missing"})

    if int(integrity.get("warning_count", 0) or 0) > 0:
        warnings.append({"code": "repository_integrity_warnings_present", "count": integrity.get("warning_count")})
    if int(topology.get("warning_count", 0) or 0) > 0:
        warnings.append({"code": "repository_topology_warnings_present", "count": topology.get("warning_count")})

    return {
        "schema_version": "V4.7.3-runtime-maintenance",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if not errors else "FAIL",
        "hard_error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "repository_integrity": {
            "status": integrity.get("status"),
            "hard_error_count": integrity.get("hard_error_count"),
            "warning_count": integrity.get("warning_count"),
            "runner": integrity_run,
        },
        "repository_asset_topology": {
            "status": topology.get("status"),
            "hard_error_count": topology.get("hard_error_count"),
            "warning_count": topology.get("warning_count"),
            "registered_competition_profiles_present": topology.get("registered_competition_profiles_present"),
            "registered_competition_count": topology.get("registered_competition_count"),
            "runner": topology_run,
        },
        "runtime_asset_coverage": assets,
        "last_cross_repository_reconciliation_status": rec_status,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "current_rule_change": False,
        "policy": "Engineering maintenance only; fail closed on missing runtime assets and never modify CURRENT or formal model weights.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = build_report()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "hard_error_count": report["hard_error_count"],
            "warning_count": report["warning_count"],
            "registered_competition_count": report["runtime_asset_coverage"]["registered_competition_count"],
            "missing_profiles": report["runtime_asset_coverage"]["missing_profiles"],
            "missing_processed_domains": report["runtime_asset_coverage"]["missing_processed_domains"],
            "empty_processed_domains": report["runtime_asset_coverage"]["empty_processed_domains"],
            "last_cross_repository_reconciliation_status": report["last_cross_repository_reconciliation_status"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
