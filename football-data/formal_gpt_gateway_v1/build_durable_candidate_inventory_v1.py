#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.request
from pathlib import Path
from typing import Any

import durable_state_contract_v1 as contract
import durable_state_selector_v1 as selector
import github_api_budget_transport_v1 as transport

SCHEMA = transport.INVENTORY_SCHEMA


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in ("id", "head_branch", "head_sha", "status", "conclusion")}


def _download_candidate_zip(repo: str, token: str, artifact_id: int, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip",
        headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
    )
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response:
        payload = response.read()
    target.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def _public_artifact(row: dict[str, Any], cache_root: Path, zip_sha: str) -> dict[str, Any]:
    workflow_run = row.get("workflow_run") or {}
    artifact_id = int(row.get("id") or 0)
    return {
        "id": artifact_id,
        "name": str(row.get("name") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": row.get("updated_at"),
        "expired": bool(row.get("expired")),
        "digest": row.get("digest"),
        "archive_download_url": row.get("archive_download_url"),
        "cached_zip_relpath": str((cache_root / f"{artifact_id}.zip").as_posix()),
        "cached_zip_sha256": zip_sha,
        "workflow_run": {
            "id": int(workflow_run.get("id") or 0),
            "head_branch": workflow_run.get("head_branch"),
            "head_sha": workflow_run.get("head_sha"),
            "status": workflow_run.get("status"),
            "conclusion": workflow_run.get("conclusion"),
        },
    }


def build(repo: str, token: str, out_path: Path, artifact_cache_dir: Path) -> dict[str, Any]:
    transport.reset_for_tests()
    transport.install()
    artifacts: list[dict[str, Any]] = []
    run_ids: set[int] = set()
    for artifact in selector._artifact_pages(repo, token):
        name = str(artifact.get("name") or "")
        workflow_run = artifact.get("workflow_run") or {}
        if artifact.get("expired") or not contract.artifact_role_ok(name):
            continue
        branch = workflow_run.get("head_branch")
        if branch is not None and branch not in selector.ALLOWED_HEAD_BRANCHES:
            continue
        run_id = int(workflow_run.get("id") or 0)
        if not run_id:
            continue
        artifacts.append(artifact)
        run_ids.add(run_id)

    runs = selector._run_records(repo, token, run_ids)
    artifacts = [
        artifact for artifact in artifacts
        if int((artifact.get("workflow_run") or {}).get("id") or 0) in runs
        and runs[int((artifact.get("workflow_run") or {}).get("id") or 0)].get("head_branch") in selector.ALLOWED_HEAD_BRANCHES
    ]
    artifacts.sort(key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)), reverse=True)

    artifact_cache_dir.mkdir(parents=True, exist_ok=True)
    public_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        artifact_id = int(artifact.get("id") or 0)
        zip_path = artifact_cache_dir / f"{artifact_id}.zip"
        zip_sha = _download_candidate_zip(repo, token, artifact_id, zip_path)
        enriched = dict(artifact)
        workflow_run = dict(enriched.get("workflow_run") or {})
        run = runs[int(workflow_run["id"])]
        for key in ("id", "head_branch", "head_sha", "status", "conclusion"):
            if run.get(key) is not None:
                workflow_run[key] = run.get(key)
        enriched["workflow_run"] = workflow_run
        public_artifacts.append(_public_artifact(enriched, Path(artifact_cache_dir.name), zip_sha))

    public_runs = sorted(
        (_public_run(runs[run_id]) for run_id in run_ids if run_id in runs and runs[run_id].get("head_branch") in selector.ALLOWED_HEAD_BRANCHES),
        key=lambda row: int(row.get("id") or 0),
    )
    core = {
        "schema_version": SCHEMA,
        "repository": repo,
        "artifacts": public_artifacts,
        "runs": public_runs,
    }
    inventory_sha = hashlib.sha256(canon(core)).hexdigest()
    stats = transport.snapshot()
    stats["inventory_sha"] = inventory_sha
    if stats["run_list_pages"] != 0:
        raise RuntimeError("repository-wide workflow run scan detected")
    if stats["individual_run_get_count"] > len(public_runs):
        raise RuntimeError("candidate run GET count exceeds deduplicated candidate run count")
    if stats["artifact_download_count"] != len(public_artifacts):
        raise RuntimeError("candidate artifact ZIP cache was not built exactly once")
    if stats["network_request_count"] > stats["artifact_list_pages"] + len(public_runs) + len(public_artifacts):
        raise RuntimeError("API request count exceeded linear candidate budget")
    result = {
        **core,
        "inventory_sha": inventory_sha,
        "candidate_artifact_count": len(public_artifacts),
        "candidate_run_count": len(public_runs),
        "api_stats": stats,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(canon(result))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--artifact-cache-dir", required=True)
    args = parser.parse_args()
    token = os.environ.get("GH_TOKEN") or ""
    if not token:
        raise SystemExit("GH_TOKEN required")
    result = build(args.repo, token, Path(args.out), Path(args.artifact_cache_dir))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
