#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from typing import Any

import request_contract_v1 as request_contract

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"
SCHEMA = "football3-gpt-auto-dispatch-bridge-v3"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
CARRIER_REF = "football3/formal-gpt-runner-request-carrier-v1"
CARRIER_PR_NUMBER = 341
CARRIER_FILE = "FOOTBALL3_FORMAL_GPT_REQUEST_CARRIER.md"
FORMAL_WORKFLOW = "football3-formal-gpt-runner-integration-v1.yml"
FORMAL_RUN_PREFIX = "Football3 Formal GPT Runner Integration V1"
RECEIVER_WORKFLOW_NAME = "Football3 GPT Auto Dispatch Receiver V3"
TRUSTED_DISPATCHER_WORKFLOW_NAME = "Football3 GPT Auto Dispatch Trusted Dispatcher V1"
BEGIN_MARKER = "<!-- football3-request-json -->"
END_MARKER = "<!-- /football3-request-json -->"
ALLOWED_PERMISSIONS = frozenset({"admin", "maintain", "write"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BridgeError(RuntimeError):
    pass


def _fail(code: str) -> None:
    raise BridgeError(code)


def request_id_hash(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def ledger_names(request_id: str, request_sha: str) -> tuple[str, str]:
    token = request_id_hash(request_id)
    return (
        f"football3-auto-dispatch-reservation-{token}-{request_sha}",
        f"football3-auto-dispatch-completed-{token}-{request_sha}",
    )


def parse_request_body(body: str) -> dict[str, Any]:
    if not isinstance(body, str):
        _fail("AUTO_DISPATCH_REQUEST_BODY_INVALID")
    if body.count(BEGIN_MARKER) != 1 or body.count(END_MARKER) != 1:
        _fail("AUTO_DISPATCH_REQUEST_MARKERS_INVALID")
    raw = body.split(BEGIN_MARKER, 1)[1].split(END_MARKER, 1)[0].strip()
    try:
        return request_contract.parse_json(raw, carrier_request=True)
    except request_contract.FormalRequestContractError as exc:
        raise BridgeError(f"AUTO_DISPATCH_REQUEST_CONTRACT:{exc.code}") from exc


def trusted_request_mode(request: dict[str, Any]) -> str:
    mode = request.get("mode")
    if not isinstance(mode, str) or mode not in request_contract.FORMAL_PREDICTION_MODES:
        _fail("AUTO_DISPATCH_REQUEST_MODE_INVALID")
    return mode


def validate_live_pr(pr: Any, repo: str) -> None:
    if type(pr) is not dict or pr.get("number") != CARRIER_PR_NUMBER:
        _fail("AUTO_DISPATCH_LIVE_CARRIER_INVALID")
    if pr.get("state") != "open" or pr.get("draft") is not True or pr.get("merged_at") is not None:
        _fail("AUTO_DISPATCH_LIVE_CARRIER_NOT_OPEN_DRAFT")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    if base.get("ref") != CANONICAL_REF:
        _fail("AUTO_DISPATCH_LIVE_BASE_REF_UNAUTHORIZED")
    if head.get("ref") != CARRIER_REF or head_repo.get("full_name") != repo:
        _fail("AUTO_DISPATCH_LIVE_HEAD_UNAUTHORIZED")
    if not isinstance(base.get("sha"), str) or not SHA_RE.fullmatch(base["sha"]):
        _fail("AUTO_DISPATCH_LIVE_BASE_SHA_INVALID")
    if not isinstance(head.get("sha"), str) or not SHA_RE.fullmatch(head["sha"]):
        _fail("AUTO_DISPATCH_LIVE_HEAD_SHA_INVALID")


def validate_carrier_files(files: Any) -> None:
    if not isinstance(files, list) or any(not isinstance(x, str) for x in files):
        _fail("AUTO_DISPATCH_CARRIER_CHANGED_FILES_INVALID")
    if files != [CARRIER_FILE]:
        _fail("AUTO_DISPATCH_CARRIER_CHANGED_FILES_UNAUTHORIZED")


def validate_permission(permission: Any) -> str:
    if permission not in ALLOWED_PERMISSIONS:
        _fail(f"AUTO_DISPATCH_ACTOR_PERMISSION_DENIED:{permission}")
    return str(permission)


def classify_ledgers(request_id: str, request_sha: str, artifacts: list[dict[str, Any]]) -> tuple[str, list[int]]:
    reservation_name, completed_name = ledger_names(request_id, request_sha)
    token = request_id_hash(request_id)
    prefixes = (
        f"football3-auto-dispatch-reservation-{token}-",
        f"football3-auto-dispatch-completed-{token}-",
    )
    related = [a for a in artifacts if type(a) is dict and a.get("expired") is not True and isinstance(a.get("name"), str) and a["name"].startswith(prefixes)]
    allowed = {reservation_name, completed_name}
    if any(a.get("name") not in allowed for a in related):
        _fail("AUTO_DISPATCH_REQUEST_ID_CONTENT_MISMATCH")
    completed = [a for a in related if a.get("name") == completed_name]
    if completed:
        return "DUPLICATE_COMPLETED", [int(a["id"]) for a in completed if a.get("id") is not None]
    reservation = [a for a in related if a.get("name") == reservation_name]
    if reservation:
        return "RESERVATION_CONFLICT", [int(a["id"]) for a in reservation if a.get("id") is not None]
    return "NEW", []


def assert_request_unchanged(audit: dict[str, Any], request: dict[str, Any]) -> str:
    actual = request_contract.request_sha256(request)
    expected = audit.get("request_sha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        _fail("AUTO_DISPATCH_PREPARED_REQUEST_SHA_INVALID")
    if actual != expected or request.get("request_id") != audit.get("request_id"):
        _fail("AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION")
    expected_mode = audit.get("request_mode")
    if not isinstance(expected_mode, str) or expected_mode not in request_contract.FORMAL_PREDICTION_MODES:
        _fail("AUTO_DISPATCH_PREPARED_REQUEST_MODE_INVALID")
    if trusted_request_mode(request) != expected_mode:
        _fail("AUTO_DISPATCH_REQUEST_MODE_CHANGED_AFTER_RESERVATION")
    return actual


def assert_canonical_unchanged(audit: dict[str, Any], live_sha: str) -> None:
    if live_sha != audit.get("canonical_execution_sha"):
        _fail("AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION")


def build_dispatch_payload(request_sha: str) -> dict[str, Any]:
    if not isinstance(request_sha, str) or not SHA256_RE.fullmatch(request_sha):
        _fail("AUTO_DISPATCH_REQUEST_SHA_INVALID")
    return {"ref": CANONICAL_REF, "inputs": {"request_pr_number": "341", "expected_request_sha256": request_sha}}


def select_new_formal_run(before_ids: set[int], runs: list[dict[str, Any]], request_sha: str, canonical_sha: str) -> dict[str, Any] | None:
    expected_title = f"{FORMAL_RUN_PREFIX} {request_sha}"
    matches = []
    for run in runs:
        if type(run) is not dict:
            continue
        try:
            run_id = int(run.get("id"))
        except (TypeError, ValueError):
            continue
        if run_id in before_ids:
            continue
        if run.get("event") != "workflow_dispatch" or run.get("head_branch") != CANONICAL_REF or run.get("head_sha") != canonical_sha or run.get("display_title") != expected_title:
            continue
        matches.append(run)
    if len(matches) > 1:
        _fail("AUTO_DISPATCH_FORMAL_RUN_AMBIGUOUS")
    return matches[0] if matches else None


class GitHubAPI:
    def __init__(self, repo: str, token: str):
        if not repo or not token:
            _fail("AUTO_DISPATCH_GITHUB_CREDENTIALS_MISSING")
        self.repo = repo
        self.token = token

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            data=request_contract.canonical_bytes(payload) if payload is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "football3-gpt-auto-dispatch-bridge-v3"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_ERROR:{method}:{path}:{exc.code}:{body[:240]}") from exc
        except Exception as exc:
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_UNAVAILABLE:{method}:{path}") from exc

    def json_value(self, path: str) -> Any:
        status, raw = self.request("GET", path)
        if status != 200:
            _fail(f"AUTO_DISPATCH_GITHUB_API_STATUS:{status}:{path}")
        try:
            return json.loads(raw)
        except Exception as exc:
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_JSON_INVALID:{path}") from exc

    def live_pr(self) -> dict[str, Any]:
        value = self.json_value(f"/repos/{self.repo}/pulls/{CARRIER_PR_NUMBER}")
        if type(value) is not dict:
            _fail("AUTO_DISPATCH_LIVE_CARRIER_INVALID")
        return value

    def carrier_files(self) -> list[str]:
        names: list[str] = []
        for page in range(1, 11):
            value = self.json_value(f"/repos/{self.repo}/pulls/{CARRIER_PR_NUMBER}/files?per_page=100&page={page}")
            if not isinstance(value, list):
                _fail("AUTO_DISPATCH_CARRIER_CHANGED_FILES_INVALID")
            for item in value:
                if type(item) is not dict or not isinstance(item.get("filename"), str):
                    _fail("AUTO_DISPATCH_CARRIER_CHANGED_FILES_INVALID")
                names.append(item["filename"])
            if len(value) < 100:
                return names
        _fail("AUTO_DISPATCH_CARRIER_CHANGED_FILES_PAGINATION_LIMIT")

    def actor_permission(self, actor: str) -> str:
        value = self.json_value(f"/repos/{self.repo}/collaborators/{urllib.parse.quote(actor, safe='')}/permission")
        if type(value) is not dict:
            _fail("AUTO_DISPATCH_ACTOR_PERMISSION_RESPONSE_INVALID")
        return validate_permission(value.get("permission"))

    def live_canonical_sha(self) -> str:
        value = self.json_value(f"/repos/{self.repo}/git/ref/heads/{urllib.parse.quote(CANONICAL_REF, safe='')}")
        obj = value.get("object") if type(value) is dict else None
        sha = obj.get("sha") if type(obj) is dict else None
        if value.get("ref") != f"refs/heads/{CANONICAL_REF}" or obj.get("type") != "commit" or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            _fail("AUTO_DISPATCH_CANONICAL_SHA_INVALID")
        return sha

    def default_branch(self) -> str:
        value = self.json_value(f"/repos/{self.repo}")
        branch = value.get("default_branch") if type(value) is dict else None
        if not isinstance(branch, str) or not branch:
            _fail("AUTO_DISPATCH_DEFAULT_BRANCH_INVALID")
        return branch

    def all_artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for page in range(1, 101):
            value = self.json_value(f"/repos/{self.repo}/actions/artifacts?per_page=100&page={page}")
            batch = value.get("artifacts") if type(value) is dict else None
            if not isinstance(batch, list):
                _fail("AUTO_DISPATCH_ARTIFACT_LIST_INVALID")
            artifacts.extend(a for a in batch if type(a) is dict)
            total = value.get("total_count")
            if isinstance(total, int) and len(artifacts) >= total:
                return artifacts
            if len(batch) < 100:
                return artifacts
        _fail("AUTO_DISPATCH_ARTIFACT_LIST_PAGINATION_LIMIT")

    def validate_ledger_artifact_sources(self, artifacts: list[dict[str, Any]], request_id: str) -> None:
        token = request_id_hash(request_id)
        prefixes = (f"football3-auto-dispatch-reservation-{token}-", f"football3-auto-dispatch-completed-{token}-")
        default_branch = self.default_branch()
        checked: dict[int, dict[str, Any]] = {}
        for artifact in artifacts:
            name = artifact.get("name") if type(artifact) is dict else None
            if artifact.get("expired") is True or not isinstance(name, str) or not name.startswith(prefixes):
                continue
            run_meta = artifact.get("workflow_run") or {}
            try:
                run_id = int(run_meta.get("id"))
            except (TypeError, ValueError):
                _fail("AUTO_DISPATCH_LEDGER_AUTHORITY_INVALID")
            if run_id not in checked:
                value = self.json_value(f"/repos/{self.repo}/actions/runs/{run_id}")
                if type(value) is not dict:
                    _fail("AUTO_DISPATCH_LEDGER_AUTHORITY_INVALID")
                checked[run_id] = value
            run = checked[run_id]
            if run.get("event") != "workflow_run" or run.get("name") != TRUSTED_DISPATCHER_WORKFLOW_NAME or run.get("head_branch") != default_branch:
                _fail("AUTO_DISPATCH_LEDGER_AUTHORITY_INVALID")

    def formal_runs(self) -> list[dict[str, Any]]:
        value = self.json_value(f"/repos/{self.repo}/actions/workflows/{urllib.parse.quote(FORMAL_WORKFLOW, safe='')}/runs?event=workflow_dispatch&branch={urllib.parse.quote(CANONICAL_REF, safe='')}&per_page=100")
        runs = value.get("workflow_runs") if type(value) is dict else None
        if not isinstance(runs, list):
            _fail("AUTO_DISPATCH_FORMAL_RUN_LIST_INVALID")
        return [r for r in runs if type(r) is dict]

    def matching_formal_run_ids(self, request_sha: str, canonical_sha: str) -> set[int]:
        title = f"{FORMAL_RUN_PREFIX} {request_sha}"
        out: set[int] = set()
        for run in self.formal_runs():
            if run.get("event") == "workflow_dispatch" and run.get("head_branch") == CANONICAL_REF and run.get("head_sha") == canonical_sha and run.get("display_title") == title:
                out.add(int(run["id"]))
        return out

    def dispatch_formal(self, request_sha: str) -> None:
        status, _ = self.request("POST", f"/repos/{self.repo}/actions/workflows/{urllib.parse.quote(FORMAL_WORKFLOW, safe='')}/dispatches", build_dispatch_payload(request_sha))
        if status != 204:
            _fail(f"AUTO_DISPATCH_WORKFLOW_DISPATCH_STATUS:{status}")

    def locate_new_formal_run(self, before_ids: set[int], request_sha: str, canonical_sha: str, attempts: int = 30) -> dict[str, Any]:
        for _ in range(attempts):
            found = select_new_formal_run(before_ids, self.formal_runs(), request_sha, canonical_sha)
            if found is not None:
                return found
            time.sleep(1)
        _fail("AUTO_DISPATCH_FORMAL_RUN_NOT_FOUND")

    def wait_terminal(self, run_id: int, attempts: int = 180) -> dict[str, Any]:
        for _ in range(attempts):
            value = self.json_value(f"/repos/{self.repo}/actions/runs/{run_id}")
            if value.get("status") == "completed":
                return value
            time.sleep(5)
        _fail("AUTO_DISPATCH_FORMAL_RUN_TIMEOUT")

    def run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        value = self.json_value(f"/repos/{self.repo}/actions/runs/{run_id}/artifacts?per_page=100")
        artifacts = value.get("artifacts") if type(value) is dict else None
        if not isinstance(artifacts, list):
            _fail("AUTO_DISPATCH_FORMAL_ARTIFACT_LIST_INVALID")
        return [a for a in artifacts if type(a) is dict]

    def download_artifact_zip(self, artifact_id: int) -> bytes:
        status, raw = self.request("GET", f"/repos/{self.repo}/actions/artifacts/{artifact_id}/zip")
        if status != 200:
            _fail("AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED")
        return raw


def _write_json(path: str, value: dict[str, Any]) -> None:
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(request_contract.canonical_bytes(value) + b"\n")


def _append_output(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _audit_live(api: GitHubAPI, actor: str | None) -> tuple[dict[str, Any], dict[str, Any], str, str | None]:
    pr = api.live_pr()
    validate_live_pr(pr, api.repo)
    validate_carrier_files(api.carrier_files())
    permission = api.actor_permission(actor) if actor else None
    request = parse_request_body(pr.get("body") or "")
    return request, pr, api.live_canonical_sha(), permission


def audit_live(args: argparse.Namespace) -> int:
    api = GitHubAPI(args.repo, args.token)
    request, pr, canonical_sha, permission = _audit_live(api, args.actor)
    audit = {
        "schema_version": SCHEMA,
        "phase": "AUDIT_ONLY",
        "status": "PASS",
        "request_id": request["request_id"],
        "request_sha256": request_contract.request_sha256(request),
        "request_mode": trusted_request_mode(request),
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_head_sha": (pr.get("head") or {}).get("sha"),
        "canonical_execution_sha": canonical_sha,
        "actor_permission": permission,
        "dispatch_performed": False,
        "candidate_validation_only": True,
    }
    _write_json(args.audit_out, audit)
    return 0


def prepare(args: argparse.Namespace) -> int:
    for value, code in ((args.trusted_checkout_sha, "AUTO_DISPATCH_TRUSTED_CHECKOUT_SHA_INVALID"), (args.trusted_dispatcher_sha, "AUTO_DISPATCH_TRUSTED_DISPATCHER_SHA_INVALID")):
        if not SHA_RE.fullmatch(value or ""):
            _fail(code)
    api = GitHubAPI(args.repo, args.token)
    request, pr, canonical_sha, permission = _audit_live(api, args.actor)
    if canonical_sha != args.trusted_checkout_sha:
        _fail("AUTO_DISPATCH_TRUSTED_SOURCE_MOVED")
    request_sha = request_contract.request_sha256(request)
    artifacts = api.all_artifacts()
    api.validate_ledger_artifact_sources(artifacts, str(request["request_id"]))
    ledger_status, ledger_ids = classify_ledgers(str(request["request_id"]), request_sha, artifacts)
    reservation_name, completed_name = ledger_names(str(request["request_id"]), request_sha)
    audit = {
        "schema_version": SCHEMA,
        "phase": "PREPARED",
        "status": "READY",
        "request_id": request["request_id"],
        "request_sha256": request_sha,
        "request_mode": trusted_request_mode(request),
        "actor": args.actor,
        "actor_permission": permission,
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_head_sha": (pr.get("head") or {}).get("sha"),
        "carrier_base_sha_audit_only": (pr.get("base") or {}).get("sha"),
        "canonical_execution_sha": canonical_sha,
        "trusted_dispatcher_sha": args.trusted_dispatcher_sha,
        "trusted_dispatcher_run_id": int(args.trusted_dispatcher_run_id),
        "receiver_run_id": int(args.receiver_run_id),
        "dispatch_payload": build_dispatch_payload(request_sha),
        "request_carrier_code_executed": False,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    if ledger_status == "DUPLICATE_COMPLETED":
        audit.update({"phase": "DUPLICATE_SUPPRESSED", "status": "PASS", "dedup_artifact_ids": ledger_ids})
        _write_json(args.audit_out, audit)
        _append_output({"dispatch_required": "false", "result": "DUPLICATE_REQUEST_ID_SUPPRESSED", "request_sha256": request_sha, "reservation_name": reservation_name, "completed_name": completed_name})
        return 0
    if ledger_status == "RESERVATION_CONFLICT":
        by_id = {int(a["id"]): a for a in artifacts if type(a) is dict and a.get("id") is not None}
        superseded: list[dict[str, Any]] = []
        retry_allowed = True
        for artifact_id in ledger_ids:
            artifact = by_id.get(artifact_id)
            if type(artifact) is not dict:
                _fail("AUTO_DISPATCH_RESERVATION_LEDGER_ARTIFACT_MISSING")
            try:
                zf = zipfile.ZipFile(io.BytesIO(api.download_artifact_zip(artifact_id)))
            except Exception as exc:
                raise BridgeError("AUTO_DISPATCH_RESERVATION_LEDGER_ZIP_INVALID") from exc
            ledger, _ = _zip_json(zf, "ledger.json")
            prior_head = ledger.get("canonical_execution_sha")
            try:
                prior_dispatcher_run_id = int(ledger.get("trusted_dispatcher_run_id") or 0)
                artifact_dispatcher_run_id = int(((artifact.get("workflow_run") or {}).get("id")) or 0)
            except (TypeError, ValueError) as exc:
                raise BridgeError("AUTO_DISPATCH_RESERVATION_LEDGER_INVALID") from exc
            if (
                ledger.get("schema_version") != SCHEMA
                or ledger.get("phase") != "PREPARED"
                or ledger.get("status") != "READY"
                or ledger.get("request_id") != request["request_id"]
                or ledger.get("request_sha256") != request_sha
                or not isinstance(prior_head, str)
                or not SHA_RE.fullmatch(prior_head)
                or prior_dispatcher_run_id <= 0
                or prior_dispatcher_run_id != artifact_dispatcher_run_id
            ):
                _fail("AUTO_DISPATCH_RESERVATION_LEDGER_INVALID")
            prior_run = api.json_value(f"/repos/{api.repo}/actions/runs/{prior_dispatcher_run_id}")
            if (
                type(prior_run) is not dict
                or prior_run.get("event") != "workflow_run"
                or prior_run.get("name") != TRUSTED_DISPATCHER_WORKFLOW_NAME
                or prior_run.get("status") != "completed"
            ):
                _fail("AUTO_DISPATCH_RESERVATION_LEDGER_AUTHORITY_INVALID")
            prior_conclusion = prior_run.get("conclusion")
            same_canonical = prior_head == canonical_sha
            if same_canonical or prior_conclusion != "failure":
                retry_allowed = False
            superseded.append({
                "reservation_artifact_id": artifact_id,
                "trusted_dispatcher_run_id": prior_dispatcher_run_id,
                "canonical_execution_sha": prior_head,
                "trusted_dispatcher_conclusion": prior_conclusion,
                "same_canonical_execution_sha": same_canonical,
            })
        if not retry_allowed:
            audit.update({"phase": "RESERVATION_CONFLICT_FAIL_CLOSED", "status": "FAIL_CLOSED", "reservation_artifact_ids": ledger_ids, "reservation_evidence": superseded})
            _write_json(args.audit_out, audit)
            _fail("AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT")
        audit.update({
            "reservation_retry_policy": "FAILED_PRIOR_DISPATCHER_DIFFERENT_CANONICAL_ONLY",
            "superseded_failed_reservations": superseded,
        })
    _write_json(args.audit_out, audit)
    _append_output({"dispatch_required": "true", "result": "PREPARED", "request_sha256": request_sha, "reservation_name": reservation_name, "completed_name": completed_name})
    return 0


def dispatch(args: argparse.Namespace) -> int:
    audit_path = pathlib.Path(args.audit)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BridgeError("AUTO_DISPATCH_AUDIT_INVALID") from exc
    if type(audit) is not dict or audit.get("schema_version") != SCHEMA or audit.get("phase") != "PREPARED":
        _fail("AUTO_DISPATCH_AUDIT_NOT_PREPARED")
    api = GitHubAPI(args.repo, args.token)
    request, pr, live_sha, _ = _audit_live(api, str(audit.get("actor") or ""))
    assert_request_unchanged(audit, request)
    if (pr.get("head") or {}).get("sha") != audit.get("carrier_head_sha"):
        _fail("AUTO_DISPATCH_CARRIER_HEAD_MOVED_AFTER_RESERVATION")
    assert_canonical_unchanged(audit, live_sha)
    request_sha = str(audit["request_sha256"])
    before_ids = api.matching_formal_run_ids(request_sha, live_sha)
    api.dispatch_formal(request_sha)
    run = api.locate_new_formal_run(before_ids, request_sha, live_sha)
    run_id = int(run["id"])
    audit.update({"phase": "DISPATCHED", "status": "IN_PROGRESS", "dispatch_performed": True, "formal_run_id": run_id, "formal_run_url": run.get("html_url"), "formal_run_head_sha": run.get("head_sha"), "dispatched_at": datetime.now(timezone.utc).isoformat()})
    _write_json(str(audit_path), audit)
    _append_output({"result": "DISPATCHED", "formal_run_id": str(run_id)})
    return 0


def _zip_json(zf: zipfile.ZipFile, basename: str) -> tuple[dict[str, Any], bytes]:
    matches = [name for name in zf.namelist() if pathlib.PurePosixPath(name).name == basename]
    if len(matches) != 1:
        _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_FILE_MISSING:{basename}")
    raw = zf.read(matches[0])
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise BridgeError(f"AUTO_DISPATCH_FORMAL_RECEIPT_JSON_INVALID:{basename}") from exc
    if type(value) is not dict:
        _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_JSON_INVALID:{basename}")
    return value, raw


def finalize(args: argparse.Namespace) -> int:
    audit_path = pathlib.Path(args.audit)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise BridgeError("AUTO_DISPATCH_AUDIT_INVALID") from exc
    if type(audit) is not dict or audit.get("phase") != "DISPATCHED":
        _fail("AUTO_DISPATCH_AUDIT_NOT_DISPATCHED")
    api = GitHubAPI(args.repo, args.token)
    run_id = int(audit.get("formal_run_id") or 0)
    run = api.wait_terminal(run_id)
    if run.get("conclusion") != "success":
        _fail(f"AUTO_DISPATCH_FORMAL_RUN_FAILED:{run.get('conclusion')}")
    if run.get("event") != "workflow_dispatch" or run.get("head_branch") != CANONICAL_REF or run.get("head_sha") != audit.get("canonical_execution_sha"):
        _fail("AUTO_DISPATCH_FORMAL_RUN_HEAD_MISMATCH")
    artifacts = [a for a in api.run_artifacts(run_id) if a.get("name") == f"formal-gpt-runner-receipt-{run_id}" and a.get("expired") is not True]
    if len(artifacts) != 1:
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_MISSING")
    artifact = artifacts[0]
    artifact_id = int(artifact["id"])
    digest = artifact.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_DIGEST_MISSING")
    try:
        zf = zipfile.ZipFile(io.BytesIO(api.download_artifact_zip(artifact_id)))
    except Exception as exc:
        raise BridgeError("AUTO_DISPATCH_FORMAL_RECEIPT_ZIP_INVALID") from exc
    binding, binding_raw = _zip_json(zf, "request_sha_binding_receipt.json")
    summary, _ = _zip_json(zf, "summary.json")
    prediction, prediction_raw = _zip_json(zf, "prediction_receipt.json")
    if binding.get("status") != "PASS" or binding.get("request_id") != audit.get("request_id") or binding.get("request_sha256") != audit.get("request_sha256"):
        _fail("AUTO_DISPATCH_FORMAL_REQUEST_BINDING_MISMATCH")
    if binding.get("carrier_head_sha") != audit.get("carrier_head_sha") or binding.get("canonical_execution_sha") != audit.get("canonical_execution_sha"):
        _fail("AUTO_DISPATCH_FORMAL_AUTHORITY_BINDING_MISMATCH")
    if summary.get("status") != "PASS":
        _fail("AUTO_DISPATCH_FORMAL_SUMMARY_NOT_PASS")
    prediction_sha = summary.get("prediction_sha")
    if not isinstance(prediction_sha, str) or not prediction_sha or prediction.get("prediction_sha") != prediction_sha:
        _fail("AUTO_DISPATCH_FORMAL_PREDICTION_SHA_MISMATCH")
    final_receipt = {
        "schema_version": "football3-auto-dispatch-final-binding-receipt-v1",
        "status": "PASS",
        "request_id": audit.get("request_id"),
        "request_sha256": audit.get("request_sha256"),
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_head_sha": audit.get("carrier_head_sha"),
        "trusted_dispatcher_sha": audit.get("trusted_dispatcher_sha"),
        "trusted_dispatcher_run_id": audit.get("trusted_dispatcher_run_id"),
        "receiver_run_id": audit.get("receiver_run_id"),
        "canonical_integration_execution_sha": audit.get("canonical_execution_sha"),
        "resulting_production_run_id": run_id,
        "formal_receipt_artifact_id": artifact_id,
        "formal_receipt_artifact_digest": digest,
        "formal_request_binding_receipt_sha256": hashlib.sha256(binding_raw).hexdigest(),
        "formal_prediction_receipt_sha256": hashlib.sha256(prediction_raw).hexdigest(),
        "prediction_sha": prediction_sha,
        "dispatch_performed": True,
        "request_carrier_code_executed": False,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    audit.update({"phase": "COMPLETED", "status": "PASS", "prediction_sha": prediction_sha, "final_binding_receipt": final_receipt})
    _write_json(str(audit_path), audit)
    _write_json(args.final_receipt_out, final_receipt)
    _append_output({"result": "COMPLETED", "formal_run_id": str(run_id), "formal_receipt_artifact_id": str(artifact_id), "prediction_sha": prediction_sha})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("audit-live"); p.add_argument("--repo", required=True); p.add_argument("--token", required=True); p.add_argument("--actor"); p.add_argument("--audit-out", required=True); p.set_defaults(func=audit_live)
    p = sub.add_parser("prepare"); p.add_argument("--repo", required=True); p.add_argument("--token", required=True); p.add_argument("--actor", required=True); p.add_argument("--trusted-checkout-sha", required=True); p.add_argument("--trusted-dispatcher-sha", required=True); p.add_argument("--trusted-dispatcher-run-id", required=True); p.add_argument("--receiver-run-id", required=True); p.add_argument("--audit-out", required=True); p.set_defaults(func=prepare)
    p = sub.add_parser("dispatch"); p.add_argument("--repo", required=True); p.add_argument("--token", required=True); p.add_argument("--audit", required=True); p.set_defaults(func=dispatch)
    p = sub.add_parser("finalize"); p.add_argument("--repo", required=True); p.add_argument("--token", required=True); p.add_argument("--audit", required=True); p.add_argument("--final-receipt-out", required=True); p.set_defaults(func=finalize)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
