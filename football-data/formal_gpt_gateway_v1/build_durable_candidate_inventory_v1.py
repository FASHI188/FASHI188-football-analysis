#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import durable_state_contract_v1 as contract
import durable_state_selector_v1 as selector
import github_api_budget_transport_v1 as transport

SCHEMA = transport.INVENTORY_SCHEMA


def canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _public_artifact(row: dict[str, Any]) -> dict[str, Any]:
    wr = row.get("workflow_run") or {}
    return {
        "id": int(row.get("id") or 0),
        "name": str(row.get("name") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": row.get("updated_at"),
        "expired": bool(row.get("expired")),
        "digest": row.get("digest"),
        "archive_download_url": row.get("archive_download_url"),
        "workflow_run": {
            "id": int(wr.get("id") or 0),
            "head_branch": wr.get("head_branch"),
            "head_sha": wr.get("head_sha"),
            "status": wr.get("status"),
            "conclusion": wr.get("conclusion"),
        },
    }


def _public_run(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row.get(k) for k in ("id", "head_branch", "head_sha", "status", "conclusion")}


def build(repo: str, token: str, out_path: Path) -> dict[str, Any]:
    transport.reset_for_tests()
    transport.install()
    artifacts: list[dict[str, Any]] = []
    run_ids: set[int] = set()
    for artifact in selector._artifact_pages(repo, token):
        name = str(artifact.get("name") or "")
        wr = artifact.get("workflow_run") or {}
        if artifact.get("expired") or not contract.artifact_role_ok(name):
            continue
        branch = wr.get("head_branch")
        if branch is not None and branch not in selector.ALLOWED_HEAD_BRANCHES:
            continue
        run_id = int(wr.get("id") or 0)
        if not run_id:
            continue
        artifacts.append(artifact)
        run_ids.add(run_id)
    runs = selector._run_records(repo, token, run_ids)
    artifacts = [a for a in artifacts if runs[int((a.get("workflow_run") or {}).get("id") or 0)].get("head_branch") in selector.ALLOWED_HEAD_BRANCHES]
    public_artifacts = sorted((_public_artifact(a) for a in artifacts), key=lambda x: (x["created_at"], x["id"]), reverse=True)
    public_runs = sorted((_public_run(runs[x]) for x in run_ids if x in runs and runs[x].get("head_branch") in selector.ALLOWED_HEAD_BRANCHES), key=lambda x: int(x.get("id") or 0))
    core = {
        "schema_version": SCHEMA,
        "repository": repo,
        "artifacts": public_artifacts,
        "runs": public_runs,
    }
    inventory_sha = hashlib.sha256(canon(core)).hexdigest()
    stats = transport.snapshot()
    stats["inventory_sha"] = inventory_sha
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or ""
    if not token:
        raise SystemExit("GH_TOKEN required")
    result = build(args.repo, token, Path(args.out))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
