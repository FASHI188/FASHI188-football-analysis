#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import request_contract_v1 as request_contract

SCHEMA = "football3-gpt-auto-dispatch-bridge-v2"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
CARRIER_REF = "football3/formal-gpt-runner-request-carrier-v1"
CARRIER_PR_NUMBER = 341
CARRIER_FILE = "FOOTBALL3_FORMAL_GPT_REQUEST_CARRIER.md"
FORMAL_WORKFLOW = "football3-formal-gpt-runner-integration-v1.yml"
FORMAL_RUN_PREFIX = "Football3 Formal GPT Runner Integration V1"
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
    import hashlib
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
    prefix, rest = body.split(BEGIN_MARKER, 1)
    raw, suffix = rest.split(END_MARKER, 1)
    del prefix, suffix
    try:
        return request_contract.parse_json(raw.strip(), carrier_request=True)
    except request_contract.FormalRequestContractError as exc:
        raise BridgeError(f"AUTO_DISPATCH_REQUEST_CONTRACT:{exc.code}") from exc


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
    for value, code in (
        (base.get("sha"), "AUTO_DISPATCH_LIVE_BASE_SHA_INVALID"),
        (head.get("sha"), "AUTO_DISPATCH_LIVE_HEAD_SHA_INVALID"),
    ):
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            _fail(code)


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
    reservation_prefix = f"football3-auto-dispatch-reservation-{token}"
    completed_prefix = f"football3-auto-dispatch-completed-{token}"
    related: list[dict[str, Any]] = []
    for artifact in artifacts:
        if type(artifact) is not dict or artifact.get("expired") is True:
            continue
        name = artifact.get("name")
        if isinstance(name, str) and (name.startswith(reservation_prefix) or name.startswith(completed_prefix)):
            related.append(artifact)
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
    return actual


def assert_canonical_unchanged(audit: dict[str, Any], live_sha: str) -> None:
    if live_sha != audit.get("canonical_execution_sha"):
        _fail("AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION")


def build_dispatch_payload(request_sha: str) -> dict[str, Any]:
    if not isinstance(request_sha, str) or not SHA256_RE.fullmatch(request_sha):
        _fail("AUTO_DISPATCH_REQUEST_SHA_INVALID")
    return {
        "ref": CANONICAL_REF,
        "inputs": {
            "request_pr_number": str(CARRIER_PR_NUMBER),
            "expected_request_sha256": request_sha,
        },
    }


