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
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "football-draw-composite-prereg-r1.yml"
BASE_SHA = "605abf2d9f98c46f063106c7bd47193b96e588e4"
AUTH_REL = "football-data/research/draw_composite_run_authorization_r1.json"

ALLOWED_PATHS = {
    ".github/workflows/football-draw-composite-prereg-r1.yml",
    "football-data/research/draw_composite_route_inventory_r1.json",
    "football-data/research/draw_composite_raw_field_pit_ledger_r1.json",
    "football-data/research/draw_composite_preregistration_r1.json",
    "football-data/research/draw_composite_research_plan_r1.md",
    "football-data/research/draw_composite_execution_contract_r1.json",
    "football-data/research/draw_composite_prereg_integrity_receipt_r1.json",
    "football-data/research/validate_draw_composite_prereg_r1.py",
    "football-data/research/draw_auto_research_spec_r1.json",
    "football-data/research/draw_auto_research_math_r1.py",
    "football-data/research/draw_auto_research_engine_r1.py",
    "football-data/research/draw_auto_research_controller_r1.py",
    "football-data/research/validate_draw_auto_research_preflight_r1.py",
    "football-data/research/test_draw_auto_research_r1.py",
    "football-data/research/draw_auto_research_identity_r1.json",
    AUTH_REL,
}


def run(*args: str) -> str:
    completed = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if completed.returncode:
        raise RuntimeError(f"command failed {' '.join(args)}: {completed.stderr.strip()}")
    return completed.stdout.strip()


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
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_spec(spec: dict[str, Any]) -> None:
    if spec.get("status") != "FROZEN_VIEWED_DEVELOPMENT_RESEARCH_PENDING_CODEX":
        raise ValueError("spec status mismatch")
    if spec.get("user_authorization_record") != "rec0WJJzXiuDvAqSb":
        raise ValueError("authorization record mismatch")
    if spec.get("data_status") != "VIEWED_DEVELOPMENT_DATA" or spec.get("formal_weight") != 0:
        raise ValueError("data/formal boundary mismatch")
    budget = spec["budget"]
    expected = {"batch_size": 20, "maximum_candidates": 200, "maximum_cumulative_seconds": 21600, "maximum_stagnant_batches": 3}
    for key, value in expected.items():
        if budget.get(key) != value:
            raise ValueError(f"budget mismatch: {key}")
    if spec["validation"].get("random_split") is not False:
        raise ValueError("random split must be false")
    if spec["validation"].get("outer_evaluation_for_preprocessing_decisions") != 0:
        raise ValueError("outer evaluation preprocessing gate mismatch")
    if spec["candidate_catalog"].get("candidate_count") != 200:
        raise ValueError("candidate count mismatch")
    if len(spec.get("dataset_sha256") or {}) != 17:
        raise ValueError("dataset universe mismatch")
    if spec["security"].get("repository_or_environment_secrets") is not False:
        raise ValueError("secret boundary mismatch")
    boundary = spec["formal_boundary"]
    if any(boundary[key] != 0 for key in ("formal_model_changes", "formal_data_changes", "config_changes", "current_changes")):
        raise ValueError("formal asset boundary mismatch")
    if boundary["ready_authorized"] or boundary["merge_authorized"] or boundary["formal_promotion_authorized"]:
        raise ValueError("governance boundary weakened")


def verify_workflow() -> dict[str, Any]:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    required = ["runs-on: ubuntu-latest", "max-parallel: 1", "fail-fast: false", "cancel-in-progress: false", "draw_auto_research_controller_r1.py", "actions/cache/restore@v4", "actions/cache/save@v4", "actions/upload-artifact@v4", "matrix:", "slot:", "VIEWED_DEVELOPMENT_DATA"]
    missing = [token for token in required if token not in text]
    if missing:
        raise ValueError(f"workflow missing tokens: {missing}")
    forbidden = ["secrets.", "API_FOOTBALL", "api-football", "larger", "ubuntu-8", "ubuntu-16", "cancel-in-progress: true"]
    present = [token for token in forbidden if token.lower() in text.lower()]
    if present:
        raise ValueError(f"workflow forbidden tokens: {present}")
    slots_line = next((line for line in text.splitlines() if "slot:" in line and "[" in line), "")
    if not slots_line:
        raise ValueError("workflow recovery slots missing")
    return {"standard_runner": True, "secret_context_references": 0, "matrix_slots_declared": slots_line.strip()}


