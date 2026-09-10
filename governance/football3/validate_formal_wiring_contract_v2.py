from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

EXPECTED_BRANCH = "football3/historical-xg-fusion-v2-formal-wiring-governed-v1"
EXPECTED_INTEGRATION_BRANCH = "football3/formal-gpt-runner-integration-v1"
EXPECTED_ACTIVATION_BRANCH = "football3/historical-xg-fusion-v2-formal-activation-v1"
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
    "market_features", "market_validator_change", "new_target_labels", "retrain", "retune",
    "change_weight", "change_gate", "change_model_parameters", "post_view_repair",
    "future_research_queue", "CURRENT", "PR334/R5", "Ready", "merge", "force", "formal_enablement",
}
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


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
        "schema_version": 2, "project_id": "football3", "contract_kind": EXPECTED_KIND,
        "branch": EXPECTED_BRANCH, "status": EXPECTED_STATUS,
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
    require_exact_keys(contract, {
        "schema_version", "project_id", "contract_kind", "branch", "status",
        "research_acceptance", "frozen_v1", "fusion", "runtime", "governance", "forbidden",
    }, "contract")
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
    require_exact_keys(runtime, {"candidate_entry", "formal_enablement", "production_pointer_changed", "prospective_queue", "historical_completed_only_for_acceptance"}, "runtime")
    if runtime != {
        "candidate_entry": "football-data/new_engine_v1/formal_fusion_v2.py",
        "formal_enablement": False,
        "production_pointer_changed": False,
        "prospective_queue": False,
        "historical_completed_only_for_acceptance": True,
    }:
        fail("runtime governance must remain non-enabled historical-only")
    gov = contract["governance"]
    require_exact_keys(gov, {
        "mode", "market_features", "market_inputs", "market_baseline", "market_validator_semantics",
        "training", "tuning", "new_target_labels", "existing_frozen_historical_replay_only",
        "same_kickoff_isolation_required", "formal_enablement", "production_pointer_change",
        "whitelist_base_head", "changed_file_whitelist", "scientific_code_bindings",
        "immutable_market_governance_git_blobs", "immutable_formal_source_git_blobs",
    }, "governance")
    exact_nonmarket = {
        "mode": "FORMAL_WIRING_NON_MARKET", "market_features": False, "market_inputs": [],
        "market_baseline": False,
        "market_validator_semantics": "UNCHANGED_AND_NOT_APPLICABLE_TO_NON_MARKET_FORMAL_WIRING",
        "training": False, "tuning": False, "new_target_labels": False,
        "existing_frozen_historical_replay_only": True, "same_kickoff_isolation_required": True,
        "formal_enablement": False, "production_pointer_change": False,
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
    return matches[0] if len(matches) == 1 else None


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


def _runtime_branch() -> str:
    return (os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "").strip()


def _runtime_event() -> str:
    return (os.environ.get("GITHUB_EVENT_NAME") or "").strip()


def _git_head_sha(repo_root: Path) -> str:
    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()
    except Exception as exc:
        fail(f"cannot resolve checked-out HEAD: {exc}")
    if not _SHA_RE.fullmatch(sha):
        fail("checked-out HEAD SHA invalid")
    return sha


def _validate_integration_runtime_binding(repo_root: Path) -> str:
    event = _runtime_event()
    runtime = (os.environ.get("GITHUB_REF_NAME") or "").strip()
    ref = (os.environ.get("GITHUB_REF") or "").strip()
    head_ref = (os.environ.get("GITHUB_HEAD_REF") or "").strip()
    sha = (os.environ.get("GITHUB_SHA") or "").strip()
    if event not in {"push", "workflow_dispatch"}:
        fail(f"formal integration runtime event mismatch: {event or '<missing>'}")
    if runtime != EXPECTED_INTEGRATION_BRANCH:
        fail(f"formal integration runtime branch mismatch: expected={EXPECTED_INTEGRATION_BRANCH} runtime={runtime or '<missing>'}")
    if ref != f"refs/heads/{EXPECTED_INTEGRATION_BRANCH}":
        fail(f"formal integration runtime ref mismatch: expected=refs/heads/{EXPECTED_INTEGRATION_BRANCH} ref={ref or '<missing>'}")
    if head_ref:
        fail("formal integration runtime must not use pull-request head ref")
    if not _SHA_RE.fullmatch(sha):
        fail("formal integration runtime SHA invalid or missing")
    checked_out = _git_head_sha(repo_root)
    if checked_out != sha:
        fail(f"formal integration runtime SHA mismatch: github={sha} checkout={checked_out}")
    return sha


def _pull_request_base_sha() -> str | None:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request":
        return None
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path:
        fail("candidate authority requires GITHUB_EVENT_PATH")
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"candidate authority event unreadable: {exc}")
    sha = (((event or {}).get("pull_request") or {}).get("base") or {}).get("sha")
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        fail("candidate authority pull-request base SHA invalid")
    return sha


def validate_runtime_branch(contract: dict, repo_root: Path = Path("."), *, allow_activation_contract_context: bool = False) -> str:
    runtime = _runtime_branch()
    event = _runtime_event()
    ref = (os.environ.get("GITHUB_REF") or "").strip()
    integration_ref = f"refs/heads/{EXPECTED_INTEGRATION_BRANCH}"
    if runtime == EXPECTED_INTEGRATION_BRANCH or ref == integration_ref:
        _validate_integration_runtime_binding(repo_root)
        return "FORMAL_INTEGRATION_RUNTIME"
    if runtime == contract["branch"]:
        return "CONTRACT_BRANCH"
    if not runtime:
        if not event:
            return "CONTRACT_BRANCH"
        if (
            allow_activation_contract_context
            and event in {"push", "workflow_dispatch"}
            and ref == f"refs/heads/{EXPECTED_ACTIVATION_BRANCH}"
        ):
            return "CONTRACT_BRANCH"
        fail("runtime branch missing in GitHub event context")
    if event != "pull_request":
        fail(f"runtime branch mismatch outside pull-request candidate: contract={contract['branch']} runtime={runtime}")
    _pull_request_base_sha()
    return "PULL_REQUEST_CANDIDATE"


