#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import email.utils
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
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
_MAX_API_PAGES = 10
_MAX_HTTP_ATTEMPTS = 4
_MAX_RETRY_SLEEP_SECONDS = 60.0
_JSON_CACHE: dict[str, dict[str, Any]] = {}
_JSON_CACHE_LOCK = threading.RLock()


def _clear_process_cache_for_tests() -> None:
    with _JSON_CACHE_LOCK:
        _JSON_CACHE.clear()


def _http_error_details(exc: urllib.error.HTTPError, url: str) -> dict[str, Any]:
    try:
        raw = exc.read()
    except Exception:
        raw = b""
    text = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text) if text else {}
    except Exception:
        payload = {}
    if type(payload) is not dict:
        payload = {}
    headers = exc.headers
    return {
        "schema_version": "football3-github-api-http-error-v1",
        "status": int(exc.code),
        "url": url,
        "x-ratelimit-limit": headers.get("x-ratelimit-limit") if headers else None,
        "x-ratelimit-remaining": headers.get("x-ratelimit-remaining") if headers else None,
        "x-ratelimit-reset": headers.get("x-ratelimit-reset") if headers else None,
        "x-ratelimit-resource": headers.get("x-ratelimit-resource") if headers else None,
        "retry-after": headers.get("retry-after") if headers else None,
        "message": payload.get("message"),
        "documentation_url": payload.get("documentation_url"),
        "response_body": text,
    }


def _emit_http_error_details(details: dict[str, Any]) -> None:
    print(json.dumps(details, sort_keys=True), file=sys.stderr, flush=True)


def _permission_style_403(details: dict[str, Any]) -> bool:
    if int(details.get("status") or 0) != 403:
        return False
    message = str(details.get("message") or "").lower()
    return any(marker in message for marker in (
        "resource not accessible by integration",
        "must have admin rights",
        "requires authentication",
        "insufficient permission",
    ))


def _retryable_http_error(details: dict[str, Any]) -> bool:
    status = int(details.get("status") or 0)
    if status == 429:
        return True
    if status != 403 or _permission_style_403(details):
        return False
    message = str(details.get("message") or "").lower()
    documentation_url = str(details.get("documentation_url") or "").lower()
    remaining = str(details.get("x-ratelimit-remaining") or "")
    return bool(
        details.get("retry-after")
        or remaining == "0"
        or "rate limit" in message
        or "secondary rate" in message
        or "abuse detection" in message
        or "rate-limit" in documentation_url
        or "rate_limits" in documentation_url
    )


def _retry_delay_seconds(details: dict[str, Any], attempt_index: int) -> float:
    retry_after = details.get("retry-after")
    if retry_after is not None:
        value = str(retry_after).strip()
        try:
            return min(max(float(value), 0.0), _MAX_RETRY_SLEEP_SECONDS)
        except ValueError:
            try:
                target = email.utils.parsedate_to_datetime(value).timestamp()
                return min(max(target - time.time(), 0.0), _MAX_RETRY_SLEEP_SECONDS)
            except Exception:
                pass
    if str(details.get("x-ratelimit-remaining") or "") == "0":
        try:
            reset = float(str(details.get("x-ratelimit-reset") or ""))
            return min(max(reset - time.time(), 1.0), _MAX_RETRY_SLEEP_SECONDS)
        except ValueError:
            pass
    return min(float(2 ** attempt_index), _MAX_RETRY_SLEEP_SECONDS)


def _raise_http_gate(details: dict[str, Any]) -> None:
    raise rt.RuntimeGateError(
        "GITHUB_API_HTTP_ERROR:" + json.dumps(details, sort_keys=True, separators=(",", ":"))
    )


