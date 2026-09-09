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
RESEARCH_REPLAY_MARKER = "FOOTBALL3_GOVERNED_RESEARCH_REPLAY"
RESEARCH_REPLAY_MARKER_VALUE = "football3-current-formal-retrospective-research-replay-v1"
RESEARCH_REPLAY_MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
ALLOWED_RUNTIME_ATTRIBUTES = {"FORMAL_SCOPE", "_parse_dt", "_normalize_team"}
RESEARCH_REPLAY_ALLOWED_RUNTIME_ATTRIBUTES = {
    "RuntimeGateError", "FORMAL_SCOPE", "FORMAL_HEAD", "CURRENT_SHA256", "formal_v2",
    "_normalize_team", "_read_aliases", "_canonical_team", "_parse_dt", "hxg",
    "_fixture_id", "_global_team_id", "HistoryFixture", "XGLabel",
    "load_frozen_v1_history", "load_xg_labels", "BASE_HISTORY_CUTOFF",
    "replay_history_state", "_prediction_from_state", "_parse_match_date",
}
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


def _parse_governed(file_name: str) -> tuple[pathlib.Path, ast.Module] | None:
    if not file_name.startswith(TRANSPORT_PREFIX) or not file_name.endswith(".py"):
        return None
    path = REPO_ROOT / file_name
    if not path.is_file():
        return None
    try:
        return path, ast.parse(path.read_text(encoding="utf-8"), filename=file_name)
    except Exception:
        return None


def _shared_non_scientific_blockers(file_name: str, tree: ast.Module) -> list[str]:
    blockers: list[str] = []
    for forbidden in FORBIDDEN_TRANSPORT_CONSTANT_NAMES:
        if _constant(tree, forbidden) is not None:
            blockers.append(f"{file_name}: GOVERNED_GATEWAY_SCIENTIFIC_CONSTANT_FORBIDDEN:{forbidden}")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(fragment in alias.name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                    blockers.append(f"{file_name}: GOVERNED_GATEWAY_SCIENTIFIC_IMPORT_FORBIDDEN:{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            name = node.module or ""
            if any(fragment in name for fragment in FORBIDDEN_IMPORT_FRAGMENTS):
                blockers.append(f"{file_name}: GOVERNED_GATEWAY_SCIENTIFIC_IMPORT_FORBIDDEN:{name}")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_CALL_NAMES:
                blockers.append(f"{file_name}: GOVERNED_GATEWAY_SCIENTIFIC_CALL_FORBIDDEN:{name}")
    for reason in scientific.dynamic_authority_blockers(REPO_ROOT / file_name):
        blockers.append(f"{file_name}: AST_DYNAMIC_AUTHORITY_DENIED:{reason}")
    if scientific.contract_constant(REPO_ROOT / file_name) or scientific.helper_contract_constant(REPO_ROOT / file_name):
        blockers.append(f"{file_name}: GOVERNED_GATEWAY_MUST_NOT_BIND_EXPERIMENT_CONTRACT")
    if scientific.formal_wiring_contract_constant(REPO_ROOT / file_name) or scientific.formal_wiring_helper_constant(REPO_ROOT / file_name):
        blockers.append(f"{file_name}: GOVERNED_GATEWAY_MUST_NOT_BIND_FORMAL_SCIENTIFIC_CONTRACT")
    return blockers


def transport_blockers(file_name: str) -> list[str]:
    parsed = _parse_governed(file_name)
    if parsed is None:
        return [f"{file_name}: PRODUCTION_TRANSPORT_SYNTAX_INVALID"]
    _path, tree = parsed
    blockers: list[str] = []
    if _constant(tree, TRANSPORT_MARKER) != TRANSPORT_MARKER_VALUE:
        blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_MARKER_MISSING")
    blockers.extend(_shared_non_scientific_blockers(file_name, tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rt":
            if node.attr not in ALLOWED_RUNTIME_ATTRIBUTES:
                blockers.append(f"{file_name}: PRODUCTION_TRANSPORT_RUNTIME_ATTRIBUTE_FORBIDDEN:{node.attr}")
    return sorted(set(blockers))


def research_replay_blockers(file_name: str) -> list[str]:
    parsed = _parse_governed(file_name)
    if parsed is None:
        return [f"{file_name}: RESEARCH_REPLAY_SYNTAX_INVALID"]
    _path, tree = parsed
    blockers: list[str] = []
    if _constant(tree, RESEARCH_REPLAY_MARKER) != RESEARCH_REPLAY_MARKER_VALUE:
        blockers.append(f"{file_name}: RESEARCH_REPLAY_MARKER_MISSING")
    if _constant(tree, "MODE") != RESEARCH_REPLAY_MODE:
        blockers.append(f"{file_name}: RESEARCH_REPLAY_MODE_INVALID")
    if _constant(tree, TRANSPORT_MARKER) is not None:
        blockers.append(f"{file_name}: RESEARCH_REPLAY_MUST_NOT_CLAIM_PRODUCTION_TRANSPORT")
    blockers.extend(_shared_non_scientific_blockers(file_name, tree))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "rt":
            if node.attr not in RESEARCH_REPLAY_ALLOWED_RUNTIME_ATTRIBUTES:
                blockers.append(f"{file_name}: RESEARCH_REPLAY_RUNTIME_ATTRIBUTE_FORBIDDEN:{node.attr}")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "rt":
                    blockers.append(f"{file_name}: RESEARCH_REPLAY_RUNTIME_MUTATION_FORBIDDEN:{target.attr}")
    return sorted(set(blockers))


def is_transport(file_name: str) -> bool:
    parsed = _parse_governed(file_name)
    return parsed is not None and _constant(parsed[1], TRANSPORT_MARKER) == TRANSPORT_MARKER_VALUE


def is_research_replay(file_name: str) -> bool:
    parsed = _parse_governed(file_name)
    return parsed is not None and _constant(parsed[1], RESEARCH_REPLAY_MARKER) == RESEARCH_REPLAY_MARKER_VALUE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--expected-guard-canonical-ast-sha256", required=True)
    ap.add_argument("--expected-audit-canonical-ast-sha256", required=True)
    args = ap.parse_args()
    changed = scientific.changed_files(args.base, args.head)
    transport = [f for f in changed if is_transport(f)]
    research_replay = [f for f in changed if f not in transport and is_research_replay(f)]
    routed = set(transport) | set(research_replay)
    remaining = [f for f in changed if f not in routed]
    blockers: list[str] = []
    for file_name in transport:
        blockers.extend(transport_blockers(file_name))
    for file_name in research_replay:
        blockers.extend(research_replay_blockers(file_name))
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
    print(
        "FOOTBALL3_CHANGED_AUTHORITY_ROUTER=PASS "
        f"transport_files={len(transport)} research_replay_files={len(research_replay)} "
        f"scientific_authority_files={len(remaining)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
