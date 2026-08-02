#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import pathlib
import subprocess
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC_PATH = HERE / "draw_auto_research_spec_r1.json"
IDENTITY_PATH = HERE / "draw_auto_research_identity_r1.json"
AUTH_PATH = HERE / "draw_composite_run_authorization_r1.json"
WORKFLOW_REL = ".github/workflows/football-draw-auto-research-r1.yml"
WORKFLOW_PATH = ROOT / WORKFLOW_REL
BASE_SHA = "605abf2d9f98c46f063106c7bd47193b96e588e4"
AUTH_REL = "football-data/research/draw_composite_run_authorization_r1.json"
ALLOWED_PATHS = {
    WORKFLOW_REL,
    "football-data/research/draw_composite_route_inventory_r1.json",
    "football-data/research/draw_composite_raw_field_pit_ledger_r1.json",
    "football-data/research/draw_composite_preregistration_r1.json",
    "football-data/research/draw_composite_research_plan_r1.md",
    "football-data/research/draw_composite_execution_contract_r1.json",
    "football-data/research/draw_composite_prereg_integrity_receipt_r1.json",
    "football-data/research/validate_draw_composite_prereg_r1.py",
    "football-data/research/draw_auto_research_spec_r1.json",
    "football-data/research/draw_auto_research_math_r1.py",
    "football-data/research/draw_auto_research_baseline_r1.py",
    "football-data/research/draw_auto_research_baseline_receipt_r1.json",
    "football-data/research/draw_auto_research_gate_r1.py",
    "football-data/research/draw_auto_research_engine_r1.py",
    "football-data/research/draw_auto_research_engine_data_r1.py",
    "football-data/research/draw_auto_research_engine_fit_r1.py",
    "football-data/research/draw_auto_research_engine_eval_r1.py",
    "football-data/research/draw_auto_research_controller_r1.py",
    "football-data/research/draw_auto_research_run_wrapper_r1.py",
    "football-data/research/draw_auto_research_restore_r1.py",
    "football-data/research/draw_auto_research_artifact_fallback_r1.py",
    "football-data/research/draw_auto_research_synthetic_evidence_r1.py",
    "football-data/research/draw_auto_research_synthetic_evidence_receipt_r1.json",
    "football-data/research/validate_draw_auto_research_preflight_r1.py",
    "football-data/research/validate_draw_auto_research_preflight_impl_r1.py",
    "football-data/research/test_draw_auto_research_r1.py",
    "football-data/research/test_draw_auto_artifact_fallback_r1.py",
    "football-data/research/test_draw_auto_research_impl_r1.py",
    "football-data/research/validate_draw_auto_authorization_r1.py",
    "football-data/research/test_draw_auto_authorization_r1.py",
    "football-data/research/draw_auto_research_identity_r1.json",
    AUTH_REL,
}


def run(*args: str) -> str:
    cp = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if cp.returncode:
        raise RuntimeError(f"command failed {' '.join(args)}: {cp.stderr.strip()}")
    return cp.stdout.strip()


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object required: {path}")
    return value


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def resolve_workflow_event(event_name: str, dispatch_mode: str, authorization_present: bool) -> dict[str, Any]:
    if dispatch_mode not in {"preflight", "research"}:
        raise ValueError(f"unsupported dispatch mode: {dispatch_mode}")
    preflight_mode = "authorized" if authorization_present else "preauth"
    research_allowed = bool(
        authorization_present
        and (
            event_name == "push"
            or (event_name == "workflow_dispatch" and dispatch_mode == "research")
        )
    )
    if event_name == "workflow_dispatch" and dispatch_mode == "preflight":
        research_allowed = False
    return {
        "authorized": bool(authorization_present),
        "research_allowed": research_allowed,
        "preflight_mode": preflight_mode,
    }


def validate_workflow_reference(path: str) -> None:
    if path != WORKFLOW_REL:
        raise ValueError(f"non-current workflow reference: {path}")


