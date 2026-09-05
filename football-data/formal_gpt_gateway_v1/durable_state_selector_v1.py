#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

import durable_state_contract_v1 as contract
import runtime as rt

SCHEMA = contract.SELECTION_SCHEMA
ALLOWED_HEAD_BRANCHES = {
    "football3/formal-gpt-runner-integration-v1",
    "football3/formal-gpt-runner-request-carrier-v1",
    "football3/durable-state-cutoff-selector-governed-v1",
}


def _get_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req) as response:
        obj = json.load(response)
    if type(obj) is not dict:
        raise rt.RuntimeGateError("GitHub JSON object required")
    return obj


def _download(url: str, token: str, path: Path) -> None:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    # GitHub's artifact endpoint redirects to signed object storage. Keep the
    # repository token on the GitHub request only; forwarding it to the storage
    # host overrides the signed URL's authentication and yields HTTP 401.
    req.add_unredirected_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as response, path.open("wb") as out:
        shutil.copyfileobj(response, out)


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise rt.RuntimeGateError("unsafe durable state artifact path")
        archive.extractall(dest)


def _candidate_from_bundle(artifact: dict[str, Any], run: dict[str, Any], bundle_dir: Path,
                           target_cutoff, competition_id: str) -> dict[str, Any]:
    base = {
        "artifact_id": int(artifact.get("id") or 0),
        "artifact_name": str(artifact.get("name") or ""),
        "artifact_created_at": str(artifact.get("created_at") or ""),
        "artifact_digest": artifact.get("digest"),
        "artifact_role_ok": contract.artifact_role_ok(str(artifact.get("name") or "")),
        "verified": run.get("status") == "completed" and run.get("conclusion") == "success",
        "schema_ok": False,
        "runtime_ok": False,
        "model_current_ok": False,
        "competition_scope_ok": False,
        "pit_ok": False,
        "competition_id": competition_id,
        "head_branch": run.get("head_branch"),
        "workflow_run_id": run.get("id"),
    }
    try:
        loaded = rt.validate_bundle(bundle_dir)
        meta = loaded["meta"]
        state_cutoff = rt._parse_dt(str(meta.get("historical_cutoff")), "state cutoff")
        max_source = contract.max_source_observed_at(loaded)
        base.update({
            "schema_ok": True,
            "runtime_ok": (
                meta.get("schema_version") == rt.BUNDLE_SCHEMA
                and meta.get("formal_scope") == list(rt.FORMAL_SCOPE)
            ),
            "model_current_ok": (
                meta.get("formal_head") == rt.FORMAL_HEAD
                and meta.get("current_sha256") == rt.CURRENT_SHA256
            ),
            "competition_scope_ok": competition_id in rt.FORMAL_SCOPE and competition_id in meta.get("formal_scope", []),
            "pit_ok": (
                state_cutoff <= target_cutoff
                and rt._parse_dt(max_source, "max source observed at") <= state_cutoff
            ),
            "state_cutoff": state_cutoff.isoformat(),
            "max_source_observed_at": max_source,
            "state_sha256": loaded["manifest"]["state_sha256"],
            "state_bundle_sha256": loaded["manifest"]["state_bundle_sha256"],
            "formal_head": meta.get("formal_head"),
            "current_sha256": meta.get("current_sha256"),
            "runtime_contract_sha256": contract.runtime_contract_payload()["runtime_contract_sha256"],
        })
    except Exception as exc:
        base["bundle_validation_error"] = f"{type(exc).__name__}:{exc}"
    return base


def _artifact_pages(repo: str, token: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for page in range(1, 11):
        data = _get_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}", token)
        rows = data.get("artifacts") or []
        if type(rows) is not list:
            raise rt.RuntimeGateError("artifact list schema mismatch")
        out.extend(x for x in rows if type(x) is dict)
        if len(rows) < 100:
            break
    return out