def _get_json(url: str, token: str) -> dict[str, Any]:
    # Deliberately hold the process-local lock through the network request. This
    # makes duplicate concurrent lookups collapse to one request instead of
    # racing the same GitHub endpoint and amplifying secondary-rate pressure.
    with _JSON_CACHE_LOCK:
        cached = _JSON_CACHE.get(url)
        if cached is not None:
            return copy.deepcopy(cached)
        for attempt in range(_MAX_HTTP_ATTEMPTS):
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            })
            try:
                with urllib.request.urlopen(req) as response:
                    obj = json.load(response)
            except urllib.error.HTTPError as exc:
                details = _http_error_details(exc, url)
                _emit_http_error_details(details)
                if _retryable_http_error(details) and attempt + 1 < _MAX_HTTP_ATTEMPTS:
                    time.sleep(_retry_delay_seconds(details, attempt))
                    continue
                _raise_http_gate(details)
            if type(obj) is not dict:
                raise rt.RuntimeGateError("GitHub JSON object required")
            _JSON_CACHE[url] = copy.deepcopy(obj)
            return copy.deepcopy(obj)
    raise rt.RuntimeGateError("GitHub JSON request exhausted without response")


def _download(url: str, token: str, path: Path) -> None:
    for attempt in range(_MAX_HTTP_ATTEMPTS):
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        # GitHub's artifact endpoint redirects to signed object storage. Keep the
        # repository token on the GitHub request only; forwarding it to the storage
        # host overrides the signed URL's authentication and yields HTTP 401.
        req.add_unredirected_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as response, path.open("wb") as out:
                shutil.copyfileobj(response, out)
            return
        except urllib.error.HTTPError as exc:
            details = _http_error_details(exc, url)
            _emit_http_error_details(details)
            if _retryable_http_error(details) and attempt + 1 < _MAX_HTTP_ATTEMPTS:
                time.sleep(_retry_delay_seconds(details, attempt))
                continue
            _raise_http_gate(details)
    raise rt.RuntimeGateError("GitHub artifact download exhausted without response")


