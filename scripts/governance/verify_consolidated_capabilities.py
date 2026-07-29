#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ADJ = ROOT / "governance" / "consolidated_capability_adjudication.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows"
SUMMARY = {"ci.yml", "forward.yml", "maintenance.yml", "research.yml", "scheduled-data.yml"}
ALLOWED = {"EXECUTION_RETAINED", "STATIC_REFERENCE_ONLY", "RETIRED_ARCHIVE_ONLY"}
EXPECTED_COUNTS = {
    "EXECUTION_RETAINED": 5,
    "STATIC_REFERENCE_ONLY": 34,
    "RETIRED_ARCHIVE_ONLY": 15,
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(SUMMARY))
    parser.add_argument("--source")
    parser.add_argument("--require-archived", action="store_true")
    args = parser.parse_args()

    plan = load(PLAN)
    adjudication = load(ADJ)
    plan_rows = [row for row in plan["migrations"] if row["disposition"] == "CONSOLIDATE"]
    adjudicated = adjudication.get("entries", [])
    errors: list[str] = []

    if len(plan_rows) != 54:
        errors.append(f"consolidate_count={len(plan_rows)} expected=54")
    if len(adjudicated) != 54:
        errors.append(f"adjudication_count={len(adjudicated)} expected=54")

    plan_by_source = {row["source_path"]: row for row in plan_rows}
    adj_by_source = {row["source_path"]: row for row in adjudicated}
    if set(plan_by_source) != set(adj_by_source):
        errors.append(
            "adjudication source set differs from frozen CONSOLIDATE set: "
            f"missing={sorted(set(plan_by_source)-set(adj_by_source))} "
            f"extra={sorted(set(adj_by_source)-set(plan_by_source))}"
        )

    counts = collections.Counter(row.get("adjudication") for row in adjudicated)
    for state in counts:
        if state not in ALLOWED:
            errors.append(f"unknown adjudication state: {state}")
    if {k: counts.get(k, 0) for k in EXPECTED_COUNTS} != EXPECTED_COUNTS:
        errors.append(f"adjudication counts drift: {dict(counts)} expected={EXPECTED_COUNTS}")

    selected_sources = set(plan_by_source)
    if args.target:
        selected_sources = {
            source
            for source, row in plan_by_source.items()
            if args.target in row.get("target_workflow", "").split("/")
            or adj_by_source[source].get("execution_owner") == args.target
        }
    if args.source:
        selected_sources &= {args.source}
        if args.source not in plan_by_source:
            errors.append(f"source is not frozen CONSOLIDATE: {args.source}")

    archived_by_name: dict[str, list[Path]] = collections.defaultdict(list)
    for candidate in [*ARCHIVE_ROOT.rglob("*.yml"), *ARCHIVE_ROOT.rglob("*.yaml")]:
        archived_by_name[candidate.name].append(candidate)

    missing_dependencies: set[str] = set()
    syntax_errors: list[str] = []
    archive_errors: list[str] = []
    owner_reference_errors: list[str] = []
    execution_reference_verified = 0
    selected_rows = []

    for source in sorted(selected_sources):
        plan_row = plan_by_source[source]
        adj_row = adj_by_source[source]
        state = adj_row["adjudication"]
        src_path = ROOT / source
        if src_path.exists():
            archive_errors.append(f"legacy CONSOLIDATE workflow still active: {source}")

        copies = archived_by_name.get(Path(source).name, [])
        if args.require_archived and len(copies) != 1:
            archive_errors.append(f"archive copy count {len(copies)} for {source}: {[str(p.relative_to(ROOT)) for p in copies]}")

        if state != "RETIRED_ARCHIVE_ONLY":
            for dep in plan_row.get("unique_script_dependencies", []):
                dep_path = ROOT / dep
                if not dep_path.exists():
                    missing_dependencies.add(dep)
                    continue
                if dep_path.suffix == ".py":
                    try:
                        compile(dep_path.read_text(encoding="utf-8"), str(dep_path), "exec")
                    except Exception as exc:
                        syntax_errors.append(f"{dep}: {exc}")

        if state == "EXECUTION_RETAINED":
            owner = adj_row.get("execution_owner")
            refs = adj_row.get("execution_references") or []
            if owner not in SUMMARY:
                owner_reference_errors.append(f"invalid execution owner for {source}: {owner}")
            elif not refs:
                owner_reference_errors.append(f"missing execution references for {source}")
            else:
                owner_path = ROOT / ".github" / "workflows" / owner
                if not owner_path.exists():
                    owner_reference_errors.append(f"missing owner {owner} for {source}")
                else:
                    owner_text = owner_path.read_text(encoding="utf-8")
                    if re.search(r"(?m)^\s*contents:\s*write\s*$", owner_text):
                        owner_reference_errors.append(f"execution owner has contents:write: {owner}")
                    if re.search(r"\bgit\s+(?:commit|push)\b|persist_generated_worktree|persist_files_v474\.py", owner_text, re.I):
                        owner_reference_errors.append(f"execution owner contains repository persistence path: {owner}")
                    ref_ok = True
                    for ref in refs:
                        ref_path = ROOT / ref
                        if not ref_path.exists():
                            owner_reference_errors.append(f"execution reference missing from repository: {ref}")
                            ref_ok = False
                            continue
                        if ref not in owner_text:
                            owner_reference_errors.append(f"owner {owner} does not directly reference {ref} for {source}")
                            ref_ok = False
                        if not re.search(rf"python[^\n]*{re.escape(ref)}", owner_text):
                            owner_reference_errors.append(f"owner {owner} does not directly execute {ref} for {source}")
                            ref_ok = False
                    if ref_ok:
                        execution_reference_verified += 1

        selected_rows.append(
            {
                "source_path": source,
                "adjudication": state,
                "execution_owner": adj_row.get("execution_owner"),
                "archive_copy_count": len(copies),
            }
        )

    errors.extend(sorted(missing_dependencies))
    errors.extend(syntax_errors)
    errors.extend(archive_errors)
    errors.extend(owner_reference_errors)

    result = {
        "status": "PASS" if not errors else "FAIL",
        "claim": "PARTIAL_EXECUTION_RETENTION_WITH_EXPLICIT_RETIREMENT",
        "forbidden_claim": "CAPABILITY_CONSOLIDATED_54_OF_54",
        "frozen_consolidate_count": len(plan_rows),
        "adjudication_count": len(adjudicated),
        "adjudication_counts": {k: counts.get(k, 0) for k in sorted(ALLOWED)},
        "selected_count": len(selected_rows),
        "execution_owner_direct_reference_verified": execution_reference_verified,
        "target": args.target,
        "source": args.source,
        "require_archived": args.require_archived,
        "selected": selected_rows,
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