def select_from_github(repo: str, token: str, request: dict[str, Any], cache_root: Path, audit_path: Path) -> dict[str, Any]:
    match = request.get("match")
    if type(match) is not dict:
        raise rt.RuntimeGateError("cutoff-aware durable selector requires prediction match")
    competition_id = str(match.get("competition_id") or "")
    target_cutoff = rt._parse_dt(str(match.get("cutoff") or ""), "target cutoff")
    if competition_id not in rt.FORMAL_SCOPE:
        raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")

    candidates: list[dict[str, Any]] = []
    work = Path(tempfile.mkdtemp(prefix="football3-durable-selector-"))
    try:
        for artifact in _artifact_pages(repo, token):
            name = str(artifact.get("name") or "")
            workflow_run = artifact.get("workflow_run") or {}
            if artifact.get("expired") or not contract.artifact_role_ok(name):
                continue
            if workflow_run.get("head_branch") not in ALLOWED_HEAD_BRANCHES:
                continue
            artifact_id = int(artifact.get("id") or 0)
            run_id = int(workflow_run.get("id") or 0)
            row_dir = work / str(artifact_id)
            row_dir.mkdir(parents=True, exist_ok=True)
            run = _get_json(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", token)
            zip_path = row_dir / "state.zip"
            bundle_dir = row_dir / "bundle"
            try:
                _download(f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip", token, zip_path)
                _safe_extract(zip_path, bundle_dir)
                candidate = _candidate_from_bundle(artifact, run, bundle_dir, target_cutoff, competition_id)
            except Exception as exc:
                candidate = {
                    "artifact_id": artifact_id,
                    "artifact_name": name,
                    "artifact_created_at": str(artifact.get("created_at") or ""),
                    "artifact_role_ok": True,
                    "verified": run.get("status") == "completed" and run.get("conclusion") == "success",
                    "schema_ok": False,
                    "runtime_ok": False,
                    "model_current_ok": False,
                    "competition_scope_ok": False,
                    "pit_ok": False,
                    "competition_id": competition_id,
                    "bundle_validation_error": f"{type(exc).__name__}:{exc}",
                }
            candidate["_bundle_dir"] = str(bundle_dir)
            candidates.append(candidate)

        selected, evaluated = contract.choose_candidate(candidates, target_cutoff, competition_id)
        public_evaluated = []
        for row in evaluated:
            clean = {k: v for k, v in row.items() if not k.startswith("_")}
            public_evaluated.append(clean)
        if selected is None:
            audit = {
                "schema_version": SCHEMA,
                "status": "DATA_STATE_ANOMALY",
                "reason": "NO_ELIGIBLE_DURABLE_STATE",
                "target_cutoff": target_cutoff.isoformat(),
                "competition_id": competition_id,
                "runtime_contract": contract.runtime_contract_payload(),
                "candidates": public_evaluated,
            }
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            audit_path.write_bytes(contract.canon(audit))
            return audit

        cache_root.mkdir(parents=True, exist_ok=True)
        bundle_target = cache_root / "bundle"
        shutil.rmtree(bundle_target, ignore_errors=True)
        shutil.copytree(Path(str(selected["_bundle_dir"])), bundle_target)
        loaded = rt.validate_bundle(bundle_target)
        selected_public = {k: v for k, v in selected.items() if not k.startswith("_")}
        audit_core = {
            "schema_version": SCHEMA,
            "status": "SELECTED",
            "selection_rule": "eligible_then_max_state_cutoff_created_at_tiebreak",
            "target_cutoff": target_cutoff.isoformat(),
            "competition_id": competition_id,
            "selected": selected_public,
            "runtime_contract": contract.runtime_contract_payload(),
            "candidate_count": len(public_evaluated),
            "candidates": public_evaluated,
        }
        audit = {**audit_core, "selection_sha256": contract.sha(audit_core)}
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        audit_path.write_bytes(contract.canon(audit))
        (cache_root / "durable_state_selection_v1.json").write_bytes(contract.canon(audit))
        # Revalidate after copy; no altered bundle can pass through selection.
        if loaded["manifest"]["state_sha256"] != selected_public["state_sha256"]:
            raise rt.RuntimeGateError("selected durable state SHA changed after copy")
        return audit
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--request", required=True)
    ap.add_argument("--cache-root", required=True)
    ap.add_argument("--audit", required=True)
    args = ap.parse_args()
    token = os.environ.get("GH_TOKEN") or ""
    if not token:
        raise SystemExit("GH_TOKEN required")
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    audit = select_from_github(args.repo, token, request, Path(args.cache_root), Path(args.audit))
    print(json.dumps(audit, sort_keys=True))
    return 0 if audit.get("status") == "SELECTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