def verify_identity(identity: dict[str, Any]) -> dict[str, str]:
    files = identity.get("files") or {}
    if len(files) < 7:
        raise ValueError("identity file set incomplete")
    checked: dict[str, str] = {}
    for name, item in sorted(files.items()):
        path = ROOT / item["path"]
        if not path.is_file():
            raise ValueError(f"identity path missing: {name}")
        blob = run("git", "hash-object", str(path.relative_to(ROOT)))
        if blob != item["git_blob_sha"]:
            raise ValueError(f"identity blob mismatch {name}: {blob} != {item['git_blob_sha']}")
        if item.get("canonical_json_sha256") and canonical_sha(read_json(path)) != item["canonical_json_sha256"]:
            raise ValueError(f"identity canonical mismatch: {name}")
        checked[name] = blob
    return checked


def verify_datasets(spec: dict[str, Any]) -> dict[str, Any]:
    required_header = {"competition_id", "season", "date", "home_team", "away_team", "home_last5_gf", "home_last5_ga", "home_last5_ppg", "away_last5_gf", "away_last5_ga", "away_last5_ppg", "elo_difference_with_home_advantage", "label_result"}
    checks: dict[str, Any] = {}
    for competition, expected in sorted(spec["dataset_sha256"].items()):
        path = ROOT / "football-data" / "training_datasets" / competition / "point_in_time.csv"
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(f"dataset hash mismatch: {competition}")
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader([handle.readline()]))
        missing = required_header - set(header)
        if missing:
            raise ValueError(f"dataset header missing {competition}: {sorted(missing)}")
        checks[competition] = {"sha256": actual, "header_columns": len(header), "rows_parsed": 0, "labels_parsed": 0}
    return checks


def verify_repository(mode: str) -> dict[str, Any]:
    head = run("git", "rev-parse", "HEAD")
    if run("git", "merge-base", BASE_SHA, head) != BASE_SHA:
        raise ValueError("HEAD not descended from base")
    changed = [line for line in run("git", "diff", "--name-only", f"{BASE_SHA}..{head}").splitlines() if line]
    unexpected = sorted(set(changed) - ALLOWED_PATHS)
    if unexpected:
        raise ValueError(f"unexpected changed paths: {unexpected}")
    formal = [path for path in changed if path.startswith(("football-data/models/", "football-data/config/", "football-data/training_datasets/")) or "CURRENT_唯一正式规则" in path]
    if formal:
        raise ValueError(f"formal asset changes found: {formal}")
    authorization = None
    if mode == "preauth":
        if AUTH_PATH.exists():
            raise ValueError("authorization file must be absent on code preflight HEAD")
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
        auth_diff = [line for line in run("git", "diff", "--name-only", f"{frozen}..{head}").splitlines() if line]
        if auth_diff != [AUTH_REL]:
            raise ValueError(f"authorization commit contains extra files: {auth_diff}")
    return {"exact_head": head, "changed_paths": changed, "unexpected_paths": unexpected, "formal_asset_changes": 0, "authorization_file_present": AUTH_PATH.exists(), "authorization": authorization}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preauth", "authorized"), default="preauth")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        spec = read_json(SPEC_PATH)
        verify_spec(spec)
        workflow = verify_workflow()
        checked_identity = verify_identity(read_json(IDENTITY_PATH))
        repository = verify_repository(args.mode)
        datasets = verify_datasets(spec)
        result = {
            "schema_version": "DRAW-AUTO-RESEARCH-PREFLIGHT-R1.0",
            "status": "PASS_ZERO_LABEL_PREFLIGHT",
            "mode": args.mode,
            "data_status": "VIEWED_DEVELOPMENT_DATA",
            "user_authorization_record": "rec0WJJzXiuDvAqSb",
            "exact_head": repository["exact_head"],
            "workflow": workflow,
            "identity_files_checked": checked_identity,
            "dataset_checks": datasets,
            "dataset_count": len(datasets),
            "candidate_catalog_count": 200,
            "batch_size": 20,
            "maximum_cumulative_seconds": 21600,
            "rows_parsed": 0,
            "labels_parsed": 0,
            "training_runs": 0,
            "scoring_runs": 0,
            "experiment_executed": False,
            "provider_requests": 0,
            "api_football_requests": 0,
            "secret_context_references": 0,
            "repository_writeback": 0,
            "formal_weight": 0,
            "repository": repository,
        }
        result["canonical_json_sha256"] = canonical_sha(result)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {"schema_version": "DRAW-AUTO-RESEARCH-PREFLIGHT-R1.0", "status": "FAIL_CLOSED", "mode": args.mode, "error": str(exc), "rows_parsed": 0, "labels_parsed": 0, "training_runs": 0, "scoring_runs": 0, "experiment_executed": False}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