def verify_spec(spec: dict[str, Any]) -> None:
    if spec.get("status") != "GPT_REMEDIATED_PENDING_CODEX":
        raise ValueError("spec status mismatch")
    if spec.get("failed_r14_verdict") != "CODEX_FULL_RECHECK_FAILED_R1_4_DO_NOT_AUTHORIZE":
        raise ValueError("R1.4 Codex failure not frozen")
    if spec.get("authorization_permitted_now") is not False:
        raise ValueError("authorization boundary mismatch")
    if spec.get("data_status") != "VIEWED_DEVELOPMENT_DATA" or spec.get("formal_weight") != 0:
        raise ValueError("boundary mismatch")
    if spec["candidate_catalog"].get("candidate_count") != 200:
        raise ValueError("candidate count mismatch")
    if "draw_logit_offset" not in spec["candidate_catalog"].get("prohibited_dimensions", []):
        raise ValueError("redundant dimension not prohibited")
    if spec["baseline"].get("candidate_parameters_used") != 0:
        raise ValueError("baseline contaminated")
    if len(spec.get("dataset_sha256") or {}) != 17:
        raise ValueError("dataset universe mismatch")
    failure = spec.get("failure_contract") or {}
    if not all(failure.get(key) is True for key in (
        "controller_nonzero_writes_receipt",
        "controller_nonzero_appends_ledger",
        "existing_checkpoint_terminalized_atomically",
        "probe_prioritizes_failure_receipt",
        "manifest_regenerated_after_failure",
        "exit_2_production_path_reachable",
    )):
        raise ValueError("failure contract incomplete")
    recovery = spec.get("artifact_recovery") or {}
    if not all(recovery.get(key) is True for key in (
        "actual_file_universe_equals_manifest_files_plus_manifest",
        "reject_unregistered_files",
        "reject_missing_files",
        "reject_absolute_paths",
        "reject_parent_traversal",
        "reject_duplicate_paths",
        "reject_symbolic_links",
        "validate_checkpoint_references",
        "api_query_failure_is_terminal",
        "download_or_extract_failure_is_terminal",
        "matching_but_all_incompatible_is_terminal",
        "integrity_failure_is_terminal",
    )):
        raise ValueError("artifact recovery contract incomplete")


def verify_workflow() -> dict[str, Any]:
    validate_workflow_reference(WORKFLOW_REL)
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    required = [
        "runs-on: ubuntu-latest",
        "max-parallel: 1",
        "fail-fast: false",
        "cancel-in-progress: false",
        "queue: max",
        "group: draw-auto-research-r14-${{ github.workflow }}-${{ github.ref_name }}",
        "draw_auto_research_run_wrapper_r1.py",
        "draw_auto_research_restore_r1.py",
        "draw_auto_research_artifact_fallback_r1.py",
        "test_draw_auto_artifact_fallback_r1.py",
        "actions/cache/restore@v4",
        "actions/cache/save@v4",
        "actions/upload-artifact@v4",
        "Restore original controller exit code",
        "slot: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]",
        "needs.zero-label-preflight.outputs.research_allowed == 'true'",
        "cross-platform-determinism",
        "windows-latest",
    ]
    missing = [token for token in required if token not in text]
    if missing:
        raise ValueError(f"workflow missing tokens: {missing}")
    if text.count("concurrency:") != 1 or text.index("concurrency:") > text.index("jobs:"):
        raise ValueError("concurrency must be workflow-level only")
    concurrency_block = text[text.index("concurrency:"):text.index("jobs:")]
    if "github.sha" in concurrency_block:
        raise ValueError("concurrency group must not isolate commits by SHA")
    forbidden = ["secrets.", "cancel-in-progress: true", "set +e"]
    if any(token.lower() in text.lower() for token in forbidden):
        raise ValueError("forbidden workflow token")
    fallback_text = (HERE / "draw_auto_research_artifact_fallback_r1.py").read_text(encoding="utf-8")
    for token in ("NO_HISTORICAL_ARTIFACT_CONFIRMED", "MATCHING_ARTIFACTS_FOUND_BUT_NONE_COMPATIBLE", "ARTIFACT_API_QUERY_FAILED", "ARTIFACT_DOWNLOAD_OR_EXTRACT_FAILED"):
        if token not in fallback_text:
            raise ValueError(f"artifact fallback missing status: {token}")
    combinations = {
        "push_authorized": resolve_workflow_event("push", "preflight", True),
        "dispatch_research_authorized": resolve_workflow_event("workflow_dispatch", "research", True),
        "dispatch_preflight_authorized": resolve_workflow_event("workflow_dispatch", "preflight", True),
    }
    if not combinations["push_authorized"]["research_allowed"]:
        raise ValueError("authorized push must allow research")
    if not combinations["dispatch_research_authorized"]["research_allowed"]:
        raise ValueError("authorized research dispatch must allow research")
    if combinations["dispatch_preflight_authorized"]["research_allowed"]:
        raise ValueError("preflight dispatch must not allow research")
    return {
        "workflow_path": WORKFLOW_REL,
        "standard_runner": True,
        "workflow_level_concurrency": True,
        "concurrency_group_by_workflow_and_ref": True,
        "queue_max": True,
        "matrix_slots": 14,
        "artifact_fallback_fail_closed": True,
        "cross_platform_determinism": True,
        "event_contracts": combinations,
    }


def verify_identity(identity: dict[str, Any]) -> dict[str, str]:
    output: dict[str, str] = {}
    for name, item in sorted((identity.get("files") or {}).items()):
        path = ROOT / item["path"]
        if not path.is_file():
            raise ValueError(f"identity path missing: {name}")
        blob = run("git", "hash-object", str(path.relative_to(ROOT)))
        if blob != item["git_blob_sha"]:
            raise ValueError(f"identity blob mismatch: {name}")
        if item.get("canonical_json_sha256") and canonical_sha(read_json(path)) != item["canonical_json_sha256"]:
            raise ValueError(f"identity canonical mismatch: {name}")
        output[name] = blob
    return output


