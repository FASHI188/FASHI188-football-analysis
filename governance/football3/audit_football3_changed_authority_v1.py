#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from typing import Any

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SCIENCE_DIR = REPO_ROOT / "football-data" / "research"
if str(SCIENCE_DIR) not in sys.path:
    sys.path.insert(0, str(SCIENCE_DIR))

import audit_football3_changed_scientific_files as scientific

TRANSPORT_PREFIX = "football-data/formal_gpt_gateway_v1/"
TRANSPORT_MARKER = "FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT"
TRANSPORT_MARKER_VALUE = "football3-formal-gpt-request-transport-v1"
ALLOWED_RUNTIME_ATTRIBUTES = {"FORMAL_SCOPE", "_parse_dt", "_normalize_team"}
FORBIDDEN_IMPORT_FRAGMENTS = ("new_engine_v1", "historical_xg_fusion_v2", "historical_xg_challenger", "formal_fusion_v2")
FORBIDDEN_CALL_NAMES = {"fit", "fit_transform", "predict", "predict_proba", "score", "train"}
FORBIDDEN_TRANSPORT_CONSTANT_NAMES = {"FORMAL_HEAD", "CURRENT_SHA256", "FUSION_WEIGHTS", "EVIDENCE_THRESHOLD"}


def _constant(tree: ast.Module, name: str) -> Any:
    values: list[Any] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == name for t in targets):
            continue
        if isinstance(node.value, ast.Constant):
            values.append(node.value.value)
    return values[0] if len(values) == 1 else None


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def transport_blockers(file_name: str) -> list[str]:
    path = REPO_ROOT / file_name
    blockers: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=file_name)
    except Exception as exc:
        return [f"{file_name}: PRODUCTION_TRANSPORT_SYNTAX_INVALID:{exc}"]
    if _constant(tree, TRANSPORT_MARKER) != TRANSPORT_MARKER_VALUE:
        blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_MARKER_MISSING")
    for forbidden in FORBIDDEN_TRANSPORT_CONSTANT_NAMES:
        if _constant(tree, forbidden) is not None:
            blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_SCIENTIFIC_CONSTANT_FORBIDDEN:{forbidden}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(fragment in alias.name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                    blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_SCIENTIFIC_IMPORT_FORBIDDEN:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if any(fragment in name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_SCIENTIFIC_IMPORT_FORBIDDEN:{name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_CALL_NAMES:
                blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_SCIENTIFIC_CALL_FORBIDDEN:{name}")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rt":
            if node.attr not in ALLOWED_RUNTIME_ATTRIBUTES:
                blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_RUNTIME_ATTRIBUTE_FORBIDDEN:{node.attr}")
    for reason in scientific.dynamic_authority_blockers(path):
        blockers.append(f"{file_name}: AST_DYNAMIC_AUTHORITY_DENIED:{reason}")
    if scientific.contract_constant(path) or scientific.helper_contract_constant(path):
        blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_MUST_NOT_BIND_EXPERIMENT_CONTRACT")
    if scientific.formal_wiring_contract_constant(path) or scientific.formal_wiring_helper_constant(path):
        blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_MUST_NOT_BIND_FORMAL_SCIENTIFIC_CONTRACT")
    return sorted(set(blockers))


def is_transport(file_name: str) -> bool:
    if not file_name.startswith(TRANSPORT_PREFIX) or not file_name.endswith(".py"):
        return False
    path = REPO_ROOT / file_name
    if not path.is_file():
        return False
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=file_name)
    except Exception:
        return False
    return _constant(tree, TRANSPORT_MARKER) == TRANSPORT_MARKER_VALUE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--expected-guard-canonical-ast-sha256", required=True)
    ap.add_argument("--expected-audit-canonical-ast-sha256", required=True)
    args = ap.parse_args()
    changed = scientific.changed_files(args.base, args.head)
    transport = [f for f in changed if is_transport(f)]
    remaining = [f for f in changed if f not in transport]
    blockers: list[str] = []
    for file_name in transport:
        blockers.extend(transport_blockers(file_name))
    if blockers:
        for blocker in blockers:
            print(f"::error::{blocker}")
        print("FOOTBALL3_CHANGED_AUTHORITY_ROUTER=BLOCK")
        return 2
    original_changed_files = scientific.changed_files
    original_argv = list(sys.argv)
    try:
        scientific.changed_files = lambda base, head: list(remaining)
        sys.argv = [str(SCIENCE_DIR / "audit_football3_changed_scientific_files.py"), "--base", args.base, "--head", args.head, "--expected-guard-canonical-ast-sha256", args.expected_guard_canonical_ast_sha256, "--expected-audit-canonical-ast-sha256", args.expected_audit_canonical_ast_sha256]
        rc = scientific.main()
    except SystemExit as exc:
        rc = int(exc.code or 0)
    finally:
        scientific.changed_files = original_changed_files
        sys.argv = original_argv
    if rc != 0:
        return int(rc)
    print(f"FOOTBALL3_CHANGED_AUTHORITY_ROUTER=PASS transport_files={len(transport)} scientific_authority_files={len(remaining)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