def _git_changed_files(repo_root: Path, base_head: str) -> set[str]:
    try:
        subprocess.check_call(["git", "merge-base", "--is-ancestor", base_head, "HEAD"], cwd=repo_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        out = subprocess.check_output(["git", "diff", "--name-only", f"{base_head}...HEAD"], cwd=repo_root, text=True)
    except Exception as exc:
        fail(f"cannot compute governed candidate diff: {exc}")
    return {line.strip() for line in out.splitlines() if line.strip()}


def _candidate_protected_paths(contract: dict) -> set[str]:
    gov = contract["governance"]
    protected = set(gov["immutable_market_governance_git_blobs"])
    protected.update(gov["immutable_formal_source_git_blobs"])
    protected.add("football-data/historical_xg_fusion_v2/contracts/FORMAL_FUSION_V2_WIRING.json")
    protected.add("governance/football3/formal_wiring_contract_schema_v2.json")
    protected.add(gov["scientific_code_bindings"]["runner"])
    protected.update(gov["scientific_code_bindings"]["helpers"])
    protected.add(gov["scientific_code_bindings"]["authority_guard"])
    return protected


def _is_additionally_protected_scientific_path(path: str) -> bool:
    if path.startswith("football-data/new_engine_v1/"):
        return True
    if path.startswith("football-data/historical_xg_fusion_v2/contracts/"):
        return True
    lowered = path.lower()
    if path.startswith("football-data/config/") and Path(path).name.casefold() == "current":
        return True
    if "formal_model_pointer" in lowered:
        return True
    return False


def _git_blob_at_ref(repo_root: Path, ref: str, rel: str) -> str:
    try:
        blob = subprocess.check_output(["git", "rev-parse", f"{ref}:{rel}"], cwd=repo_root, text=True).strip()
    except Exception as exc:
        fail(f"cannot resolve protected formal_wiring path at {ref}: {rel}: {exc}")
    if not _SHA_RE.fullmatch(blob):
        fail(f"protected formal_wiring blob identity invalid at {ref}: {rel}")
    return blob


def validate_integration_runtime_surface(contract: dict, repo_root: Path) -> set[str]:
    baseline_ref = f"refs/remotes/origin/{EXPECTED_BRANCH}"
    protected = _candidate_protected_paths(contract)
    for rel in sorted(protected):
        baseline = _git_blob_at_ref(repo_root, baseline_ref, rel)
        current = _git_blob_at_ref(repo_root, "HEAD", rel)
        if current != baseline:
            fail(
                "formal integration protected surface drift: "
                f"path={rel} governed={baseline} runtime={current}"
            )
    return protected


def validate_changed_files(contract: dict, repo_root: Path, base_head: str, authority_mode: str) -> tuple[set[str], str]:
    if authority_mode == "CONTRACT_BRANCH":
        if base_head != EXPECTED_BASE_HEAD or base_head != contract["governance"]["whitelist_base_head"]:
            fail("diff base must equal frozen cumulative research wiring HEAD")
        changed = _git_changed_files(repo_root, base_head)
        expected = set(contract["governance"]["changed_file_whitelist"])
        if changed != expected:
            fail(f"remediation diff scope mismatch: changed={sorted(changed)} expected={sorted(expected)}")
        return changed, base_head
    if authority_mode == "FORMAL_INTEGRATION_RUNTIME":
        protected = validate_integration_runtime_surface(contract, repo_root)
        return protected, _git_head_sha(repo_root)
    candidate_base = _pull_request_base_sha()
    assert candidate_base is not None
    changed = _git_changed_files(repo_root, candidate_base)
    protected = _candidate_protected_paths(contract)
    violations = sorted(path for path in changed if path in protected or _is_additionally_protected_scientific_path(path))
    if violations:
        fail(f"candidate scientific authority violation: {violations}")
    if not changed:
        fail("candidate authority diff must not be empty")
    return changed, candidate_base


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
    authority_mode = validate_runtime_branch(
        contract,
        args.repo_root,
        allow_activation_contract_context=args.skip_diff,
    )
    validate_source_bindings(contract, args.repo_root)
    validate_repo_locks(contract, args.repo_root)
    if args.skip_diff:
        if authority_mode != "CONTRACT_BRANCH":
            fail("skip-diff is forbidden outside contract branch context")
        changed: set[str] = set()
        diff_base = args.base_head
    else:
        changed, diff_base = validate_changed_files(contract, args.repo_root, args.base_head, authority_mode)
    print(json.dumps({
        "status": "FORMAL_WIRING_GOVERNANCE_V2_PASS",
        "contract_kind": contract["contract_kind"],
        "branch": contract["branch"],
        "runtime_branch": _runtime_branch() or None,
        "authority_mode": authority_mode,
        "base_head": diff_base,
        "market_features": False,
        "market_validator_semantics": "UNCHANGED",
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "formal_enablement": False,
        "changed_file_count": (
            len(changed)
            if authority_mode in {"PULL_REQUEST_CANDIDATE", "FORMAL_INTEGRATION_RUNTIME"}
            else len(contract["governance"]["changed_file_whitelist"])
        ),
        "scientific_code_bindings": contract["governance"]["scientific_code_bindings"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