def select_new_formal_run(before_ids: set[int], runs: list[dict[str, Any]], request_sha: str, canonical_sha: str) -> dict[str, Any] | None:
    expected_title = f"{FORMAL_RUN_PREFIX} {request_sha}"
    matches: list[dict[str, Any]] = []
    for run in runs:
        if type(run) is not dict:
            continue
        try:
            run_id = int(run.get("id"))
        except (TypeError, ValueError):
            continue
        if run_id in before_ids:
            continue
        if run.get("event") != "workflow_dispatch":
            continue
        if run.get("head_branch") != CANONICAL_REF or run.get("head_sha") != canonical_sha:
            continue
        if run.get("display_title") != expected_title:
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
        url = f"https://api.github.com{path}"
        data = request_contract.canonical_bytes(payload) if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "football3-gpt-auto-dispatch-bridge-v2",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
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
        actor = urllib.parse.quote(actor, safe="")
        value = self.json_value(f"/repos/{self.repo}/collaborators/{actor}/permission")
        if type(value) is not dict:
            _fail("AUTO_DISPATCH_ACTOR_PERMISSION_RESPONSE_INVALID")
        return validate_permission(value.get("permission"))

    def live_canonical_sha(self) -> str:
        encoded = urllib.parse.quote(CANONICAL_REF, safe="")
        value = self.json_value(f"/repos/{self.repo}/git/ref/heads/{encoded}")
        if type(value) is not dict or value.get("ref") != f"refs/heads/{CANONICAL_REF}":
            _fail("AUTO_DISPATCH_CANONICAL_REF_INVALID")
        obj = value.get("object") or {}
        sha = obj.get("sha")
        if obj.get("type") != "commit" or not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            _fail("AUTO_DISPATCH_CANONICAL_SHA_INVALID")
        return sha

    def all_artifacts(self) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for page in range(1, 101):
            value = self.json_value(f"/repos/{self.repo}/actions/artifacts?per_page=100&page={page}")
            if type(value) is not dict or not isinstance(value.get("artifacts"), list):
                _fail("AUTO_DISPATCH_ARTIFACT_LIST_INVALID")
            batch = [a for a in value["artifacts"] if type(a) is dict]
            artifacts.extend(batch)
            total = value.get("total_count")
            if isinstance(total, int) and len(artifacts) >= total:
                return artifacts
            if len(batch) < 100:
                return artifacts
        _fail("AUTO_DISPATCH_ARTIFACT_LIST_PAGINATION_LIMIT")

    def formal_runs(self) -> list[dict[str, Any]]:
        workflow = urllib.parse.quote(FORMAL_WORKFLOW, safe="")
        branch = urllib.parse.quote(CANONICAL_REF, safe="")
        value = self.json_value(f"/repos/{self.repo}/actions/workflows/{workflow}/runs?event=workflow_dispatch&branch={branch}&per_page=100")
        if type(value) is not dict or not isinstance(value.get("workflow_runs"), list):
            _fail("AUTO_DISPATCH_FORMAL_RUN_LIST_INVALID")
        return [r for r in value["workflow_runs"] if type(r) is dict]

    def matching_formal_run_ids(self, request_sha: str, canonical_sha: str) -> set[int]:
        expected_title = f"{FORMAL_RUN_PREFIX} {request_sha}"
        result: set[int] = set()
        for run in self.formal_runs():
            if run.get("event") == "workflow_dispatch" and run.get("head_branch") == CANONICAL_REF and run.get("head_sha") == canonical_sha and run.get("display_title") == expected_title:
                try:
                    result.add(int(run["id"]))
                except (KeyError, TypeError, ValueError):
                    _fail("AUTO_DISPATCH_FORMAL_RUN_ID_INVALID")
        return result

    def dispatch_formal(self, request_sha: str) -> None:
        workflow = urllib.parse.quote(FORMAL_WORKFLOW, safe="")
        status, _ = self.request("POST", f"/repos/{self.repo}/actions/workflows/{workflow}/dispatches", build_dispatch_payload(request_sha))
        if status != 204:
            _fail(f"AUTO_DISPATCH_WORKFLOW_DISPATCH_STATUS:{status}")

    def locate_new_formal_run(self, before_ids: set[int], request_sha: str, canonical_sha: str, *, attempts: int = 30, sleep_seconds: float = 1.0) -> dict[str, Any]:
        for _ in range(attempts):
            found = select_new_formal_run(before_ids, self.formal_runs(), request_sha, canonical_sha)
            if found is not None:
                return found
            time.sleep(sleep_seconds)
        _fail("AUTO_DISPATCH_FORMAL_RUN_NOT_FOUND")


def _write_json(path: str, value: dict[str, Any]) -> None:
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(request_contract.canonical_bytes(value) + b"\n")


