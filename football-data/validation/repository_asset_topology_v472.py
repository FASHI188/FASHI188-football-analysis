#!/usr/bin/env python3
"""V4.7.2 fail-closed asset-topology guard for the dedicated football repo.

This guard catches cross-batch deletion and migration incompleteness that pure
syntax/hash checks cannot detect. It never changes CURRENT or formal weights.
Governance-archived workflows are validated against their explicit capability
adjudication instead of being incorrectly required to remain active.

For the JPN J1 2026 special transition route, the raw/processed work products were
never committed to Git. Clean-checkout audit therefore verifies the committed,
source-hash-bound transition manifest and the independently committed promotion
review for exact cross-consistency and frozen Git-blob identity instead of
pretending untracked files are repository assets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FOOTBALL = ROOT / "football-data"
OUT = FOOTBALL / "manifests" / "repository_asset_topology_v472_status.json"
CAP_ADJ = ROOT / "governance" / "consolidated_capability_adjudication.json"
BATCH001_SOURCE = ".github/workflows/football-data-batch-001.yml"
BATCH001_ARCHIVE = ROOT / "governance" / "archive" / "workflows" / "phase2c-consolidate-batch01" / "football-data-batch-001.yml"
JPN_TRANSITION_MANIFEST = FOOTBALL / "manifests" / "jpn_j1_2026_special_official_v467_status.json"
JPN_PROMOTION_REVIEW = FOOTBALL / "manifests" / "jpn_j1_promotion_review_v467_status.json"
JPN_TRANSITION_MANIFEST_FROZEN_BLOB = "62ca43ef12929c03997e8842dd01f99590bdb9d1"
JPN_PROMOTION_REVIEW_FROZEN_BLOB = "0789c73d6760ac7905dee822691b3487243ff7c5"
EXPECTED_JPN_STAGE_COUNTS = {
    "transition_regional_east": 90,
    "transition_regional_west": 90,
    "transition_playoff_round": 20,
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_git_blob_sha(path: Path) -> str:
    """Compute the committed-text Git blob identity independent of CRLF checkout conversion."""
    content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _capability_adjudication(source_path: str) -> dict[str, Any] | None:
    if not CAP_ADJ.is_file():
        return None
    payload = load_json(CAP_ADJ)
    for row in payload.get("entries", []):
        if isinstance(row, dict) and row.get("source_path") == source_path:
            return row
    return None


def _audit_jpn_transition_evidence(registry_row: dict[str, Any]) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    missing = [
        path.relative_to(ROOT).as_posix()
        for path in (JPN_TRANSITION_MANIFEST, JPN_PROMOTION_REVIEW)
        if not path.is_file()
    ]
    if missing:
        return {
            "status": "FAIL",
            "evidence_mode": "FROZEN_COMMITTED_MANIFESTS",
            "untracked_work_products_required": False,
            "errors": [{"code": "jpn_transition_frozen_evidence_missing", "paths": missing}],
        }

    transition_blob = _canonical_git_blob_sha(JPN_TRANSITION_MANIFEST)
    promotion_blob = _canonical_git_blob_sha(JPN_PROMOTION_REVIEW)
    official = load_json(JPN_TRANSITION_MANIFEST)
    review = load_json(JPN_PROMOTION_REVIEW)
    review_route = review.get("official_transition_route") or {}
    deployment_gate = review.get("target_season_deployment_gate") or {}
    review_checks = review.get("checks") or {}
    official_audit = official.get("audit") or {}

    def require(condition: bool, code: str, **extra: Any) -> None:
        if not condition:
            errors.append({"code": code, **extra})

    response_sha = str(official.get("response_sha256") or "")
    source = str(official.get("source") or "")
    official_stage_counts = official_audit.get("stage_counts") or {}

    require(transition_blob == JPN_TRANSITION_MANIFEST_FROZEN_BLOB,
            "jpn_transition_manifest_frozen_blob_mismatch",
            expected=JPN_TRANSITION_MANIFEST_FROZEN_BLOB,
            actual=transition_blob)
    require(promotion_blob == JPN_PROMOTION_REVIEW_FROZEN_BLOB,
            "jpn_promotion_review_frozen_blob_mismatch",
            expected=JPN_PROMOTION_REVIEW_FROZEN_BLOB,
            actual=promotion_blob)
    require(registry_row.get("official_transition_route_status") == "OFFICIAL_TRANSITION_ROUTE_VALIDATED",
            "jpn_registry_transition_status_invalid",
            actual=registry_row.get("official_transition_route_status"))
    require(official.get("status") == "OFFICIAL_TRANSITION_ROUTE_VALIDATED",
            "jpn_transition_manifest_status_invalid",
            actual=official.get("status"))
    require(official.get("competition_id") == "JPN_J1", "jpn_transition_manifest_competition_invalid")
    require(official.get("season") == "2026_special", "jpn_transition_manifest_season_invalid")
    require(source.startswith("https://data.j-league.or.jp/"),
            "jpn_transition_manifest_source_not_official",
            source=source)
    require(bool(re.fullmatch(r"[0-9a-fA-F]{64}", response_sha)),
            "jpn_transition_manifest_response_sha_invalid",
            response_sha256=response_sha)
    require(official.get("processed_path") == "processed/JPN_J1/official_2026_special.csv",
            "jpn_transition_manifest_processed_path_invalid",
            actual=official.get("processed_path"))
    require(official.get("transition_season_is_separate_domain") is True,
            "jpn_transition_manifest_separate_domain_false")
    require(official.get("must_not_pool_into_2026_27_target_season") is True,
            "jpn_transition_manifest_pooling_guard_false")
    require(official.get("settlement_scope") == "90_minutes_including_stoppage",
            "jpn_transition_manifest_settlement_scope_invalid")
    require(official_audit.get("match_count") == 200,
            "jpn_transition_manifest_match_count_invalid",
            actual=official_audit.get("match_count"))
    require(official_audit.get("team_count") == 20,
            "jpn_transition_manifest_team_count_invalid",
            actual=official_audit.get("team_count"))
    require(official_stage_counts == EXPECTED_JPN_STAGE_COUNTS,
            "jpn_transition_manifest_stage_counts_invalid",
            actual=official_stage_counts)
    require(official_audit.get("probability_input_score_scope") == "90_minute_scores_only",
            "jpn_transition_manifest_probability_scope_invalid")
    require(official_audit.get("penalty_shootout_used_for_formal_score") is False,
            "jpn_transition_manifest_penalty_scope_invalid")

    require(review.get("competition_id") == "JPN_J1", "jpn_promotion_review_competition_invalid")
    require(review.get("formal_weight") == 0, "jpn_promotion_review_formal_weight_nonzero")
    require(review.get("automatic_promotion") is False, "jpn_promotion_review_automatic_promotion_enabled")
    require(registry_row.get("promotion_review_status") == review.get("status"),
            "jpn_registry_promotion_review_status_mismatch",
            registry=registry_row.get("promotion_review_status"),
            manifest=review.get("status"))
    require(review_route.get("status") == official.get("status"),
            "jpn_transition_crosscheck_status_mismatch")
    require(review_route.get("season") == official.get("season"),
            "jpn_transition_crosscheck_season_mismatch")
    require(review_route.get("match_count") == official_audit.get("match_count"),
            "jpn_transition_crosscheck_match_count_mismatch")
    require(review_route.get("stage_counts") == official_stage_counts,
            "jpn_transition_crosscheck_stage_counts_mismatch")
    require(review_route.get("settlement_scope") == official.get("settlement_scope"),
            "jpn_transition_crosscheck_settlement_scope_mismatch")
    require(review_route.get("separate_domain") is True,
            "jpn_transition_crosscheck_separate_domain_false")
    require(deployment_gate.get("special_2026_may_supply_2026_27_team_strength") is False,
            "jpn_transition_deployment_pooling_guard_false")
    require(review_checks.get("official_2026_special_route_validated") is True,
            "jpn_transition_review_validation_check_false")
    require(review_checks.get("special_transition_kept_separate") is True,
            "jpn_transition_review_separation_check_false")

    return {
        "status": "PASS" if not errors else "FAIL",
        "evidence_mode": "FROZEN_COMMITTED_MANIFESTS",
        "untracked_work_products_required": False,
        "transition_manifest_frozen_blob": transition_blob,
        "promotion_review_frozen_blob": promotion_blob,
        "official_source": source,
        "official_response_sha256": response_sha,
        "match_count": official_audit.get("match_count"),
        "stage_counts": official_stage_counts,
        "transition_manifest": JPN_TRANSITION_MANIFEST.relative_to(ROOT).as_posix(),
        "promotion_review_manifest": JPN_PROMOTION_REVIEW.relative_to(ROOT).as_posix(),
        "errors": errors,
    }


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
    jpn_transition_evidence: dict[str, Any] | None = None
    if jpn.get("official_transition_route_status") == "OFFICIAL_TRANSITION_ROUTE_VALIDATED":
        jpn_transition_evidence = _audit_jpn_transition_evidence(jpn)
        if jpn_transition_evidence["status"] != "PASS":
            errors.extend(jpn_transition_evidence["errors"])

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
        "schema_version": "V4.7.2-repository-asset-topology-governance-aware-r4",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "hard_error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
        "registered_competition_count": len(competitions),
        "registered_competition_profiles_present": len(competitions) - len(missing_profiles),
        "batch001_workflow_authority": locals().get("batch001_authority", "INVALID"),
        "jpn_transition_evidence": jpn_transition_evidence,
        "reconciliation_summary": reconciliation_summary,
        "formal_weight_change": False,
        "automatic_promotion": False,
        "policy": (
            "Repository asset topology only; governance-archived STATIC_REFERENCE_ONLY workflows are not required to remain active. "
            "JPN 2026-special clean-checkout validation is bound to exact frozen manifest blobs plus official-source/response hashes rather than untracked work products. "
            "No CURRENT or formal model weight changes."
        ),
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
        jpn_evidence = report.get("jpn_transition_evidence") or {}
        print(json.dumps({
            "status": report["status"],
            "hard_error_count": report["hard_error_count"],
            "warning_count": report["warning_count"],
            "registered_competition_profiles_present": report["registered_competition_profiles_present"],
            "registered_competition_count": report["registered_competition_count"],
            "batch001_workflow_authority": report["batch001_workflow_authority"],
            "jpn_transition_evidence_status": jpn_evidence.get("status"),
            "jpn_transition_evidence_mode": jpn_evidence.get("evidence_mode"),
            "errors": report["errors"],
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
