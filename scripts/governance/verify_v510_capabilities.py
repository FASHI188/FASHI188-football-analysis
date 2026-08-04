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
HISTORICAL = ROOT / "governance" / "consolidated_capability_adjudication.json"
OVERLAY = ROOT / "governance" / "v510_capability_retirement.json"
ARCHIVE_ROOT = ROOT / "governance" / "archive" / "workflows"
SUMMARY = {"ci.yml", "forward.yml", "maintenance.yml", "research.yml", "scheduled-data.yml"}
ALLOWED = {"EXECUTION_RETAINED", "STATIC_REFERENCE_ONLY", "RETIRED_ARCHIVE_ONLY"}
EXPECTED_COUNTS = {
    "EXECUTION_RETAINED": 2,
    "STATIC_REFERENCE_ONLY": 33,
    "RETIRED_ARCHIVE_ONLY": 19,
}
RETAINED_COMMANDS: dict[str, list[str]] = {
    ".github/workflows/football-runtime-maintenance-v473.yml": [
        "football-data/validation/runtime_maintenance_v473.py",
        "--strict-exit",
        "--print-summary",
    ],
    ".github/workflows/football-workflow-stability-v474.yml": [
        "football-data/validation/workflow_stability_audit_v474.py",
        "--strict-exit",
        "--print-summary",
    ],
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_retained(source: str, command: list[str]) -> dict[str, object]:
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
    parser.add_argument("--execute-retained", action="store_true")
    args = parser.parse_args()

    plan = load(PLAN)
    historical = load(HISTORICAL)
    overlay = load(OVERLAY)
    plan_rows = [row for row in plan["migrations"] if row["disposition"] == "CONSOLIDATE"]
    historical_rows = historical.get("entries", [])
    errors: list[str] = []

    if len(plan_rows) != 54:
        errors.append(f"consolidate_count={len(plan_rows)} expected=54")
    if len(historical_rows) != 54:
        errors.append(f"historical_adjudication_count={len(historical_rows)} expected=54")

    plan_by_source = {row["source_path"]: row for row in plan_rows}
    historical_by_source = {row["source_path"]: dict(row) for row in historical_rows}
    if set(plan_by_source) != set(historical_by_source):
        errors.append("historical adjudication source set differs from frozen CONSOLIDATE set")

    effective_by_source = {source: dict(row) for source, row in historical_by_source.items()}
    overlay_rows = overlay.get("retirements", [])
    overlay_sources: set[str] = set()
    for row in overlay_rows:
        source = row.get("source_path")
        if source in overlay_sources:
            errors.append(f"duplicate overlay source: {source}")
            continue
        overlay_sources.add(source)
        if source not in effective_by_source:
            errors.append(f"overlay source is not frozen CONSOLIDATE: {source}")
            continue
        state = row.get("effective_adjudication")
        if state != "RETIRED_ARCHIVE_ONLY":
            errors.append(f"overlay may only retire capability: {source} -> {state}")
            continue
        effective_by_source[source]["adjudication"] = state
        effective_by_source[source]["reason_code"] = row.get("reason_code")
        effective_by_source[source].pop("execution_owner", None)
        effective_by_source[source].pop("execution_references", None)

    if len(overlay_sources) != 4:
        errors.append(f"overlay retirement count={len(overlay_sources)} expected=4")

    counts = collections.Counter(row.get("adjudication") for row in effective_by_source.values())
    for state in counts:
        if state not in ALLOWED:
            errors.append(f"unknown effective adjudication state: {state}")
    effective_counts = {key: counts.get(key, 0) for key in EXPECTED_COUNTS}
    if effective_counts != EXPECTED_COUNTS:
        errors.append(f"effective counts drift: {effective_counts} expected={EXPECTED_COUNTS}")
    if overlay.get("effective_counts") != EXPECTED_COUNTS:
        errors.append("overlay declared counts drift")

    retained_sources = {
        source for source, row in effective_by_source.items() if row.get("adjudication") == "EXECUTION_RETAINED"
    }
    if retained_sources != set(RETAINED_COMMANDS):
        errors.append(
            "effective retained command map drift: "
            f"adjudicated={sorted(retained_sources)} commands={sorted(RETAINED_COMMANDS)}"
        )

    selected_sources = set(plan_by_source)
    if args.target:
        selected_sources = {
            source
            for source, row in plan_by_source.items()
            if args.target in row.get("target_workflow", "").split("/")
            or historical_by_source[source].get("execution_owner") == args.target
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
    owner_errors: list[str] = []
    execution_reference_verified = 0
    selected_rows: list[dict[str, object]] = []

    for source in sorted(selected_sources):
        plan_row = plan_by_source[source]
        effective = effective_by_source[source]
        state = effective["adjudication"]
        if (ROOT / source).exists():
            archive_errors.append(f"legacy CONSOLIDATE workflow still active: {source}")

        copies = archived_by_name.get(Path(source).name, [])
        if args.require_archived and len(copies) != 1:
            archive_errors.append(
                f"archive copy count {len(copies)} for {source}: {[str(p.relative_to(ROOT)) for p in copies]}"
            )

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
            owner = effective.get("execution_owner")
            refs = effective.get("execution_references") or []
            if owner not in SUMMARY:
                owner_errors.append(f"invalid execution owner for {source}: {owner}")
            elif not refs:
                owner_errors.append(f"missing execution references for {source}")
            else:
                owner_path = ROOT / ".github" / "workflows" / owner
                if not owner_path.exists():
                    owner_errors.append(f"missing owner {owner} for {source}")
                else:
                    owner_text = owner_path.read_text(encoding="utf-8")
                    if not re.search(r"(?m)^\s*contents:\s*read\s*$", owner_text):
                        owner_errors.append(f"execution owner lacks contents:read: {owner}")
                    if re.search(r"(?m)^\s*contents:\s*write\s*$", owner_text):
                        owner_errors.append(f"execution owner has contents:write: {owner}")
                    if re.search(r"\bgit\s+(?:commit|push)\b|persist_generated_worktree|persist_files_v474\.py|api\.github\.com/repos/.*/contents", owner_text, re.I):
                        owner_errors.append(f"execution owner contains persistence path: {owner}")
                    ref_ok = True
                    for ref in refs:
                        if not (ROOT / ref).exists():
                            owner_errors.append(f"execution reference missing: {ref}")
                            ref_ok = False
                            continue
                        if ref not in owner_text or not re.search(rf"python[^\n]*{re.escape(ref)}", owner_text):
                            owner_errors.append(f"owner {owner} does not directly execute {ref} for {source}")
                            ref_ok = False
                    if ref_ok:
                        execution_reference_verified += 1

        selected_rows.append(
            {
                "source_path": source,
                "historical_adjudication": historical_by_source[source].get("adjudication"),
                "effective_adjudication": state,
                "execution_owner": effective.get("execution_owner"),
                "archive_copy_count": len(copies),
            }
        )

    errors.extend(sorted(missing_dependencies))
    errors.extend(syntax_errors)
    errors.extend(archive_errors)
    errors.extend(owner_errors)

    execution_results: list[dict[str, object]] = []
    if args.execute_retained:
        for source in sorted(source for source in selected_sources if source in retained_sources):
            try:
                result = run_retained(source, RETAINED_COMMANDS[source])
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
                errors.append(f"retained capability execution failed: {source}")
    elif not args.target and not args.source:
        errors.append("full effective capability validation requires --execute-retained")

    result = {
        "status": "PASS" if not errors else "FAIL",
        "claim": "V510_EFFECTIVE_CAPABILITY_RETIREMENT_WITH_HISTORICAL_EVIDENCE_PRESERVED",
        "historical_adjudication_counts": historical.get("counts"),
        "effective_adjudication_counts": effective_counts,
        "overlay_retirement_count": len(overlay_sources),
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