def _append_output(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def _audit_live(api: GitHubAPI, *, actor: str | None) -> tuple[dict[str, Any], dict[str, Any], str, str | None]:
    pr = api.live_pr()
    validate_live_pr(pr, api.repo)
    files = api.carrier_files()
    validate_carrier_files(files)
    permission = api.actor_permission(actor) if actor else None
    request = parse_request_body(pr.get("body") or "")
    canonical_sha = api.live_canonical_sha()
    return request, pr, canonical_sha, permission


def audit_live(args: argparse.Namespace) -> int:
    api = GitHubAPI(args.repo, args.token)
    request, pr, canonical_sha, permission = _audit_live(api, actor=args.actor)
    request_sha = request_contract.request_sha256(request)
    audit = {
        "schema_version": SCHEMA,
        "phase": "AUDIT_ONLY",
        "status": "PASS",
        "request_id": request["request_id"],
        "request_sha256": request_sha,
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_file": CARRIER_FILE,
        "carrier_head_sha": (pr.get("head") or {}).get("sha"),
        "carrier_base_sha_audit_only": (pr.get("base") or {}).get("sha"),
        "canonical_ref": CANONICAL_REF,
        "canonical_execution_sha": canonical_sha,
        "actor": args.actor or None,
        "actor_permission": permission,
        "dispatch_performed": False,
        "trusted_auto_dispatch_trigger": "TRUSTED_AUTO_DISPATCH_TRIGGER_UNAVAILABLE",
    }
    _write_json(args.audit_out, audit)
    _append_output({"request_id": str(request["request_id"]), "request_sha256": request_sha, "canonical_execution_sha": canonical_sha, "carrier_head_sha": str(audit["carrier_head_sha"])})
    return 0


def prepare(args: argparse.Namespace) -> int:
    if not SHA_RE.fullmatch(args.trusted_checkout_sha or ""):
        _fail("AUTO_DISPATCH_TRUSTED_CHECKOUT_SHA_INVALID")
    api = GitHubAPI(args.repo, args.token)
    request, pr, canonical_sha, permission = _audit_live(api, actor=args.actor)
    if canonical_sha != args.trusted_checkout_sha:
        _fail("AUTO_DISPATCH_TRUSTED_SOURCE_MOVED")
    request_sha = request_contract.request_sha256(request)
    ledger_status, ledger_ids = classify_ledgers(str(request["request_id"]), request_sha, api.all_artifacts())
    reservation_name, completed_name = ledger_names(str(request["request_id"]), request_sha)
    audit = {
        "schema_version": SCHEMA,
        "phase": "PREPARED",
        "request_id": request["request_id"],
        "request_sha256": request_sha,
        "actor": args.actor,
        "actor_permission": permission,
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_file": CARRIER_FILE,
        "carrier_ref": CARRIER_REF,
        "carrier_head_sha": (pr.get("head") or {}).get("sha"),
        "carrier_base_sha_audit_only": (pr.get("base") or {}).get("sha"),
        "canonical_ref": CANONICAL_REF,
        "canonical_execution_sha": canonical_sha,
        "formal_workflow": FORMAL_WORKFLOW,
        "dispatch_payload": build_dispatch_payload(request_sha),
        "request_carrier_code_executed": False,
        "trusted_workflow_source_sha": args.trusted_checkout_sha,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    if ledger_status == "DUPLICATE_COMPLETED":
        audit["phase"] = "DUPLICATE_SUPPRESSED"
        audit["dedup_artifact_ids"] = ledger_ids
        _write_json(args.audit_out, audit)
        _append_output({"dispatch_required": "false", "result": "DUPLICATE_REQUEST_ID_SUPPRESSED", "request_id": str(request["request_id"]), "request_sha256": request_sha, "reservation_name": reservation_name, "completed_name": completed_name, "canonical_execution_sha": canonical_sha})
        return 0
    if ledger_status == "RESERVATION_CONFLICT":
        audit["phase"] = "RESERVATION_CONFLICT_FAIL_CLOSED"
        audit["reservation_artifact_ids"] = ledger_ids
        _write_json(args.audit_out, audit)
        _fail("AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT")
    _write_json(args.audit_out, audit)
    _append_output({"dispatch_required": "true", "result": "PREPARED", "request_id": str(request["request_id"]), "request_sha256": request_sha, "reservation_name": reservation_name, "completed_name": completed_name, "canonical_execution_sha": canonical_sha})
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
    request, pr, live_sha, permission = _audit_live(api, actor=str(audit.get("actor") or ""))
    del permission
    assert_request_unchanged(audit, request)
    if (pr.get("head") or {}).get("sha") != audit.get("carrier_head_sha"):
        _fail("AUTO_DISPATCH_CARRIER_HEAD_MOVED_AFTER_RESERVATION")
    assert_canonical_unchanged(audit, live_sha)
    request_sha = str(audit["request_sha256"])
    before_ids = api.matching_formal_run_ids(request_sha, live_sha)
    api.dispatch_formal(request_sha)
    run = api.locate_new_formal_run(before_ids, request_sha, live_sha)
    try:
        run_id = int(run["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BridgeError("AUTO_DISPATCH_FORMAL_RUN_ID_INVALID") from exc
    audit.update({"phase": "DISPATCHED", "dispatched_at": datetime.now(timezone.utc).isoformat(), "dispatch_ref": CANONICAL_REF, "dispatch_request_sha256": request_sha, "dispatch_live_canonical_sha": live_sha, "formal_run_id": run_id, "formal_run_url": run.get("html_url"), "formal_run_head_sha": run.get("head_sha"), "formal_run_event": run.get("event")})
    _write_json(str(audit_path), audit)
    _append_output({"result": "DISPATCHED", "request_sha256": request_sha, "canonical_execution_sha": live_sha, "formal_run_id": str(run_id)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("audit-live")
    command.add_argument("--repo", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--actor")
    command.add_argument("--audit-out", required=True)
    command.set_defaults(func=audit_live)
    command = sub.add_parser("prepare")
    command.add_argument("--repo", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--actor", required=True)
    command.add_argument("--trusted-checkout-sha", required=True)
    command.add_argument("--audit-out", required=True)
    command.set_defaults(func=prepare)
    command = sub.add_parser("dispatch")
    command.add_argument("--repo", required=True)
    command.add_argument("--token", required=True)
    command.add_argument("--audit", required=True)
    command.set_defaults(func=dispatch)
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