def verify_datasets(spec: dict[str, Any]) -> dict[str, Any]:
    required = {"competition_id", "season", "date", "home_team", "away_team", "label_result"}
    output: dict[str, Any] = {}
    for competition, expected in sorted(spec["dataset_sha256"].items()):
        path = ROOT / "football-data" / "training_datasets" / competition / "point_in_time.csv"
        if sha256_file(path) != expected:
            raise ValueError(f"dataset hash mismatch: {competition}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader([handle.readline()]))
        if required - set(header):
            raise ValueError(f"dataset header missing: {competition}")
        output[competition] = {"sha256": expected, "rows_parsed": 0, "labels_parsed": 0}
    return output


def verify_repository(mode: str) -> dict[str, Any]:
    head = run("git", "rev-parse", "HEAD")
    if run("git", "merge-base", BASE_SHA, head) != BASE_SHA:
        raise ValueError("history rewrite or wrong base")
    changed = [item for item in run("git", "diff", "--name-only", f"{BASE_SHA}..{head}").splitlines() if item]
    unexpected = sorted(set(changed) - ALLOWED_PATHS)
    if unexpected:
        raise ValueError(f"unexpected paths: {unexpected}")
    formal = [path for path in changed if path.startswith(("football-data/models/", "football-data/config/", "football-data/training_datasets/")) or "CURRENT_唯一正式规则" in path]
    if formal:
        raise ValueError(f"formal changes: {formal}")
    if mode == "preauth":
        if AUTH_PATH.exists():
            raise ValueError("authorization must be absent")
    else:
        if not AUTH_PATH.is_file():
            raise ValueError("authorization file missing")
        authorization = read_json(AUTH_PATH)
        frozen = str(authorization.get("frozen_code_head") or "")
        if len(frozen) != 40:
            raise ValueError("frozen_code_head missing")
        parents = run("git", "rev-list", "--parents", "-n", "1", head).split()
        if len(parents) != 2 or parents[1] != frozen:
            raise ValueError("authorization commit must directly follow frozen code HEAD")
        auth_diff = [item for item in run("git", "diff", "--name-only", f"{frozen}..{head}").splitlines() if item]
        if auth_diff != [AUTH_REL]:
            raise ValueError(f"authorization commit contains extra files: {auth_diff}")
    return {
        "exact_head": head,
        "changed_paths": changed,
        "unexpected_paths": unexpected,
        "formal_asset_changes": 0,
        "authorization_file_present": AUTH_PATH.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preauth", "authorized"), default="preauth")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        spec = read_json(SPEC_PATH)
        verify_spec(spec)
        workflow = verify_workflow()
        identity = verify_identity(read_json(IDENTITY_PATH))
        repository = verify_repository(args.mode)
        datasets = verify_datasets(spec)
        synthetic = read_json(HERE / "draw_auto_research_synthetic_evidence_receipt_r1.json")
        if not synthetic.get("all_basis_predictions_distinct") or not synthetic.get("baseline_candidate_independent"):
            raise ValueError("synthetic evidence failed")
        result = {
            "schema_version": "DRAW-AUTO-RESEARCH-PREFLIGHT-R1.4",
            "status": "PASS_ZERO_LABEL_PREFLIGHT",
            "mode": args.mode,
            "data_status": "VIEWED_DEVELOPMENT_DATA",
            "exact_head": repository["exact_head"],
            "workflow": workflow,
            "identity_files_checked": identity,
            "dataset_checks": datasets,
            "dataset_count": len(datasets),
            "candidate_catalog_count": 200,
            "synthetic_evidence_sha256": sha256_file(HERE / "draw_auto_research_synthetic_evidence_receipt_r1.json"),
            "synthetic_evidence_canonical_sha256": canonical_sha(synthetic),
            "rows_parsed": 0,
            "labels_parsed": 0,
            "training_runs": 0,
            "scoring_runs": 0,
            "experiment_executed": False,
            "artifact_count": 0,
            "provider_requests": 0,
            "api_football_requests": 0,
            "secret_context_references": 0,
            "repository_writeback": 0,
            "formal_weight": 0,
            "repository": repository,
        }
        result["canonical_json_sha256"] = canonical_sha(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {
            "schema_version": "DRAW-AUTO-RESEARCH-PREFLIGHT-R1.4",
            "status": "FAIL_CLOSED",
            "mode": args.mode,
            "error": str(exc),
            "rows_parsed": 0,
            "labels_parsed": 0,
            "training_runs": 0,
            "scoring_runs": 0,
            "experiment_executed": False,
            "artifact_count": 0,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
