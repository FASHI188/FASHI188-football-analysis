#!/usr/bin/env python3
"""V4.7.3 runtime wrapper around the V4.7.1 repository integrity audit.

The underlying V4.7.1 scanner remains strict. This wrapper recognizes only a
small, explicit set of immutable migration/reconciliation provenance artifacts
as historical references to the retired source repository. It also resolves the
known Windows checkout CRLF false-negative for the hash-bound formal engine by
verifying the repository-text (LF-normalized) SHA256 before suppressing only the
two engine-byte findings caused by line-ending conversion.

For workflows that intentionally execute Python only after an exact checkout of
another trusted ref, missing-in-current-tree references are accepted only when
the workflow mechanically proves the complete cross-checkout contract: a fixed
GitHub ref is resolved by API, the returned value is constrained to a 40-hex
commit SHA, checkout uses that step output with credentials disabled, HEAD is
verified equal to that SHA, and every external Python reference is py-compiled
after HEAD verification before any execution. Any incomplete contract remains a
hard repository-integrity failure.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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

PY_PATH_TOKEN_RE = re.compile(r"\b(football-data/[A-Za-z0-9_./-]+\.py)\b")
CHECKOUT_REF_OUTPUT_RE = re.compile(
    r"ref:\s*\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}"
)
FIXED_GIT_REF_API_RE = re.compile(r"/git/ref/heads/([A-Za-z0-9%._/-]+)")
STEP_START_RE = re.compile(r"^      -\s+(?:name:|uses:)", re.MULTILINE)
HEX_SHA_GUARD = "^[0-9a-f]{40}$"


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


def _workflow_steps(text: str) -> list[tuple[int, int, str]]:
    """Return GitHub Actions job-step spans without interpreting shell block contents."""
    starts = [m.start() for m in STEP_START_RE.finditer(text)]
    return [
        (
            start,
            starts[index + 1] if index + 1 < len(starts) else len(text),
            text[start:(starts[index + 1] if index + 1 < len(starts) else len(text))],
        )
        for index, start in enumerate(starts)
    ]


def _source_step(
    steps: list[tuple[int, int, str]],
    checkout_index: int,
    source_id: str,
) -> tuple[int, int, str] | None:
    pattern = re.compile(rf"(?m)^\s+id:\s*{re.escape(source_id)}\s*$")
    for step in reversed(steps[:checkout_index]):
        if pattern.search(step[2]):
            return step
    return None


def _validate_cross_checkout_reference(workflow_text: str, path_text: str) -> dict[str, Any] | None:
    """Mechanically prove that one missing local Python path is safe only after exact checkout."""
    steps = _workflow_steps(workflow_text)
    if not steps:
        return None

    for checkout_index, checkout in enumerate(steps):
        block = checkout[2]
        if "uses: actions/checkout@" not in block:
            continue
        ref_match = CHECKOUT_REF_OUTPUT_RE.search(block)
        if not ref_match or "persist-credentials: false" not in block:
            continue

        source_id, output_name = ref_match.groups()
        source = _source_step(steps, checkout_index, source_id)
        if source is None:
            continue
        source_block = source[2]

        api_match = FIXED_GIT_REF_API_RE.search(source_block)
        if not api_match:
            continue
        canonical_ref = unquote(api_match.group(1))
        if not canonical_ref or any(token in canonical_ref for token in ("$", "{", "}")):
            continue
        if HEX_SHA_GUARD not in source_block:
            continue
        if not re.search(rf"echo\s+[\"']?{re.escape(output_name)}=", source_block):
            continue
        if "GITHUB_OUTPUT" not in source_block:
            continue

        verify_index = None
        verify = None
        expected_expr = f"${{{{ steps.{source_id}.outputs.{output_name} }}}}"
        for index in range(checkout_index + 1, len(steps)):
            candidate = steps[index]
            candidate_block = candidate[2]
            if "git rev-parse HEAD" in candidate_block and expected_expr in candidate_block:
                verify_index = index
                verify = candidate
                break
        if verify_index is None or verify is None:
            continue

        verify_block = verify[2]
        head_pos = verify_block.find("git rev-parse HEAD")
        path_pos = verify_block.find(path_text)
        compile_pos = verify_block.find("-m py_compile")
        if head_pos < 0 or compile_pos < 0 or path_pos < 0:
            continue
        if not (head_pos < compile_pos <= path_pos):
            continue

        verify_global_pos = verify[0] + head_pos
        direct_refs = [
            match.start()
            for match in BASE.PY_REF_RE.finditer(workflow_text)
            if match.group(1) == path_text
        ]
        if direct_refs and min(direct_refs) < verify_global_pos:
            continue

        return {
            "path": path_text,
            "canonical_ref": canonical_ref,
            "source_step_id": source_id,
            "source_output": output_name,
            "checkout_ref_expression": expected_expr,
            "persist_credentials": False,
            "sha_guard": HEX_SHA_GUARD,
            "head_verified_before_compile": True,
            "compile_verified_before_execution": True,
        }
    return None


def _audit_cross_checkout_workflows() -> tuple[dict[tuple[str, str], dict[str, Any]], list[dict[str, Any]]]:
    """Audit every workflow with missing local Python paths and a dynamic exact checkout."""
    validated: dict[tuple[str, str], dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    workflow_files = sorted(list(BASE.WORKFLOWS.glob("*.yml")) + list(BASE.WORKFLOWS.glob("*.yaml")))

    for workflow in workflow_files:
        text = workflow.read_text(encoding="utf-8")
        if "uses: actions/checkout@" not in text or not CHECKOUT_REF_OUTPUT_RE.search(text):
            continue
        workflow_rel = BASE.rel(workflow)
        missing_paths = sorted({
            path_text
            for path_text in PY_PATH_TOKEN_RE.findall(text)
            if not (BASE.ROOT / path_text).is_file()
        })
        for path_text in missing_paths:
            contract = _validate_cross_checkout_reference(text, path_text)
            key = (workflow_rel, path_text)
            if contract is None:
                failures.append({
                    "code": "workflow_cross_checkout_python_reference_unverified",
                    "message": "workflow references Python outside the current tree without a complete exact-checkout fail-closed contract",
                    "workflow": workflow_rel,
                    "path": path_text,
                })
            else:
                validated[key] = {
                    "workflow": workflow_rel,
                    **contract,
                }
    return validated, failures


def audit() -> dict[str, Any]:
    report = BASE.audit()
    filtered_errors: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    engine_binding = _engine_hash_binding()
    validated_cross_checkout, cross_checkout_failures = _audit_cross_checkout_workflows()
    cross_checkout_failure_keys = {
        (str(item.get("workflow") or ""), str(item.get("path") or ""))
        for item in cross_checkout_failures
    }

    for item in report.get("errors", []):
        code = item.get("code")
        if code in ENGINE_LINE_ENDING_CODES and engine_binding["repository_text_hash_matches"]:
            suppressed.append({
                "code": "windows_crlf_engine_hash_false_negative_suppressed",
                "original_code": code,
                "reason": "LF-normalized repository text hash exactly matches the frozen formal-engine SHA256",
            })
            continue

        if code == "workflow_missing_python_reference":
            key = (str(item.get("workflow") or ""), str(item.get("path") or ""))
            if key in validated_cross_checkout:
                suppressed.append({
                    "code": "validated_exact_cross_checkout_python_reference",
                    **validated_cross_checkout[key],
                    "reason": "reference is absent from the current tree but is mechanically compiled only after an API-resolved exact trusted checkout and HEAD equality check",
                })
                continue
            if key in cross_checkout_failure_keys:
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

    filtered_errors.extend(cross_checkout_failures)

    report["errors"] = filtered_errors
    report["hard_error_count"] = len(filtered_errors)
    report["status"] = "PASS" if not filtered_errors else "FAIL"
    details = report.setdefault("details", {})
    details["historical_legacy_provenance_allowlist"] = {
        "allowed_paths": sorted(ALLOWED_HISTORICAL_LEGACY_REFERENCES),
        "suppressed_findings": [
            row for row in suppressed if row.get("code") == "historical_legacy_provenance_allowed"
        ],
        "policy": "Only immutable migration/reconciliation provenance may reference the retired source repository; active runtime authority may not.",
    }
    details["formal_engine_repository_text_binding"] = {
        **engine_binding,
        "suppressed_findings": [
            row for row in suppressed if row.get("code") == "windows_crlf_engine_hash_false_negative_suppressed"
        ],
        "policy": "Only line-ending byte drift may be normalized. Any LF-normalized hash mismatch remains a hard failure.",
    }
    details["exact_cross_checkout_python_references"] = {
        "validated": [validated_cross_checkout[key] for key in sorted(validated_cross_checkout)],
        "failures": cross_checkout_failures,
        "policy": (
            "A Python path absent from the current repository tree is valid only when the workflow resolves a fixed GitHub ref to a 40-hex commit SHA, "
            "checks out that step output with persist-credentials=false, verifies git HEAD equals the resolved SHA, and py-compiles the path after that "
            "verification before any direct execution. Missing files, invalid SHA provenance, checkout drift, or ordering errors remain hard failures."
        ),
    }
    report["schema_version"] = "V4.7.3-repository-integrity-runtime-wrapper-r3"
    report["policy"] = (
        "Engineering integrity only. Historical migration provenance is explicitly separated from active runtime authority. "
        "Formal-engine identity is checked using LF-normalized repository text so Windows CRLF checkout cannot create a false mismatch. "
        "Cross-checkout Python dependencies are accepted only after a complete exact-SHA fail-closed workflow contract is mechanically proven. "
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
            "exact_cross_checkout_python_references": report.get("details", {}).get("exact_cross_checkout_python_references"),
        }, ensure_ascii=False, indent=2))
    return 2 if args.strict_exit and report["status"] != "PASS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
