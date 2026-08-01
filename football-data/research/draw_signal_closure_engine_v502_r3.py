#!/usr/bin/env python3
"""Independent, route-aware draw-signal closure audit for CURRENT V5.0.2.

Read-only research code. It never trains, scores a new target period, accesses a
provider/key, or writes formal assets. It fixes five audit-boundary defects:

* frozen expected asset registry vs live git-ls-files actual ledger;
* explicit per-field def-use contracts instead of same-script co-occurrence;
* route-closure states participate in the final decision;
* global and domain-specific PIT-safe candidate gates are distinct;
* Asian-handicap aliases are market references, not UNKNOWN context.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import draw_signal_closure_engine_v502 as legacy

FORMAL_WEIGHT = 0
BASE_MAIN = legacy.BASE_MAIN
MIN_GLOBAL_COVERAGE = 0.70
MIN_COMPETITION_COVERAGE = 0.30
MIN_DOMAIN_ROWS = 500
MIN_DOMAIN_SEASONS = 3
MIN_DOMAIN_WITHIN_COVERAGE = 0.90

NEGATIVE_DECISION = legacy.NEGATIVE_DECISION
POSITIVE_DECISION = legacy.POSITIVE_DECISION
UNRESOLVED_DECISION = "EXISTING_DATA_DRAW_AUDIT_UNRESOLVED_NO_NEW_TRAINING"
ALLOWED_DECISIONS = {NEGATIVE_DECISION, POSITIVE_DECISION, UNRESOLVED_DECISION}

ROUTE_STATES = {"RESOLVED_REJECTED", "AUDIT_ONLY", "DUPLICATE", "PIT_BLOCKED", "UNRESOLVED"}
ASSET_DIRS = ("football-data/validation", "football-data/research", "football-data/manifests")
SUPPORTED_ASSET_SUFFIXES = {".py", ".json", ".jsonl", ".md", ".txt", ""}

POSTMATCH_COLUMNS = set(legacy.POSTMATCH_COLUMNS)
STRUCTURAL_COLUMNS = set(legacy.STRUCTURAL_COLUMNS)
PIT_UNPROVEN_CONTEXT = set(legacy.PIT_UNPROVEN_CONTEXT)
PIT_FIELD_CONTRACTS = dict(legacy.PIT_FIELD_CONTRACTS)

ASIAN_MARKET_EXACT = {"ahh", "ahch", "b36ca"}
ASIAN_PAIR_SUFFIXES = ("cahh", "caha", "ahh", "aha")
MARKET_TEXT_MARKERS = ("odds", "price", "<2.5", ">2.5", "handicap")

# Production is fail-closed: no field is considered previously tested unless a
# reviewed explicit contract is registered here and verified against exact SHAs.
FIELD_DATAFLOW_CONTRACTS: dict[str, list[dict[str, Any]]] = {}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _normalize_columns(columns: Iterable[str]) -> set[str]:
    return {str(column).strip().lower() for column in columns}


def bookmaker_triplet_key(column: str, all_columns: Iterable[str]) -> str | None:
    """Recognize 1X2 only when a real H/D/A sibling triplet exists."""
    token = column.strip().lower()
    columns = _normalize_columns(all_columns)
    if len(token) < 2 or token[-1] not in {"h", "d", "a"}:
        return None
    prefix = token[:-1]
    if not prefix or prefix in {"roun", "roun c"}:
        return None
    siblings = {prefix + suffix for suffix in ("h", "d", "a")}
    return prefix if siblings.issubset(columns) else None


def asian_market_pair_key(column: str, all_columns: Iterable[str]) -> str | None:
    token = column.strip().lower()
    columns = _normalize_columns(all_columns)
    if token in ASIAN_MARKET_EXACT:
        return token
    for suffix in ASIAN_PAIR_SUFFIXES:
        if not token.endswith(suffix):
            continue
        prefix = token[: -len(suffix)]
        if suffix.endswith("hh"):
            counterpart = prefix + suffix[:-2] + "ha"
        else:
            counterpart = prefix + suffix[:-2] + "hh"
        if counterpart in columns:
            return prefix + suffix[:-2]
    return None


def classify_column(
    column: str,
    all_columns: Iterable[str],
    pit_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str, str]:
    token = column.strip().lower()
    contracts = PIT_FIELD_CONTRACTS if pit_contracts is None else pit_contracts
    if token in POSTMATCH_COLUMNS:
        return "POSTMATCH_FORBIDDEN", "match result or in-match/postmatch statistic"
    if token in STRUCTURAL_COLUMNS:
        return "PIT_SAFE_STRUCTURAL", "identity/schedule field; not a new draw signal"
    if token in contracts:
        contract = contracts[token]
        return str(contract.get("classification", "PIT_SAFE_PREDICTION_TIME")), str(contract.get("source_semantics", "explicit prediction-time contract"))
    if token in PIT_UNPROVEN_CONTEXT:
        return "PIT_UNPROVEN_CONTEXT", "repository has no observed_at proving prediction-time availability"
    if asian_market_pair_key(token, all_columns):
        return "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN", "Asian-handicap line/price alias without original quote timestamp"
    if bookmaker_triplet_key(token, all_columns):
        return "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN", "H/D/A bookmaker triplet without original quote timestamp"
    if any(marker in token for marker in MARKET_TEXT_MARKERS):
        return "RETROSPECTIVE_MARKET_REFERENCE_TIMESTAMP_UNPROVEN", "market field without original quote timestamp"
    if re.fullmatch(r"[ha](?:c|f|r|s|st|y)", token):
        return "POSTMATCH_FORBIDDEN", "in-match statistic"
    return "UNKNOWN_PIT_STATUS", "no repository-wide prediction-time availability contract"


def _literal(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _names_in(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def _function_nodes(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _field_accesses_in_function(function: ast.AST, field: str) -> list[int]:
    target = field.lower()
    lines: list[int] = []
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            literal = _literal(node.args[0])
            if literal and literal.lower() == target:
                lines.append(int(getattr(node, "lineno", 0)))
        elif isinstance(node, ast.Subscript):
            literal = _literal(node.slice)
            if literal and literal.lower() == target:
                lines.append(int(getattr(node, "lineno", 0)))
    return sorted(set(lines))


def _tainted_names(function: ast.AST, field: str) -> set[str]:
    tainted: set[str] = set()
    changed = True
    statements = [node for node in ast.walk(function) if isinstance(node, (ast.Assign, ast.AnnAssign))]
    while changed:
        changed = False
        for node in statements:
            value = node.value
            if value is None:
                continue
            direct_field = False
            for child in ast.walk(value):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr == "get" and child.args:
                    literal = _literal(child.args[0])
                    if literal and literal.lower() == field.lower():
                        direct_field = True
                if isinstance(child, ast.Subscript):
                    literal = _literal(child.slice)
                    if literal and literal.lower() == field.lower():
                        direct_field = True
            depends = direct_field or bool(_names_in(value) & tainted)
            if not depends:
                continue
            targets: list[ast.AST] = list(node.targets) if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _names_in(target) | ({target.id} if isinstance(target, ast.Name) else set()):
                    if name not in tainted:
                        tainted.add(name)
                        changed = True
    return tainted


def verify_explicit_field_contract(root: Path, field: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "script", "script_sha256", "field_access_function", "feature_builder_function",
        "feature_symbol", "scorer_function", "scorer_call", "scorer_argument_symbol",
        "manifest", "manifest_sha256", "result_json_path", "time_split_evidence",
    }
    missing = sorted(required - set(contract))
    if missing:
        return {"verified": False, "reason": f"contract keys missing: {missing}"}
    script = root / str(contract["script"])
    manifest = root / str(contract["manifest"])
    if not script.is_file() or not manifest.is_file():
        return {"verified": False, "reason": "script or manifest missing"}
    if sha256_file(script) != contract["script_sha256"] or sha256_file(manifest) != contract["manifest_sha256"]:
        return {"verified": False, "reason": "exact SHA mismatch"}
    try:
        tree = ast.parse(script.read_text(encoding="utf-8", errors="replace"))
        manifest_obj = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"verified": False, "reason": f"parse failure: {type(exc).__name__}: {exc}"}
    functions = _function_nodes(tree)
    access_fn = functions.get(str(contract["field_access_function"]))
    builder_fn = functions.get(str(contract["feature_builder_function"]))
    scorer_fn = functions.get(str(contract["scorer_function"]))
    if not access_fn or not builder_fn or not scorer_fn:
        return {"verified": False, "reason": "registered function missing"}
    access_lines = _field_accesses_in_function(access_fn, field)
    if not access_lines:
        return {"verified": False, "reason": "registered raw field access missing"}
    tainted = _tainted_names(builder_fn, field)
    feature_symbol = str(contract["feature_symbol"])
    if feature_symbol not in tainted:
        return {"verified": False, "reason": "feature symbol not def-use connected to raw field"}
    scorer_call = str(contract["scorer_call"])
    scorer_argument = str(contract["scorer_argument_symbol"])
    scorer_connected = False
    for node in ast.walk(scorer_fn):
        if isinstance(node, ast.Call) and _call_name(node) == scorer_call:
            if any(scorer_argument in _names_in(arg) or (isinstance(arg, ast.Name) and arg.id == scorer_argument) for arg in node.args):
                scorer_connected = True
                break
    if not scorer_connected:
        return {"verified": False, "reason": "feature container not present in scorer/estimator arguments"}
    result: Any = manifest_obj
    for part in str(contract["result_json_path"]).split("."):
        if isinstance(result, Mapping) and part in result:
            result = result[part]
        else:
            return {"verified": False, "reason": "registered result path missing"}
    time_token = str(contract["time_split_evidence"]).lower()
    combined = script.read_text(encoding="utf-8", errors="replace").lower() + "\n" + json.dumps(manifest_obj, ensure_ascii=False).lower()
    if time_token not in combined:
        return {"verified": False, "reason": "registered PIT/time-split evidence missing"}
    return {
        "verified": True,
        "reason": "exact explicit def-use contract verified",
        "script": str(contract["script"]),
        "raw_field_access_lines": access_lines,
        "feature_builder_function": str(contract["feature_builder_function"]),
        "feature_symbol": feature_symbol,
        "scorer_function": str(contract["scorer_function"]),
        "scorer_call": scorer_call,
        "scorer_argument_symbol": scorer_argument,
        "manifest": str(contract["manifest"]),
        "result_json_path": str(contract["result_json_path"]),
        "time_split_evidence": str(contract["time_split_evidence"]),
        "complete_dataflow_chain": True,
    }


def _observational_script_evidence(root: Path, path: Path) -> dict[str, list[dict[str, Any]]]:
    """Record raw accesses but never promote same-script co-occurrence to a chain."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return {}
    functions = _function_nodes(tree)
    evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for function_name, function in functions.items():
        for node in ast.walk(function):
            field: str | None = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
                field = _literal(node.args[0])
            elif isinstance(node, ast.Subscript):
                field = _literal(node.slice)
            if field:
                evidence[field.lower()].append({
                    "script": path.relative_to(root).as_posix(),
                    "raw_field_access_line": int(getattr(node, "lineno", 0)),
                    "field_access_function": function_name,
                    "complete_dataflow_chain": False,
                    "reason": "raw access observed; no verified per-field def-use contract",
                })
    return dict(evidence)


