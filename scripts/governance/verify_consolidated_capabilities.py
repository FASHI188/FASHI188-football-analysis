#!/usr/bin/env python3
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "governance" / "legacy_workflow_migration_plan.json"
ADJ = ROOT / "governance" / "consolidated_capability_adjudication.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows"
SUMMARY = {"ci.yml", "forward.yml", "maintenance.yml", "research.yml", "scheduled-data.yml"}
ALLOWED = {"EXECUTION_RETAINED", "STATIC_REFERENCE_ONLY", "RETIRED_ARCHIVE_ONLY"}
EXPECTED_COUNTS = {
    "EXECUTION_RETAINED": 6,
    "STATIC_REFERENCE_ONLY": 33,
    "RETIRED_ARCHIVE_ONLY": 15,
}
RETAINED_COMMANDS: dict[str, list[str]] = {
    ".github/workflows/football-runtime-maintenance-v473.yml": [
        "football-data/validation/runtime_maintenance_v473.py",
        "--strict-exit",
        "--print-summary",
    ],
    ".github/workflows/football-v470-formal-next-season-parameter-rollforward.yml": [
        "football-data/validation/formal_next_season_parameter_rollforward_v470.py",
    ],
    ".github/workflows/football-v470-formal-next-season-runtime-readiness.yml": [
        "football-data/validation/formal_next_season_runtime_readiness_v470.py",
    ],
    ".github/workflows/football-v6852-kambi-formal-domain-coverage.yml": [
        "football-data/validation/v6_kambi_formal_domain_coverage_v6852.py",
    ],
    ".github/workflows/football-workflow-stability-v474.yml": [
        "football-data/validation/workflow_stability_audit_v474.py",
        "--strict-exit",
        "--print-summary",
    ],
    ".github/workflows/football-v501-formal-prediction-queue.yml": [
        "football-data/validation/formal_prediction_queue_v501.py",
    ],
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _run_retained(source: str, command: list[str]) -> dict[str, object]:
    env = dict(os.environ)
    pythonpath = [str(ROOT / "football-data" / "engine"), str(ROOT / "football-data" / "validation")]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, *command],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=300,
    )
    return {
        "source_path": source,
        "command": [sys.executable, *command],
        "return_code": proc.returncode,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "stdout_tail": proc.stdout[-12000:],
        "stderr_tail": proc.stderr[-12000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(SUMMARY))
    parser.add_argument("--source")
    parser.add_argument("--require-archived", action="store_true")
    parser.add_argument(
        "--execute-retained",
        action="store_true",
        help="Actually execute every selected EXECUTION_RETAINED capability in this ephemeral checkout.",
    )
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

    retained_sources = {
        row["source_path"] for row in adjudicated if row.get("adjudication") == "EXECUTION_RETAINED"
    }
    if retained_sources != set(RETAINED_COMMANDS):
        errors.append(
            "retained execution command map drift: "
            f"adjudicated={sorted(retained_sources)} commands={sorted(RETAINED_COMMANDS)}"
        )

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
                    if not re.search(r"(?m)^\s*contents:\s*read\s*$", owner_text):
                        owner_reference_errors.append(f"execution owner lacks explicit contents:read: {owner}")
                    if re.search(r"(?m)^\s*contents:\s*write\s*$", owner_text):
                        owner_reference_errors.append(f"execution owner has contents:write: {owner}")
                    if re.search(r"\bgit\s+(?:commit|push)\b|persist_generated_worktree|persist_files_v474\.py|api\.github\.com/repos/.*/contents", owner_text, re.I):
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

    execution_results: list[dict[str, object]] = []
    if args.execute_retained:
        selected_retained = sorted(source for source in selected_sources if source in retained_sources)
        for source in selected_retained:
            try:
                result = _run_retained(source, RETAINED_COMMANDS[source])
            except Exception as exc:
                result = {
                    "source_path": source,
                    "command": [sys.executable, *RETAINED_COMMANDS[source]],
                    "return_code": None,
                    "status": "FAIL",
                    "stdout_tail": "",
                    "stderr_tail": f"{type(exc).__name__}: {exc}",
                }
            execution_results.append(result)
            if result["status"] != "PASS":
                errors.append(
                    f"retained capability execution failed: {source} return_code={result.get('return_code')}"
                )
    elif not args.target and not args.source:
        errors.append("full capability validation requires --execute-retained; static owner text alone is insufficient")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "claim": "PARTIAL_EXECUTION_RETENTION_WITH_EXPLICIT_RETIREMENT",
        "forbidden_claim": "CAPABILITY_CONSOLIDATED_54_OF_54",
        "frozen_consolidate_count": len(plan_rows),
        "adjudication_count": len(adjudicated),
        "adjudication_counts": {k: counts.get(k, 0) for k in sorted(ALLOWED)},
        "selected_count": len(selected_rows),
        "execution_owner_direct_reference_verified": execution_reference_verified,
        "retained_execution_required": bool(args.execute_retained),
        "retained_execution_count": len(execution_results),
        "retained_execution_pass_count": sum(1 for row in execution_results if row.get("status") == "PASS"),
        "retained_execution_results": execution_results,
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
