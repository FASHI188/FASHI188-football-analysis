#!/usr/bin/env python3
"""Acceptance checks for the non-destructive Phase-2A governance deliverables."""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GOVERNANCE = ROOT / "governance"


def load(name: str):
    return json.loads((GOVERNANCE / name).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    inventory = load("workflow_inventory.json")
    summary = load("workflow_inventory_summary.json")
    migration = load("legacy_workflow_migration_plan.json")
    pr_ledger = load("pr_receipt_extraction_ledger.json")
    legacy_policy = load("legacy_workflow_policy_report.json")
    skeleton_policy = load("safe_skeleton_policy_report.json")

    workflows = inventory["workflows"]
    require(len(workflows) == 411, "inventory must contain exactly 411 workflows")
    require(summary["workflow_count"] == 411, "summary workflow count mismatch")
    require(summary["zero_unknown_gate"] is True, "zero-UNKNOWN gate must pass")
    require(summary["unresolved_path_count"] == 0, "unresolved paths remain")
    require(summary["unknown_trigger_count"] == 0, "unknown triggers remain")
    require(summary["unknown_permission_count"] == 0, "unknown permissions remain")
    require(summary["unknown_persistence_count"] == 0, "unknown persistence remains")
    require(summary["yaml_parse_failure_count"] == 0, "YAML parse failures remain")
    require(
        all(
            record["disposition"]
            in {"KEEP", "CONSOLIDATE", "MANUAL_ONLY", "ARCHIVE"}
            for record in workflows
        ),
        "invalid disposition remains",
    )
    dispositions = Counter(record["disposition"] for record in workflows)
    require(
        dict(sorted(dispositions.items())) == summary["disposition_counts"],
        "disposition summary is not reproducible",
    )
    require(
        sum(summary["disposition_counts"].values()) == 411,
        "disposition counts do not sum to 411",
    )

    with (GOVERNANCE / "workflow_inventory.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        csv_rows = list(csv.DictReader(handle))
    require(len(csv_rows) == 411, "CSV must contain exactly 411 workflow rows")
    require(
        {row["path"] for row in csv_rows}
        == {record["path"] for record in workflows},
        "CSV and JSON path sets differ",
    )

    migrations = migration["migrations"]
    require(len(migrations) == 411, "migration plan must cover 411 workflows")
    require(
        {item["source_path"] for item in migrations}
        == {record["path"] for record in workflows},
        "migration plan and inventory path sets differ",
    )
    batches = Counter(
        item["proposed_batch"]
        for item in migrations
        if item["proposed_batch"].startswith("SAFE_ARCHIVE_BATCH_")
    )
    require(
        all(count <= 25 for count in batches.values()),
        "an archive batch exceeds 25 workflows",
    )
    require(
        all(
            not item["unique_script_dependencies"]
            and not item["result_or_receipt_dependencies"]
            for item in migrations
            if item["proposed_batch"] == "SAFE_ARCHIVE_BATCH_01"
        ),
        "first archive batch contains unique dependencies",
    )

    require(pr_ledger["pr_count"] == 18, "PR ledger must cover 18 PRs")
    require(
        {record["pr_number"] for record in pr_ledger["records"]}
        == {7, *range(13, 30)},
        "PR ledger number set mismatch",
    )
    require(
        all(record["head_matches_record"] for record in pr_ledger["records"]),
        "a PR head does not match its recorded SHA",
    )
    require(
        all(record["receipt_exists"] for record in pr_ledger["records"]),
        "an expected PR receipt is missing",
    )
    require(pr_ledger["jobs_rerun"] == 0, "PR jobs were rerun")
    require(pr_ledger["prs_closed"] == 0, "PRs were closed")
    require(pr_ledger["prs_merged"] == 0, "PRs were merged")

    require(
        legacy_policy["checked_workflow_count"] == 411,
        "legacy policy report must cover 411 workflows",
    )
    require(
        skeleton_policy["checked_workflow_count"] == 5,
        "skeleton policy report must cover five workflows",
    )
    require(
        skeleton_policy["violation_count"] == 0,
        "safe skeleton policy report contains violations",
    )

    skeletons = {
        ".github/workflows/ci.yml",
        ".github/workflows/research.yml",
        ".github/workflows/scheduled-data.yml",
        ".github/workflows/forward.yml",
        ".github/workflows/maintenance.yml",
    }
    for relative in skeletons:
        text = (ROOT / relative).read_text(encoding="utf-8")
        require(
            "PAUSED_FOR_REPOSITORY_GOVERNANCE" in text,
            f"pause marker missing from {relative}",
        )

    report = {
        "phase2a_acceptance": "PASS",
        "workflow_count": 411,
        "zero_unknown_gate": True,
        "disposition_counts": summary["disposition_counts"],
        "legacy_policy_violating_workflows": legacy_policy[
            "violating_workflow_count"
        ],
        "safe_skeleton_violations": skeleton_policy["violation_count"],
        "pr_count": pr_ledger["pr_count"],
        "logical_task_count": pr_ledger["logical_task_count"],
        "duplicate_canonical_receipt_count": pr_ledger[
            "duplicate_canonical_receipt_count"
        ],
        "safe_archive_count": migration["safe_archive_count"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