def build_dataflow_index(
    root: Path,
    scripts: Sequence[Path] | None = None,
    contracts: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    paths = list(scripts) if scripts is not None else sorted((root / "football-data" / "validation").glob("**/*.py")) + sorted((root / "football-data" / "research").glob("**/*.py"))
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in paths:
        for field, rows in _observational_script_evidence(root, path).items():
            index[field].extend(rows)
    contract_map = FIELD_DATAFLOW_CONTRACTS if contracts is None else contracts
    for field, rows in contract_map.items():
        for contract in rows:
            verified = verify_explicit_field_contract(root, field, contract)
            verified["contract"] = dict(contract)
            index[field.lower()].append(verified)
    return dict(index)


def dataflow_chain_is_complete(evidence: Sequence[Mapping[str, Any]]) -> bool:
    return any(bool(row.get("verified")) and bool(row.get("complete_dataflow_chain")) for row in evidence)


def _asset_policy(path_string: str) -> tuple[bool, str]:
    path = Path(path_string)
    normalized = path_string.replace("\\", "/")
    if "/cache/" in normalized:
        return False, "CACHE_ARTIFACT_EXCLUDED"
    if path.suffix.lower() == ".log":
        return False, "GENERATED_LOG_EXCLUDED"
    if path.suffix.lower() in SUPPORTED_ASSET_SUFFIXES:
        return True, "TRACKED_VALIDATION_RESEARCH_MANIFEST_ASSET"
    return False, "UNSUPPORTED_ASSET_SUFFIX_EXCLUDED"


def load_expected_asset_registry(root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    path = registry_path or (root / "football-data" / "audit" / "draw_signal_asset_registry_v502.txt")
    entries = []
    metadata: dict[str, Any] = {"schema_version": "DRAW-SIGNAL-ASSET-REGISTRY-V502-1.0"}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            if "=" in line:
                key, value = line[1:].split("=", 1)
                metadata[key.strip()] = value.strip()
            continue
        flag, rel = line.split("\t", 1)
        if flag not in {"I", "E"}:
            raise ValueError(f"invalid registry flag: {flag}")
        entries.append({"path": rel, "expected_included": flag == "I"})
    if not entries:
        raise ValueError("expected asset registry entries missing")
    metadata["entries"] = entries
    return metadata


def live_git_asset_paths(root: Path) -> list[str]:
    output = git(root, "ls-files", *ASSET_DIRS)
    return sorted({line.strip() for line in output.splitlines() if line.strip()})


def build_actual_asset_ledger(root: Path, tracked_paths: Sequence[str] | None = None) -> list[dict[str, Any]]:
    paths = sorted(set(tracked_paths or live_git_asset_paths(root)))
    rows: list[dict[str, Any]] = []
    for rel in paths:
        included, reason = _asset_policy(rel)
        path = root / rel
        parse_error = None
        if not path.is_file():
            parse_error = "TRACKED_PATH_MISSING_FROM_WORKTREE"
        elif included:
            try:
                if path.suffix.lower() == ".py":
                    ast.parse(path.read_text(encoding="utf-8", errors="replace"))
                elif path.suffix.lower() == ".json":
                    json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                parse_error = f"{type(exc).__name__}: {exc}"
        rows.append({
            "path": rel,
            "included": included,
            "inclusion_reason": reason if included else None,
            "exclusion_reason": None if included else reason,
            "sha256": sha256_file(path) if path.is_file() else None,
            "size": path.stat().st_size if path.is_file() else None,
            "parse_error": parse_error,
            "formal_weight": 0,
        })
    return rows


def research_asset_coverage(registry: Mapping[str, Any], actual_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    expected_entries = {str(row["path"]): bool(row["expected_included"]) for row in registry.get("entries", [])}
    actual_entries = {str(row["path"]): bool(row["included"]) for row in actual_rows}
    expected_paths = set(expected_entries)
    actual_paths = set(actual_entries)
    missing_paths = sorted(expected_paths - actual_paths)
    extra_paths = sorted(actual_paths - expected_paths)
    inclusion_mismatch = sorted(path for path in expected_paths & actual_paths if expected_entries[path] != actual_entries[path])
    expected_included = sorted(path for path, included in expected_entries.items() if included)
    actual_included = sorted(path for path, included in actual_entries.items() if included)
    parse_failures = sorted(str(row["path"]) for row in actual_rows if row.get("included") and row.get("parse_error"))
    all_covered = not missing_paths and not extra_paths and not inclusion_mismatch and not parse_failures
    return {
        "registry_schema_version": registry.get("schema_version"),
        "expected_path_count": len(expected_entries),
        "actual_path_count": len(actual_entries),
        "expected_included_count": len(expected_included),
        "actual_included_count": len(actual_included),
        "expected": expected_included,
        "matched": actual_included,
        "missing": missing_paths,
        "extra": extra_paths,
        "inclusion_mismatch": inclusion_mismatch,
        "parse_failures": parse_failures,
        "all_covered": all_covered,
    }


def _route_has_metrics(row: Mapping[str, Any]) -> bool:
    return any(value is not None for value in row.get("metrics", {}).values())


def classify_route_closure(row: Mapping[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or "").strip().upper()
    reason = str(row.get("rejection_or_result") or "").strip()
    reason_upper = reason.upper()
    pit = str(row.get("pit") or "").upper()
    route_id = str(row.get("id") or "")
    family = str(row.get("family") or "").lower()
    file_count = int(row.get("file_count", 0))
    candidate_improvement = any(token in status + " " + reason_upper for token in ("CHALLENGE", "CANDIDATE", "PROMOTION", "IMPROVEMENT")) and "REJECT" not in status + reason_upper

    if file_count == 0:
        state, closure_reason = "UNRESOLVED", "canonical route has no source asset"
    elif not status:
        state, closure_reason = "UNRESOLVED", "route has no explicit result status"
    elif "FAILED_EXECUTION" in status or status == "PARTIAL":
        state, closure_reason = "UNRESOLVED", "execution/result incomplete"
    elif candidate_improvement:
        state, closure_reason = "UNRESOLVED", "candidate/challenge result requires explicit adjudication"
    elif "REJECT" in status or "REJECT" in reason_upper or "DID NOT ADD" in reason_upper or "FAILURE" in reason_upper:
        state, closure_reason = "RESOLVED_REJECTED", "explicit rejection or no-increment result"
    elif "REGISTRY" in route_id or "registry" in family:
        state, closure_reason = "DUPLICATE", "registry/duplicate-control route; not an independent challenger"
    elif "AUDIT_ONLY" in pit or "DIAGNOSTIC" in status or "diagnostic" in family or "overlap audit" in family:
        state, closure_reason = "AUDIT_ONLY", "diagnostic/audit route without promotion claim"
    elif any(token in pit for token in ("RETROSPECTIVE", "RANDOM_SAMPLE", "PIT_UNPROVEN", "PIT_CLAIMED", "REPEATED_RESEARCH_SET")):
        if _route_has_metrics(row) or reason:
            state, closure_reason = "PIT_BLOCKED", "result exists but PIT/promotion-grade evidence is blocked"
        else:
            state, closure_reason = "UNRESOLVED", "PIT-blocked route also lacks result evidence"
    elif status == "PASS" and (_route_has_metrics(row) or reason):
        state, closure_reason = "UNRESOLVED", "PASS is not an explicit rejection/audit/duplicate closure"
    else:
        state, closure_reason = "UNRESOLVED", "route result cannot be closed from current evidence"

    if state not in ROUTE_STATES:
        raise AssertionError(state)
    return {
        "id": route_id,
        "closure_state": state,
        "closure_reason": closure_reason,
        "candidate_improvement": candidate_improvement,
        "result_status": status or None,
        "result_evidence_present": bool(status and (_route_has_metrics(row) or reason or state in {"AUDIT_ONLY", "DUPLICATE"})),
        "files": list(row.get("files", [])),
        "formal_weight": 0,
    }


def build_route_closure_ledger(experiment_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [classify_route_closure(row) for row in experiment_rows]
    unresolved = [row for row in rows if row["closure_state"] == "UNRESOLVED"]
    candidates = [row for row in rows if row["candidate_improvement"]]
    missing_results = [row for row in rows if not row["result_evidence_present"]]
    counts = dict(Counter(row["closure_state"] for row in rows))
    blocks_exhausted = bool(unresolved or candidates or missing_results)
    return {
        "rows": rows,
        "state_counts": counts,
        "unresolved": unresolved,
        "candidate_improvements": candidates,
        "missing_result_evidence": missing_results,
        "blocks_exhausted": blocks_exhausted,
        "all_routes_closed": not blocks_exhausted,
    }


def _domain_eligible_rows(field_row: Mapping[str, Any]) -> list[dict[str, Any]]:
    eligible = []
    for domain in field_row.get("domain_coverage", []):
        if (
            int(domain.get("rows", 0)) >= MIN_DOMAIN_ROWS
            and float(domain.get("coverage", 0.0)) >= MIN_DOMAIN_WITHIN_COVERAGE
            and int(domain.get("season_count", 0)) >= MIN_DOMAIN_SEASONS
        ):
            eligible.append(dict(domain))
    return eligible


def assess_field_candidate(
    field: str,
    stat: Mapping[str, Any],
    *,
    all_columns: Iterable[str],
    total_rows: int,
    total_competitions: int,
    dataflow_index: Mapping[str, Sequence[Mapping[str, Any]]],
    pit_contracts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    classification, reason = classify_column(field, all_columns, pit_contracts)
    nonempty = int(stat.get("nonempty", 0))
    rows_in_files = int(stat.get("rows_in_files", 0))
    competitions = sorted(set(stat.get("competitions", [])))
    global_coverage = nonempty / total_rows if total_rows else 0.0
    competition_coverage = len(competitions) / total_competitions if total_competitions else 0.0
    within_coverage = nonempty / rows_in_files if rows_in_files else 0.0
    evidence = list(dataflow_index.get(field.lower(), []))
    exact_used = dataflow_chain_is_complete(evidence)
    substantive = classification not in {"PIT_SAFE_STRUCTURAL", "POSTMATCH_FORBIDDEN"}
    pit_safe = classification == "PIT_SAFE_PREDICTION_TIME"
    global_gate = global_coverage >= MIN_GLOBAL_COVERAGE and competition_coverage >= MIN_COMPETITION_COVERAGE
    untested = not exact_used
    domain_coverage = []
    for competition, domain in sorted(dict(stat.get("domains", {})).items()):
        rows = int(domain.get("rows", 0))
        present = int(domain.get("nonempty", 0))
        domain_coverage.append({
            "competition": competition,
            "rows": rows,
            "nonempty": present,
            "coverage": present / rows if rows else 0.0,
            "seasons": sorted(set(domain.get("seasons", []))),
            "season_count": len(set(domain.get("seasons", []))),
        })
    row = {
        "field": field,
        "classification": classification,
        "classification_reason": reason,
        "files": int(stat.get("files", 0)),
        "rows_in_files": rows_in_files,
        "nonempty": nonempty,
        "coverage_within_present_files": within_coverage,
        "global_row_coverage": global_coverage,
        "competition_count": len(competitions),
        "competition_coverage": competition_coverage,
        "competitions": competitions,
        "sample_paths": list(stat.get("sample_paths", [])),
        "domain_coverage": domain_coverage,
        "dataflow_evidence": evidence,
        "previously_tested_via_complete_dataflow": exact_used,
        "substantive_predictive_candidate": substantive,
        "pit_safe": pit_safe,
        "global_coverage_gate_passed": global_gate,
        "untested": untested,
    }
    eligible_domains = _domain_eligible_rows(row)
    row["eligible_domain_specific_scopes"] = eligible_domains
    row["qualifies_existing_pit_safe_untested"] = substantive and pit_safe and global_gate and untested
    row["qualifies_domain_specific_pit_safe_untested"] = substantive and pit_safe and untested and bool(eligible_domains) and not row["qualifies_existing_pit_safe_untested"]
    return row


def profile_fields(
    root: Path,
    *,
    pit_contracts: Mapping[str, Mapping[str, Any]] | None = None,
    dataflow_index: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    files = sorted((root / "football-data" / "processed").glob("**/*.csv"))
    field_stats: dict[str, dict[str, Any]] = {}
    file_manifest: list[dict[str, Any]] = []
    total_rows = 0
    competitions_seen: set[str] = set()
    all_columns: set[str] = set()
    for path in files:
        rel = path.relative_to(root).as_posix()
        competition = path.parent.name
        competitions_seen.add(competition)
        row_count = 0
        present: Counter[str] = Counter()
        seasons_by_column: dict[str, set[str]] = defaultdict(set)
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = reader.fieldnames or []
                all_columns.update(columns)
                for raw in reader:
                    row_count += 1
                    season = str(raw.get("season") or raw.get("Season") or path.stem).strip()
                    for column in columns:
                        if str(raw.get(column) or "").strip():
                            present[column] += 1
                            seasons_by_column[column].add(season)
        except Exception as exc:
            file_manifest.append({"path": rel, "sha256": sha256_file(path), "error": f"{type(exc).__name__}: {exc}"})
            continue
        total_rows += row_count
        file_manifest.append({"path": rel, "sha256": sha256_file(path), "rows": row_count, "columns": columns})
        for column in columns:
            stat = field_stats.setdefault(column, {"files": 0, "rows_in_files": 0, "nonempty": 0, "competitions": set(), "sample_paths": [], "domains": {}})
            stat["files"] += 1
            stat["rows_in_files"] += row_count
            stat["nonempty"] += present[column]
            stat["competitions"].add(competition)
            if len(stat["sample_paths"]) < 5:
                stat["sample_paths"].append(rel)
            domain = stat["domains"].setdefault(competition, {"rows": 0, "nonempty": 0, "seasons": set()})
            domain["rows"] += row_count
            domain["nonempty"] += present[column]
            domain["seasons"].update(seasons_by_column[column])
    flows = dict(dataflow_index) if dataflow_index is not None else build_dataflow_index(root)
    rows = [
        assess_field_candidate(
            column,
            stat,
            all_columns=all_columns,
            total_rows=total_rows,
            total_competitions=len(competitions_seen),
            dataflow_index=flows,
            pit_contracts=pit_contracts,
        )
        for column, stat in sorted(field_stats.items(), key=lambda item: item[0].lower())
    ]
    global_candidates = [row for row in rows if row["qualifies_existing_pit_safe_untested"]]
    domain_candidates = [row for row in rows if row["qualifies_domain_specific_pit_safe_untested"]]
    near_miss = [
        row for row in rows
        if row["substantive_predictive_candidate"]
        and not row["qualifies_existing_pit_safe_untested"]
        and not row["qualifies_domain_specific_pit_safe_untested"]
    ]
    return {
        "processed_csv_count": len(files),
        "processed_total_rows_scanned": total_rows,
        "processed_competition_count": len(competitions_seen),
        "data_file_manifest": file_manifest,
        "field_count": len(rows),
        "field_classification_counts": dict(Counter(row["classification"] for row in rows)),
        "fields": rows,
        "dataflow_field_count": len(flows),
        "dataflow_index_sha256": canonical_sha(flows),
        "EXISTING_PIT_SAFE_UNTESTED_FEATURES": global_candidates,
        "DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES": domain_candidates,
        "UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED": near_miss,
    }


def make_preregistration(
    global_candidates: Sequence[Mapping[str, Any]],
    domain_candidates: Sequence[Mapping[str, Any]],
    route_closure: Mapping[str, Any],
) -> dict[str, Any]:
    features = []
    for row in global_candidates:
        features.append({"field": row["field"], "scope": "GLOBAL", "evidence_sha256": canonical_sha(row)})
    for row in domain_candidates:
        features.append({
            "field": row["field"],
            "scope": "DOMAIN_SPECIFIC",
            "eligible_domains": row["eligible_domain_specific_scopes"],
            "evidence_sha256": canonical_sha(row),
        })
    return {
        "status": "PRE_REGISTERED_NOT_RUN",
        "features": features,
        "model": "domain-scoped multinomial logistic challenger with nested time-order validation",
        "hyperparameter_grid": {"l2": [0.1, 1.0, 10.0], "class_weight_draw": [1.0, 1.15, 1.3]},
        "outer_folds": "expanding-window within each eligible domain and season",
        "inner_selection": "training-only rolling validation; no random split",
        "primary_metric": "paired draw_F1 and macro_F1 improvement under full-1X2 noninferiority",
        "promotion_gates": {
            "accuracy_delta_min": -0.005,
            "log_loss_delta_max": 0.005,
            "brier_delta_max": 0.005,
            "rps_delta_max": 0.003,
            "draw_f1_paired_bootstrap_lower_bound_min": 0.0,
            "macro_f1_paired_bootstrap_lower_bound_min": 0.0,
            "minimum_draw_predictions_per_fold": 20,
        },
        "holdout_policy": "latest eligible chronological domain segment must be proven untouched before any run",
        "holdout_status": "NOT_YET_PROVEN_UNTOUCHED",
        "route_closure_blockers": [row["id"] for row in route_closure.get("unresolved", [])],
        "formal_weight": 0,
        "run_authorized": False,
    }


def decide_and_preregister(
    global_candidates: Sequence[Mapping[str, Any]],
    domain_candidates: Sequence[Mapping[str, Any]],
    route_closure: Mapping[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    if global_candidates or domain_candidates:
        return POSITIVE_DECISION, make_preregistration(global_candidates, domain_candidates, route_closure)
    if bool(route_closure.get("blocks_exhausted")):
        return UNRESOLVED_DECISION, None
    return NEGATIVE_DECISION, None


def build_audit(root: Path) -> dict[str, Any]:
    head = git(root, "rev-parse", "HEAD")
    experiment_rows, audited_files = legacy.build_experiment_ledger(root)
    found_versions = {row["version"] for row in experiment_rows if row["file_count"] > 0}
    missing_versions = [prefix for prefix in legacy.REQUIRED_VERSION_PREFIXES if not any(version.startswith(prefix) for version in found_versions)]
    route_closure = build_route_closure_ledger(experiment_rows)
    dataflow = build_dataflow_index(root)
    feature_difference = profile_fields(root, dataflow_index=dataflow)
    decision, preregistration = decide_and_preregister(
        feature_difference["EXISTING_PIT_SAFE_UNTESTED_FEATURES"],
        feature_difference["DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES"],
        route_closure,
    )
    registry = load_expected_asset_registry(root)
    actual_rows = build_actual_asset_ledger(root)
    coverage = research_asset_coverage(registry, actual_rows)
    audit = {
        "schema_version": "DRAW-SIGNAL-CLOSURE-AUDIT-V502-3.0",
        "head": head,
        "base_main": BASE_MAIN,
        "formal_weight": 0,
        "provider_network_used": False,
        "external_request_attempts": 0,
        "api_football_key_accessed": False,
        "model_training": 0,
        "decision": decision,
        "preregistration": preregistration,
        "required_version_prefixes": list(legacy.REQUIRED_VERSION_PREFIXES),
        "missing_required_version_prefixes": missing_versions,
        "experiment_count": len(experiment_rows),
        "experiment_ledger": experiment_rows,
        "experiment_route_closure": route_closure,
        "audited_files": audited_files,
        "feature_difference": feature_difference,
        "research_asset_registry_sha256": canonical_sha(registry),
        "research_asset_coverage": coverage,
        "complete_research_file_count": len(actual_rows),
        "complete_research_file_ledger": actual_rows,
        "all_draw_1x2_research_files_covered": coverage["all_covered"],
        "reference_scorecards": {
            "strict_time_ordered_probability_diagnostics": {"matches": 4786, "actual_draw_rate": 0.2566, "mean_predicted_draw_probability": 0.2479, "mean_domain_draw_auc": 0.52218, "draw_probability_std": 0.01711},
            "full_1x2": {"count": 5110, "hits": 2645, "accuracy": 0.517613, "predicted_home_draw_away": [3532, 40, 1538]},
            "selector": {"executed": 1611, "accuracy": 0.671012, "predicted_home_draw_away": [1252, 0, 359], "scope": "SELECTIVE_NOT_FULL_1X2"},
            "ha_risk_veto": {"executed": 1252, "accuracy": 0.707668, "market_coverage": 0.245010, "draw_predictions": 0, "scope": "HOME_AWAY_ABSTAIN"},
        },
    }
    audit["audit_sha256"] = canonical_sha(audit)
    return audit


def _markdown_features(feature: Mapping[str, Any], decision: str) -> str:
    lines = ["# 现有字段差集与域内候选", "", f"裁决：`{decision}`", ""]
    lines.append(f"全局候选：{len(feature['EXISTING_PIT_SAFE_UNTESTED_FEATURES'])}；域内候选：{len(feature['DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES'])}；near-miss：{len(feature['UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED'])}。")
    lines += ["", "## DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES", ""]
    for row in feature["DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES"]:
        lines.append(f"- `{row['field']}`：" + ", ".join(f"{item['competition']} rows={item['rows']} seasons={item['season_count']} coverage={item['coverage']:.2%}" for item in row["eligible_domain_specific_scopes"]))
    lines += ["", "## Near-miss", ""]
    for row in feature["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]:
        lines.append(f"- `{row['field']}`：{row['classification']}；global={row['global_row_coverage']:.2%}；domains={row['competition_count']}")
    return "\n".join(lines) + "\n"


def write_audit(out: Path, audit: Mapping[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    head = str(audit["head"])
    feature = audit["feature_difference"]
    complete = {"head": head, "count": audit["complete_research_file_count"], "rows": audit["complete_research_file_ledger"], "coverage": audit["research_asset_coverage"]}
    objects = {
        "experiment_ledger.json": {"head": head, "formal_weight": 0, "rows": audit["experiment_ledger"], "route_closure": audit["experiment_route_closure"]},
        "feature_difference.json": feature,
        "decision.json": {"decision": audit["decision"], "formal_weight": 0, "preregistration": audit["preregistration"]},
        "closure_audit.json": audit,
        "audited_files.json": audit["audited_files"],
        "data_manifest.json": feature["data_file_manifest"],
        "complete_research_file_ledger.json": complete,
        "metadata.json": {"checkout_head": head, "formal_weight": 0, "provider_network_used": False, "external_request_attempts": 0, "api_football_key_accessed": False, "model_training": 0, "decision": audit["decision"], "audit_sha256": audit["audit_sha256"], "complete_research_file_count": audit["complete_research_file_count"], "all_draw_1x2_research_files_covered": audit["all_draw_1x2_research_files_covered"]},
    }
    for name, value in objects.items():
        (out / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "feature_difference.md").write_text(_markdown_features(feature, str(audit["decision"])), encoding="utf-8")
    (out / "experiment_ledger.md").write_text(json.dumps(audit["experiment_route_closure"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    audit = build_audit(root)
    write_audit(args.output_dir, audit)
    print(json.dumps({
        "head": audit["head"],
        "experiments": audit["experiment_count"],
        "actual_assets": audit["complete_research_file_count"],
        "global_candidates": len(audit["feature_difference"]["EXISTING_PIT_SAFE_UNTESTED_FEATURES"]),
        "domain_candidates": len(audit["feature_difference"]["DOMAIN_SPECIFIC_PIT_SAFE_UNTESTED_FEATURES"]),
        "near_miss": len(audit["feature_difference"]["UNTESTED_BUT_NOT_PIT_SAFE_OR_COVERED"]),
        "unresolved_routes": len(audit["experiment_route_closure"]["unresolved"]),
        "decision": audit["decision"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
