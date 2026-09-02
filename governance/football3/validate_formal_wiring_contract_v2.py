from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
from pathlib import Path

EXPECTED_BRANCH = "football3/historical-xg-fusion-v2-formal-wiring-governed-v1"
EXPECTED_BASE_HEAD = "d3b3e322f78c48b91477ef6e11054e51ac00fd85"
EXPECTED_STATUS = "GOVERNANCE_REMEDIATED_PENDING_CODEX_RECHECK"
EXPECTED_KIND = "historical_xg_fusion_v2_formal_wiring_non_market"
EXPECTED_SCHEMA_ID = "football3://governance/formal-wiring-contract-schema-v2"
EXPECTED_RESEARCH = {
    "branch": "football3/historical-xg-fusion-v2",
    "head": "d3b3e322f78c48b91477ef6e11054e51ac00fd85",
    "run_id": 33581218312,
    "artifact_id": 9828471485,
    "codex": "CODEX_PASS",
}
EXPECTED_V1 = {
    "head": "22f639304d2e32fc952dbec2255153ee45dcd41a",
    "engine_sha256": "cc2c2c3eca421ad6d277107b8f1212656b2e943cc179e7f394ac53e916c3f318",
}
EXPECTED_WHITELIST = {
    ".github/workflows/football3-full-stack-remediation.yml",
    ".github/workflows/football3-historical-xg-fusion-v2-formal-wiring.yml",
    "football-data/historical_xg_fusion_v2/contracts/FORMAL_FUSION_V2_WIRING.json",
    "football-data/new_engine_v1/formal_fusion_v2.py",
    "football-data/new_engine_v1/test_formal_fusion_v2.py",
    "football-data/research/audit_football3_changed_scientific_files.py",
    "governance/football3/formal_wiring_contract_schema_v2.json",
    "governance/football3/test_validate_formal_wiring_contract_v2.py",
    "governance/football3/validate_formal_wiring_contract_v2.py",
}
EXPECTED_MARKET_BLOBS = {
    "football-data/research/FOOTBALL3_EXPERIMENT_CONTRACT_TEMPLATE_V2.json": "776abfbc06b66405aeb13d67848518c64e05d3d2",
    "football-data/research/validate_football3_experiment.py": "19be1b19fec4187beec8abff6d24e5ceb60c2945",
    "football-data/research/test_validate_football3_experiment.py": "18ef0467563b9354ce2d152fe48022c484e4608e",
    "football-data/research/audit_football3_changed_scientific_files.py": "e40284a163cbc4bc4d5f2862b1243d94d2b3b872",
}
EXPECTED_FORMAL_BLOBS = {
    "football-data/new_engine_v1/formal_fusion_v2.py": "a5ed26d5ffd4a2875cb9c658cfaa28665a8b7871",
    "football-data/new_engine_v1/test_formal_fusion_v2.py": "61b25cd403fa1d9efa0dfcbc1643e0f17621944e",
}
EXPECTED_BINDINGS = {
    "runner": "football-data/new_engine_v1/formal_fusion_v2.py",
    "helpers": ["football-data/new_engine_v1/test_formal_fusion_v2.py"],
    "contract_marker": "FOOTBALL3_FORMAL_WIRING_CONTRACT",
    "helper_marker": "FOOTBALL3_FORMAL_WIRING_HELPER_FOR",
    "authority_guard": "football-data/research/audit_football3_changed_scientific_files.py",
    "cumulative_audit_base_head": EXPECTED_BASE_HEAD,
}
REQUIRED_FORBIDDEN = {
    "market_features",
    "market_validator_change",
    "new_target_labels",
    "retrain",
    "retune",
    "change_weight",
    "change_gate",
    "change_model_parameters",
    "post_view_repair",
    "future_research_queue",
    "CURRENT",
    "PR334/R5",
    "Ready",
    "merge",
    "force",
    "formal_enablement",
}


class FormalWiringGovernanceError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise FormalWiringGovernanceError(message)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"unreadable JSON {path}: {exc}")
    if not isinstance(value, dict):
        fail(f"JSON root must be object: {path}")
    return value