def _safe_extract(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                raise rt.RuntimeGateError("unsafe durable state artifact path")
        archive.extractall(dest)


def _candidate_from_bundle(artifact: dict[str, Any], run: dict[str, Any], bundle_dir: Path,
                           target_cutoff, competition_id: str, artifact_availability_ceiling=None) -> dict[str, Any]:
    artifact_created_at = str(artifact.get("created_at") or "")
    availability_ceiling = artifact_availability_ceiling or target_cutoff
    base = {
        "artifact_id": int(artifact.get("id") or 0),
        "artifact_name": str(artifact.get("name") or ""),
        "artifact_created_at": artifact_created_at,
        "artifact_available_by_target_cutoff": False,
        "artifact_available_before_kickoff": False,
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
        artifact_created = rt._parse_dt(artifact_created_at, "artifact created at")
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
            "artifact_available_by_target_cutoff": artifact_created <= target_cutoff,
            "artifact_available_before_kickoff": artifact_created <= availability_ceiling,
            "pit_ok": (
                state_cutoff <= target_cutoff
                and artifact_created <= availability_ceiling
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
    dedup: dict[int, dict[str, Any]] = {}
    for page in range(1, _MAX_API_PAGES + 1):
        data = _get_json(f"https://api.github.com/repos/{repo}/actions/artifacts?per_page=100&page={page}", token)
        rows = data.get("artifacts") or []
        if type(rows) is not list:
            raise rt.RuntimeGateError("artifact list schema mismatch")
        for row in rows:
            if type(row) is not dict:
                continue
            artifact_id = int(row.get("id") or 0)
            if artifact_id:
                dedup.setdefault(artifact_id, row)
        if len(rows) < 100:
            break
    return sorted(
        dedup.values(),
        key=lambda row: (str(row.get("created_at") or ""), int(row.get("id") or 0)),
        reverse=True,
    )


def _run_records(repo: str, token: str, run_ids: set[int]) -> dict[int, dict[str, Any]]:
    wanted = {int(x) for x in run_ids if int(x) > 0}
    found: dict[int, dict[str, Any]] = {}
    if not wanted:
        return found
    # Batch-read repository workflow runs first. This replaces the previous N+1
    # /actions/runs/{id} loop. Individual GETs are a fail-closed fallback only
    # for IDs that do not appear in the bounded paginated batch window.
    for page in range(1, _MAX_API_PAGES + 1):
        data = _get_json(f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&page={page}", token)
        rows = data.get("workflow_runs") or []
        if type(rows) is not list:
            raise rt.RuntimeGateError("workflow run list schema mismatch")
        for row in rows:
            if type(row) is not dict:
                continue
            run_id = int(row.get("id") or 0)
            if run_id in wanted and run_id not in found:
                found[run_id] = row
        if wanted.issubset(found) or len(rows) < 100:
            break
    for run_id in sorted(wanted.difference(found)):
        found[run_id] = _get_json(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", token)
    return found


def _stable_public_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in rows]
    return sorted(
        public_rows,
        key=lambda row: (str(row.get("artifact_created_at") or ""), int(row.get("artifact_id") or 0)),
        reverse=True,
    )


def select_from_github(repo: str, token: str, request: dict[str, Any], cache_root: Path, audit_path: Path) -> dict[str, Any]:
    match = request.get("match")
    if type(match) is not dict:
        raise rt.RuntimeGateError("cutoff-aware durable selector requires prediction match")
    competition_id = str(match.get("competition_id") or "")
    target_cutoff = rt._parse_dt(str(match.get("cutoff") or ""), "target cutoff")
    kickoff = rt._parse_dt(str(match.get("kickoff") or ""), "kickoff")
    if competition_id not in rt.FORMAL_SCOPE:
        raise rt.RuntimeGateError("competition outside Formal Fusion V2 scope")

    artifacts = []
    run_ids: set[int] = set()
    for artifact in _artifact_pages(repo, token):
        name = str(artifact.get("name") or "")
        workflow_run = artifact.get("workflow_run") or {}
        if artifact.get("expired") or not contract.artifact_role_ok(name):
            continue
        if workflow_run.get("head_branch") not in ALLOWED_HEAD_BRANCHES:
            continue
        run_id = int(workflow_run.get("id") or 0)
        if not run_id:
            continue
        artifacts.append(artifact)
        run_ids.add(run_id)
    runs = _run_records(repo, token, run_ids)

    candidates: list[dict[str, Any]] = []
    work = Path(tempfile.mkdtemp(prefix="football3-durable-selector-"))
    try:
        for artifact in artifacts:
            name = str(artifact.get("name") or "")
            workflow_run = artifact.get("workflow_run") or {}
            artifact_id = int(artifact.get("id") or 0)
            run_id = int(workflow_run.get("id") or 0)
            run = runs.get(run_id)
            if type(run) is not dict:
                raise rt.RuntimeGateError(f"workflow run metadata unavailable for {run_id}")
            row_dir = work / str(artifact_id)
            row_dir.mkdir(parents=True, exist_ok=True)
            zip_path = row_dir / "state.zip"
            bundle_dir = row_dir / "bundle"
            try:
                _download(f"https://api.github.com/repos/{repo}/actions/artifacts/{artifact_id}/zip", token, zip_path)
                _safe_extract(zip_path, bundle_dir)
                candidate = _candidate_from_bundle(
                    artifact, run, bundle_dir, target_cutoff, competition_id, kickoff
                )
            except Exception as exc:
                candidate = {
                    "artifact_id": artifact_id,
                    "artifact_name": name,
                    "artifact_created_at": str(artifact.get("created_at") or ""),
                    "artifact_available_by_target_cutoff": False,
                    "artifact_available_before_kickoff": False,
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
        public_evaluated = _stable_public_candidates(evaluated)
        if selected is None:
            audit = {
                "schema_version": SCHEMA,
                "status": "DATA_STATE_ANOMALY",
                "reason": "NO_ELIGIBLE_DURABLE_STATE",
                "target_cutoff": target_cutoff.isoformat(),
                "prematch_artifact_availability_ceiling": kickoff.isoformat(),
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
            "selection_rule": "eligible_prematch_artifact_available_then_max_state_cutoff_created_at_tiebreak",
            "target_cutoff": target_cutoff.isoformat(),
            "prematch_artifact_availability_ceiling": kickoff.isoformat(),
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
