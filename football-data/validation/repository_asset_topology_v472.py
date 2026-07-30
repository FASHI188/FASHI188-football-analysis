#!/usr/bin/env python3
"""V4.7.2 fail-closed asset-topology guard for the dedicated football repo.

This guard catches cross-batch deletion and migration incompleteness that pure
syntax/hash checks cannot detect. It never changes CURRENT or formal weights.
Governance-archived workflows are validated against their explicit capability
adjudication instead of being incorrectly required to remain active.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "repository_asset_topology_v472_status.json"
CAP_ADJ = ROOT / "governance" / "consolidated_capability_adjudication.json"
BATCH001_SOURCE = ".github/workflows/football-data-batch-001.yml"
BATCH001_ARCHIVE = ROOT / "governance" / "archive" / "workflows" / "phase2c-consolidate-batch01" / "football-data-batch-001.yml"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _capability_adjudication(source_path: str) -> dict[str, Any] | None:
    if not CAP_ADJ.is_file():
        return None
    payload = load_json(CAP_ADJ)
    for row in payload.get("entries", []):
        if isinstance(row, dict) and row.get("source_path") == source_path:
            return row
    return None


def audit() -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    registry_path = FOOTBALL / "config" / "platform_registry.json"
    registry = load_json(registry_path)
    competitions = [
        item["competition_id"]
        for item in registry.get("competitions", [])
        if isinstance(item, dict) and item.get("competition_id")
    ]

    missing_profiles = [
        cid for cid in competitions
        if not (FOOTBALL / "league_profiles" / cid / "profile.json").is_file()
    ]
    if missing_profiles:
        errors.append({
            "code": "registered_competition_profiles_missing",
            "competitions": missing_profiles,
        })

    batch1_workflow = ROOT / BATCH001_SOURCE
    safe_adapter = FOOTBALL / "engine" / "ingest_batch_001_safe_adapter.py"
    safe_test = FOOTBALL / "tests" / "test_ingest_batch_001_safe_merge.py"
    if not safe_adapter.is_file():
        errors.append({"code": "batch001_safe_adapter_missing"})
    if not safe_test.is_file():
        errors.append({"code": "batch001_cross_competition_regression_test_missing"})

    if batch1_workflow.is_file():
        text = batch1_workflow.read_text(encoding="utf-8")
        if "python football-data/engine/ingest_batch_001_safe_adapter.py" not in text:
            errors.append({"code": "batch001_workflow_not_using_safe_adapter"})
        if "python football-data/engine/ingest_batch_001_alias_adapter.py" in text:
            errors.append({"code": "batch001_workflow_calls_destructive_legacy_adapter"})
        if "registered competition profiles missing" not in text and "would leave registered competition profiles missing" not in text:
            warnings.append({"code": "batch001_post_publish_domain_guard_not_detected"})
        batch001_authority = "ACTIVE_WORKFLOW"
    else:
        adjudication = _capability_adjudication(BATCH001_SOURCE)
        if adjudication is None:
            errors.append({"code": "batch001_workflow_missing_without_governance_adjudication"})
        elif adjudication.get("adjudication") != "STATIC_REFERENCE_ONLY":
            errors.append({
                "code": "batch001_archived_workflow_adjudication_invalid",
                "adjudication": adjudication.get("adjudication"),
            })
        elif not BATCH001_ARCHIVE.is_file():
            errors.append({
                "code": "batch001_governance_archive_missing",
                "path": BATCH001_ARCHIVE.relative_to(ROOT).as_posix(),
            })
        else:
            batch001_authority = "GOVERNANCE_ARCHIVE_STATIC_REFERENCE_ONLY"

    jpn = next((item for item in registry.get("competitions", []) if item.get("competition_id") == "JPN_J1"), {})
    if jpn.get("official_transition_route_status") == "OFFICIAL_TRANSITION_ROUTE_VALIDATED":
        required_jpn = [
            FOOTBALL / "processed" / "JPN_J1" / "official_2026_special.csv",
            FOOTBALL / "raw" / "JPN_J1" / "official_2026_special_source.json",
            FOOTBALL / "manifests" / "jpn_j1_2026_special_official_v467_status.json",
            FOOTBALL / "manifests" / "jpn_j1_promotion_review_v467_status.json",
        ]
        missing = [p.relative_to(ROOT).as_posix() for p in required_jpn if not p.is_file()]
        if missing:
            errors.append({
                "code": "jpn_validated_special_route_assets_missing",
                "paths": missing,
            })

    reconciliation_path = FOOTBALL / "manifests" / "repository_reconciliation_v472_status.json"
    reconciliation_summary: dict[str, Any] | None = None
    if reconciliation_path.is_file():
        rec = load_json(reconciliation_path)
        reconciliation_summary = {
            "status": rec.get("status"),
            "critical_source_only_count": len(rec.get("critical_source_only") or []),
            "workflow_source_only_review_count": len(rec.get("workflow_source_only_review") or []),
            "other_source_only_review_count": len(rec.get("other_source_only_review") or []),
        }
        if reconciliation_summary["critical_source_only_count"]:
            warnings.append({
                "code": "reconciliation_receipt_still_reports_source_only_critical_assets",
                "count": reconciliation_summary["critical_source_only_count"],
            })

    status = "PASS" if not errors else "FAIL"
    return {
        "schema_version": "V4.7.2-repository-asset-topology-governance-aware-r2",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "hard_error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "registered_competition_count": len(competitions),
        "registered_competition_profiles_present": len(competitions) - len(missing_profiles),
        "batch001_workflow_authority": locals().get("batch001_authority", "INVALID"),
        "reconciliation_summary": reconciliation_summary,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "policy": "Repository asset topology only; governance-archived STATIC_REFERENCE_ONLY workflows are not required to remain active; no CURRENT or formal model weight changes.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-receipt", action="store_true")
    parser.add_argument("--strict-exit", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    args = parser.parse_args()

    report = audit()
    if args.write_receipt:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.print_summary:
        print(json.dumps({
            "status": report["status"],
            "hard_error_count": report["hard_error_count"],
            "warning_count": report["warning_count"],
            "registered_competition_profiles_present": report["registered_competition_profiles_present"],
            "registered_competition_count": report["registered_competition_count"],
            "batch001_workflow_authority": report["batch001_workflow_authority"],
            "errors": report["errors"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