def require_exact_keys(value: dict, expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        fail(f"{name} keys mismatch: missing={sorted(expected-actual)} extra={sorted(actual-expected)}")


def validate_schema(schema: dict) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("formal_wiring schema must use JSON Schema draft 2020-12")
    if schema.get("$id") != EXPECTED_SCHEMA_ID:
        fail("formal_wiring schema id mismatch")
    if schema.get("additionalProperties") is not False:
        fail("formal_wiring schema must fail closed on unknown top-level keys")
    props = schema.get("properties")
    if not isinstance(props, dict):
        fail("formal_wiring schema properties missing")
    required = set(schema.get("required") or [])
    expected_required = {
        "schema_version", "project_id", "contract_kind", "branch", "status",
        "research_acceptance", "frozen_v1", "fusion", "runtime", "governance", "forbidden",
    }
    if required != expected_required:
        fail("formal_wiring schema required-key set drift")
    constants = {
        "schema_version": 2,
        "project_id": "football3",
        "contract_kind": EXPECTED_KIND,
        "branch": EXPECTED_BRANCH,
        "status": EXPECTED_STATUS,
    }
    for key, expected in constants.items():
        if not isinstance(props.get(key), dict) or props[key].get("const") != expected:
            fail(f"formal_wiring schema constant drift: {key}")
    gov_props = props.get("governance", {}).get("properties", {})
    binding_schema = gov_props.get("scientific_code_bindings")
    if not isinstance(binding_schema, dict) or binding_schema.get("additionalProperties") is not False:
        fail("formal_wiring scientific binding schema missing/fail-open")


def validate_contract(contract: dict, schema: dict) -> None:
    validate_schema(schema)
    require_exact_keys(
        contract,
        {
            "schema_version", "project_id", "contract_kind", "branch", "status",
            "research_acceptance", "frozen_v1", "fusion", "runtime", "governance", "forbidden",
        },
        "contract",
    )
    if contract["schema_version"] != 2 or contract["project_id"] != "football3":
        fail("formal_wiring contract must be football3 schema v2")
    if contract["contract_kind"] != EXPECTED_KIND:
        fail("formal_wiring contract kind mismatch")
    if contract["branch"] != EXPECTED_BRANCH:
        fail("formal_wiring contract branch mismatch")
    if contract["status"] != EXPECTED_STATUS:
        fail("formal_wiring contract status mismatch")
    if contract["research_acceptance"] != EXPECTED_RESEARCH:
        fail("frozen research acceptance identity drift")
    if contract["frozen_v1"] != EXPECTED_V1:
        fail("Frozen V1 identity drift")

    fusion = contract["fusion"]
    require_exact_keys(fusion, {"xg_weight", "v1_weight", "formula", "score_matrix_lift", "xg_insufficient"}, "fusion")
    if fusion["xg_weight"] != 0.75 or fusion["v1_weight"] != 0.25:
        fail("frozen fusion weights must remain 0.75/0.25")
    if fusion["formula"] != "normalize((1-w)*p_V1 + w*p_XG)":
        fail("frozen fusion formula drift")
    if fusion["xg_insufficient"] != "FROZEN_V1_EXACT_FALLBACK":
        fail("XG-insufficient route must be exact Frozen V1 fallback")
    if not isinstance(fusion["score_matrix_lift"], str) or not fusion["score_matrix_lift"].strip():
        fail("score-matrix lift semantics must be explicit")

    runtime = contract["runtime"]
    require_exact_keys(
        runtime,
        {"candidate_entry", "formal_enablement", "production_pointer_changed", "prospective_queue", "historical_completed_only_for_acceptance"},
        "runtime",
    )
    if runtime != {
        "candidate_entry": "football-data/new_engine_v1/formal_fusion_v2.py",
        "formal_enablement": False,
        "production_pointer_changed": False,
        "prospective_queue": False,
        "historical_completed_only_for_acceptance": True,
    }:
        fail("runtime governance must remain non-enabled historical-only")

    gov = contract["governance"]
    require_exact_keys(
        gov,
        {
            "mode", "market_features", "market_inputs", "market_baseline", "market_validator_semantics",
            "training", "tuning", "new_target_labels", "existing_frozen_historical_replay_only",
            "same_kickoff_isolation_required", "formal_enablement", "production_pointer_change",
            "whitelist_base_head", "changed_file_whitelist", "scientific_code_bindings",
            "immutable_market_governance_git_blobs", "immutable_formal_source_git_blobs",
        },
        "governance",
    )
    exact_nonmarket = {
        "mode": "FORMAL_WIRING_NON_MARKET",
        "market_features": False,
        "market_inputs": [],
        "market_baseline": False,
        "market_validator_semantics": "UNCHANGED_AND_NOT_APPLICABLE_TO_NON_MARKET_FORMAL_WIRING",
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "existing_frozen_historical_replay_only": True,
        "same_kickoff_isolation_required": True,
        "formal_enablement": False,
        "production_pointer_change": False,
        "whitelist_base_head": EXPECTED_BASE_HEAD,
    }
    for key, expected in exact_nonmarket.items():
        if gov.get(key) != expected:
            fail(f"non-market formal_wiring gate drift: {key}")

    whitelist = gov.get("changed_file_whitelist")
    if not isinstance(whitelist, list) or len(whitelist) > 12 or len(whitelist) != len(set(whitelist)):
        fail("changed-file whitelist must contain <=12 unique paths")
    if set(whitelist) != EXPECTED_WHITELIST:
        fail("changed-file whitelist differs from frozen cumulative remediation scope")
    if gov.get("scientific_code_bindings") != EXPECTED_BINDINGS:
        fail("formal_wiring scientific code binding drift")
    if gov.get("immutable_market_governance_git_blobs") != EXPECTED_MARKET_BLOBS:
        fail("market validator/schema/guard blob locks changed")
    if gov.get("immutable_formal_source_git_blobs") != EXPECTED_FORMAL_BLOBS:
        fail("formal source/test blob locks changed")

    forbidden = contract["forbidden"]
    if not isinstance(forbidden, list) or len(forbidden) != len(set(forbidden)):
        fail("forbidden list must be unique")
    if not REQUIRED_FORBIDDEN.issubset(set(forbidden)):
        fail("formal_wiring forbidden set is incomplete")


def _top_level_string_constant(path: Path, name: str) -> str | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        fail(f"binding source syntax error {path}: {exc}")
    matches: list[str] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            matches.append(value.value)
    if len(matches) != 1:
        return None
    return matches[0]


def validate_source_bindings(contract: dict, repo_root: Path) -> None:
    bindings = contract["governance"]["scientific_code_bindings"]
    contract_rel = "football-data/historical_xg_fusion_v2/contracts/FORMAL_FUSION_V2_WIRING.json"
    runner = repo_root / bindings["runner"]
    if not runner.is_file():
        fail("formal_wiring runner missing")
    if _top_level_string_constant(runner, bindings["contract_marker"]) != contract_rel:
        fail("formal_wiring runner contract marker mismatch")
    for helper_rel in bindings["helpers"]:
        helper = repo_root / helper_rel
        if not helper.is_file():
            fail(f"formal_wiring helper missing: {helper_rel}")
        if _top_level_string_constant(helper, bindings["helper_marker"]) != contract_rel:
            fail(f"formal_wiring helper contract marker mismatch: {helper_rel}")


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def validate_repo_locks(contract: dict, repo_root: Path) -> None:
    locks = {}
    locks.update(contract["governance"]["immutable_market_governance_git_blobs"])
    locks.update(contract["governance"]["immutable_formal_source_git_blobs"])
    for rel, expected in locks.items():
        path = repo_root / rel
        if not path.is_file():
            fail(f"locked repository file missing: {rel}")
        actual = git_blob_sha1(path)
        if actual != expected:
            fail(f"immutable repository blob drift: {rel}: expected={expected} actual={actual}")


def validate_runtime_branch(contract: dict) -> None:
    runtime = (os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "").strip()
    if runtime and runtime != contract["branch"]:
        fail(f"runtime branch mismatch: contract={contract['branch']} runtime={runtime}")


def validate_changed_files(contract: dict, repo_root: Path, base_head: str) -> None:
    if base_head != EXPECTED_BASE_HEAD or base_head != contract["governance"]["whitelist_base_head"]:
        fail("diff base must equal frozen cumulative research wiring HEAD")
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{base_head}...HEAD"],
            cwd=repo_root,
            text=True,
        )
    except Exception as exc:
        fail(f"cannot compute frozen cumulative remediation diff: {exc}")
    changed = {line.strip() for line in out.splitlines() if line.strip()}
    expected = set(contract["governance"]["changed_file_whitelist"])
    if changed != expected:
        fail(f"remediation diff scope mismatch: changed={sorted(changed)} expected={sorted(expected)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contract", type=Path, required=True)
    ap.add_argument("--schema", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--base-head", default=EXPECTED_BASE_HEAD)
    ap.add_argument("--skip-diff", action="store_true")
    args = ap.parse_args()

    contract = load_json(args.contract)
    schema = load_json(args.schema)
    validate_contract(contract, schema)
    validate_runtime_branch(contract)
    validate_source_bindings(contract, args.repo_root)
    validate_repo_locks(contract, args.repo_root)
    if not args.skip_diff:
        validate_changed_files(contract, args.repo_root, args.base_head)
    print(json.dumps({
        "status": "FORMAL_WIRING_GOVERNANCE_V2_PASS",
        "contract_kind": contract["contract_kind"],
        "branch": contract["branch"],
        "base_head": args.base_head,
        "market_features": False,
        "market_validator_semantics": "UNCHANGED",
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "formal_enablement": False,
        "changed_file_count": len(contract["governance"]["changed_file_whitelist"]),
        "scientific_code_bindings": contract["governance"]["scientific_code_bindings"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
