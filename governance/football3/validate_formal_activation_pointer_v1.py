from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

EXPECTED_BRANCH = "football3/historical-xg-fusion-v2-formal-activation-v1"
EXPECTED_BASE = "a142014d9355cc950916f7445e82310471d2d9b2"
EXPECTED_ARTIFACT_DIGEST = "sha256:0bed3e3d01f7fee952047f0c48d5496294bc7d0ee51d30eb731ac0bc1be3d936"
EXPECTED_ENTRY = "football-data/new_engine_v1/formal_fusion_v2.py"
EXPECTED_SCOPE = {
    "ARG_Primera", "BRA_SerieA", "ENG_PremierLeague", "ESP_LaLiga",
    "FRA_Ligue1", "GER_Bundesliga", "ITA_SerieA", "JPN_J1",
    "KOR_KLeague1", "NED_Eredivisie", "NOR_Eliteserien",
    "POR_PrimeiraLiga", "SCO_Premiership", "SUI_SuperLeague",
    "SWE_Allsvenskan", "USA_MLS",
}
EXPECTED_CHANGED = {
    ".github/workflows/football3-full-stack-remediation.yml",
    ".github/workflows/football3-historical-xg-fusion-v2-formal-activation.yml",
    "football-data/config/formal_model_pointer_historical_xg_fusion_v2.json",
    "governance/football3/formal_activation_pointer_schema_v1.json",
    "governance/football3/test_validate_formal_activation_pointer_v1.py",
    "governance/football3/validate_formal_activation_pointer_v1.py",
}
EXPECTED_ACCEPTED_BLOBS = {
    "football-data/new_engine_v1/formal_fusion_v2.py": "a5ed26d5ffd4a2875cb9c658cfaa28665a8b7871",
    "football-data/new_engine_v1/test_formal_fusion_v2.py": "61b25cd403fa1d9efa0dfcbc1643e0f17621944e",
    "football-data/historical_xg_fusion_v2/contracts/FORMAL_FUSION_V2_WIRING.json": "523b1a9062ad63d2cca3f713cde72e0d4abb659f",
}


class ActivationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise ActivationError(message)


def load_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"unreadable JSON {path}: {exc}")
    if type(value) is not dict:
        fail(f"JSON root must be object: {path}")
    return value


def git_blob_sha1(path: Path, repo: Path) -> str:
    """Return the canonical Git blob identity, independent of CRLF checkout mode."""
    try:
        return subprocess.check_output(
            ["git", "hash-object", str(path.relative_to(repo))],
            cwd=repo,
            text=True,
        ).strip()
    except Exception as exc:
        fail(f"cannot compute Git blob identity for {path}: {exc}")


def validate_schema(schema: dict) -> None:
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        fail("activation schema draft mismatch")
    if schema.get("$id") != "football3://governance/formal-activation-pointer-schema-v1":
        fail("activation schema id mismatch")
    if schema.get("additionalProperties") is not False:
        fail("activation schema must default deny unknown fields")
    props = schema.get("properties")
    if type(props) is not dict:
        fail("activation schema properties missing")
    for key, value in {
        "formal_enablement": True,
        "production_pointer_changed": True,
        "market_features": False,
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "prospective_queue": False,
        "exact_score_gate_changed": False,
        "ready": False,
        "merge": False,
    }.items():
        if props.get(key, {}).get("const") is not value:
            fail(f"activation schema constant drift: {key}")


def validate_pointer(pointer: dict, schema: dict) -> None:
    validate_schema(schema)
    expected_keys = set(schema["required"])
    if set(pointer) != expected_keys:
        fail(f"pointer keys mismatch: missing={sorted(expected_keys-set(pointer))} extra={sorted(set(pointer)-expected_keys)}")
    expected = {
        "schema_version": "football3-formal-model-pointer-v1",
        "project_id": "football3",
        "status": "ACTIVE_WHEN_EXTERNAL_CURRENT_SELECTS_EXACT_HEAD",
        "branch": EXPECTED_BRANCH,
        "authority": "EXTERNAL_UNIQUE_CURRENT_ONLY",
        "formal_current_stored_in_github": False,
        "requires_current_exact_head_match": True,
        "entry": EXPECTED_ENTRY,
        "outside_scope_route": "EXISTING_CURRENT_FORMAL_OR_COVERAGE_ROUTE",
        "formal_enablement": True,
        "production_pointer_changed": True,
        "market_features": False,
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "prospective_queue": False,
        "exact_score_gate_changed": False,
        "ready": False,
        "merge": False,
    }
    for key, value in expected.items():
        if pointer.get(key) != value:
            fail(f"activation pointer drift: {key}")
    if pointer.get("accepted_wiring") != {
        "head": EXPECTED_BASE,
        "run_id": 33617297624,
        "artifact_id": 9841488040,
        "artifact_digest": EXPECTED_ARTIFACT_DIGEST,
    }:
        fail("accepted wiring identity drift")
    if pointer.get("model") != {
        "name": "Historical XG Fusion V2",
        "xg_weight": 0.75,
        "frozen_v1_weight": 0.25,
        "formula": "normalize((1-w)*p_V1 + w*p_XG)",
        "xg_insufficient": "FROZEN_V1_EXACT_FALLBACK",
        "internal_candidate_marker_expected": False,
    }:
        fail("frozen model selection drift")
    scope = pointer.get("formal_scope")
    if type(scope) is not list or len(scope) != 16 or set(scope) != EXPECTED_SCOPE:
        fail("formal competition scope drift")


def validate_repo(repo: Path, *, check_diff: bool = True) -> None:
    pointer = load_object(repo / "football-data/config/formal_model_pointer_historical_xg_fusion_v2.json")
    schema = load_object(repo / "governance/football3/formal_activation_pointer_schema_v1.json")
    validate_pointer(pointer, schema)
    for rel, expected in EXPECTED_ACCEPTED_BLOBS.items():
        path = repo / rel
        if not path.is_file() or git_blob_sha1(path, repo) != expected:
            fail(f"accepted wiring blob drift: {rel}")
    runtime_branch = (os.environ.get("GITHUB_HEAD_REF") or os.environ.get("GITHUB_REF_NAME") or "").strip()
    if runtime_branch and runtime_branch != EXPECTED_BRANCH:
        fail(f"runtime branch mismatch: {runtime_branch}")
    if check_diff:
        changed = set(subprocess.check_output(
            ["git", "diff", "--name-only", f"{EXPECTED_BASE}...HEAD"], cwd=repo, text=True
        ).splitlines())
        if changed != EXPECTED_CHANGED:
            fail(f"activation diff mismatch: changed={sorted(changed)} expected={sorted(EXPECTED_CHANGED)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--skip-diff", action="store_true")
    args = parser.parse_args()
    validate_repo(args.repo_root.resolve(), check_diff=not args.skip_diff)
    print(json.dumps({
        "status": "FORMAL_ACTIVATION_POINTER_V1_PASS",
        "branch": EXPECTED_BRANCH,
        "accepted_head": EXPECTED_BASE,
        "xg_weight": 0.75,
        "frozen_v1_weight": 0.25,
        "scope_n": 16,
        "external_current_required": True,
        "market_features": False,
        "training": False,
        "tuning": False,
        "new_target_labels": False,
        "ready": False,
        "merge": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
