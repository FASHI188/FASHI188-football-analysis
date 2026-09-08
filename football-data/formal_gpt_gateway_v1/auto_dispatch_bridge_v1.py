#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

SCHEMA = "football3-gpt-auto-dispatch-bridge-v1"
REQUEST_SCHEMA = "football3-formal-gpt-gateway-v1"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
CARRIER_REF = "football3/formal-gpt-runner-request-carrier-v1"
CARRIER_PR_NUMBER = 341
FORMAL_WORKFLOW = "football3-formal-gpt-runner-integration-v1.yml"
BEGIN_MARKER = "<!-- football3-request-json -->"
END_MARKER = "<!-- /football3-request-json -->"
ALLOWED_PERMISSIONS = frozenset({"admin", "maintain", "write"})
SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class BridgeError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(request)).hexdigest()


def request_id_hash(request_id: str) -> str:
    return hashlib.sha256(request_id.encode("utf-8")).hexdigest()


def artifact_names(request_id: str) -> tuple[str, str]:
    digest = request_id_hash(request_id)
    return (
        f"football3-auto-dispatch-reservation-{digest}",
        f"football3-auto-dispatch-completed-{digest}",
    )


def parse_request_body(body: str) -> dict[str, Any]:
    if not isinstance(body, str):
        raise BridgeError("AUTO_DISPATCH_REQUEST_BODY_INVALID")
    if body.count(BEGIN_MARKER) != 1 or body.count(END_MARKER) != 1:
        raise BridgeError("AUTO_DISPATCH_REQUEST_MARKERS_INVALID")
    before, rest = body.split(BEGIN_MARKER, 1)
    raw, after = rest.split(END_MARKER, 1)
    del before, after
    try:
        request = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise BridgeError("AUTO_DISPATCH_REQUEST_JSON_INVALID") from exc
    if not isinstance(request, dict) or request.get("schema_version") != REQUEST_SCHEMA:
        raise BridgeError("AUTO_DISPATCH_REQUEST_SCHEMA_INVALID")
    request_id = request.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        raise BridgeError("AUTO_DISPATCH_REQUEST_ID_MISSING")
    if len(request_id) > 256 or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in request_id):
        raise BridgeError("AUTO_DISPATCH_REQUEST_ID_INVALID")
    request["request_id"] = request_id.strip()
    return request


def validate_event(event: dict[str, Any], repo: str) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise BridgeError("AUTO_DISPATCH_EVENT_INVALID")
    if event.get("action") != "edited":
        raise BridgeError("AUTO_DISPATCH_EVENT_ACTION_UNAUTHORIZED")
    repository = event.get("repository") or {}
    if repository.get("full_name") != repo:
        raise BridgeError("AUTO_DISPATCH_REPOSITORY_IDENTITY_MISMATCH")
    pr = event.get("pull_request") or {}
    if pr.get("number") != CARRIER_PR_NUMBER:
        raise BridgeError("AUTO_DISPATCH_CARRIER_PR_UNAUTHORIZED")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    if base.get("ref") != CANONICAL_REF:
        raise BridgeError("AUTO_DISPATCH_CARRIER_BASE_REF_UNAUTHORIZED")
    if head.get("ref") != CARRIER_REF or head_repo.get("full_name") != repo:
        raise BridgeError("AUTO_DISPATCH_CARRIER_HEAD_UNAUTHORIZED")
    sender = event.get("sender") or {}
    actor = sender.get("login")
    if not isinstance(actor, str) or not actor:
        raise BridgeError("AUTO_DISPATCH_ACTOR_MISSING")
    return {
        "actor": actor,
        "event_base_sha": base.get("sha"),
        "event_carrier_head_sha": head.get("sha"),
        "event_updated_at": pr.get("updated_at"),
    }


def validate_live_pr(pr: dict[str, Any], repo: str) -> None:
    if not isinstance(pr, dict) or pr.get("number") != CARRIER_PR_NUMBER:
        raise BridgeError("AUTO_DISPATCH_LIVE_CARRIER_INVALID")
    if pr.get("state") != "open" or pr.get("draft") is not True or pr.get("merged_at") is not None:
        raise BridgeError("AUTO_DISPATCH_LIVE_CARRIER_NOT_OPEN_DRAFT")
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    head_repo = head.get("repo") or {}
    if base.get("ref") != CANONICAL_REF:
        raise BridgeError("AUTO_DISPATCH_LIVE_BASE_REF_UNAUTHORIZED")
    if head.get("ref") != CARRIER_REF or head_repo.get("full_name") != repo:
        raise BridgeError("AUTO_DISPATCH_LIVE_HEAD_UNAUTHORIZED")
    for value, code in (
        (base.get("sha"), "AUTO_DISPATCH_LIVE_BASE_SHA_INVALID"),
        (head.get("sha"), "AUTO_DISPATCH_LIVE_HEAD_SHA_INVALID"),
    ):
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise BridgeError(code)


def build_dispatch_payload() -> dict[str, Any]:
    return {"ref": CANONICAL_REF, "inputs": {"request_pr_number": str(CARRIER_PR_NUMBER)}}


class GitHubAPI:
    def __init__(self, repo: str, token: str):
        self.repo = repo
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, bytes]:
        url = f"https://api.github.com{path}"
        data = canonical_bytes(payload) if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "football3-gpt-auto-dispatch-bridge-v1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return int(resp.status), resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            raise BridgeError(
                f"AUTO_DISPATCH_GITHUB_API_ERROR:{method}:{path}:{exc.code}:{body[:300]}"
            ) from exc

    def json(self, path: str) -> dict[str, Any]:
        status, body = self.request("GET", path)
        if status != 200:
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_STATUS:{status}:{path}")
        try:
            value = json.loads(body)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_JSON_INVALID:{path}") from exc
        if not isinstance(value, dict):
            raise BridgeError(f"AUTO_DISPATCH_GITHUB_API_SHAPE_INVALID:{path}")
        return value

    def live_pr(self) -> dict[str, Any]:
        return self.json(f"/repos/{self.repo}/pulls/{CARRIER_PR_NUMBER}")

    def actor_permission(self, actor: str) -> str:
        encoded = urllib.parse.quote(actor, safe="")
        value = self.json(f"/repos/{self.repo}/collaborators/{encoded}/permission")
        permission = value.get("permission")
        if permission not in ALLOWED_PERMISSIONS:
            raise BridgeError(f"AUTO_DISPATCH_ACTOR_PERMISSION_DENIED:{permission}")
        return str(permission)

    def live_canonical_sha(self) -> str:
        value = self.json(f"/repos/{self.repo}/git/ref/heads/{CANONICAL_REF}")
        expected = f"refs/heads/{CANONICAL_REF}"
        obj = value.get("object") or {}
        sha = obj.get("sha")
        if value.get("ref") != expected or obj.get("type") != "commit":
            raise BridgeError("AUTO_DISPATCH_CANONICAL_REF_INVALID")
        if not isinstance(sha, str) or not SHA_RE.fullmatch(sha):
            raise BridgeError("AUTO_DISPATCH_CANONICAL_SHA_INVALID")
        return sha

    def artifacts_named(self, name: str) -> list[dict[str, Any]]:
        encoded = urllib.parse.quote(name, safe="")
        value = self.json(
            f"/repos/{self.repo}/actions/artifacts?per_page=100&name={encoded}"
        )
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list):
            raise BridgeError("AUTO_DISPATCH_ARTIFACT_LIST_INVALID")
        return [item for item in artifacts if isinstance(item, dict)]

    def dispatch_formal(self) -> None:
        workflow = urllib.parse.quote(FORMAL_WORKFLOW, safe="")
        status, _ = self.request(
            "POST",
            f"/repos/{self.repo}/actions/workflows/{workflow}/dispatches",
            build_dispatch_payload(),
        )
        if status != 204:
            raise BridgeError(f"AUTO_DISPATCH_WORKFLOW_DISPATCH_STATUS:{status}")


def _nonexpired(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in artifacts if a.get("expired") is not True]


def _write_json(path: str, value: dict[str, Any]) -> None:
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _append_output(values: dict[str, str]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if not output:
        return
    with open(output, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def prepare(args: argparse.Namespace) -> int:
    if os.environ.get("GITHUB_EVENT_NAME") != "pull_request_target":
        raise BridgeError("AUTO_DISPATCH_TRUSTED_EVENT_REQUIRED")
    event = json.loads(pathlib.Path(args.event).read_text(encoding="utf-8"))
    event_audit = validate_event(event, args.repo)
    api = GitHubAPI(args.repo, args.token)
    permission = api.actor_permission(event_audit["actor"])
    pr = api.live_pr()
    validate_live_pr(pr, args.repo)
    request = parse_request_body(pr.get("body") or "")
    live_sha = api.live_canonical_sha()
    if live_sha != args.trusted_checkout_sha:
        raise BridgeError(
            f"AUTO_DISPATCH_TRUSTED_SOURCE_MOVED:checkout={args.trusted_checkout_sha}:live={live_sha}"
        )

    request_id = str(request["request_id"])
    reservation_name, completed_name = artifact_names(request_id)
    completed = _nonexpired(api.artifacts_named(completed_name))
    reservation = _nonexpired(api.artifacts_named(reservation_name))

    audit = {
        "schema_version": SCHEMA,
        "phase": "PREPARED",
        "request_id": request_id,
        "request_id_sha256": request_id_hash(request_id),
        "request_sha256": request_sha256(request),
        "actor": event_audit["actor"],
        "actor_permission": permission,
        "carrier_pr_number": CARRIER_PR_NUMBER,
        "carrier_ref": CARRIER_REF,
        "carrier_head_sha": (pr.get("head") or {}).get("sha"),
        "carrier_event_base_sha_audit_only": event_audit.get("event_base_sha"),
        "carrier_current_base_sha_audit_only": (pr.get("base") or {}).get("sha"),
        "canonical_ref": CANONICAL_REF,
        "trusted_execution_sha": live_sha,
        "formal_workflow": FORMAL_WORKFLOW,
        "dispatch_payload": build_dispatch_payload(),
        "request_carrier_code_executed": False,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }

    if completed:
        audit["phase"] = "DUPLICATE_SUPPRESSED"
        audit["dedup_artifact_ids"] = [a.get("id") for a in completed]
        _write_json(args.audit_out, audit)
        _append_output(
            {
                "dispatch_required": "false",
                "result": "DUPLICATE_REQUEST_ID_SUPPRESSED",
                "request_id_hash": audit["request_id_sha256"],
                "reservation_name": reservation_name,
                "completed_name": completed_name,
                "live_canonical_sha": live_sha,
            }
        )
        return 0
    if reservation:
        audit["phase"] = "RESERVATION_CONFLICT_FAIL_CLOSED"
        audit["reservation_artifact_ids"] = [a.get("id") for a in reservation]
        _write_json(args.audit_out, audit)
        raise BridgeError("AUTO_DISPATCH_REQUEST_ID_RESERVATION_PRESENT")

    _write_json(args.audit_out, audit)
    _append_output(
        {
            "dispatch_required": "true",
            "result": "PREPARED",
            "request_id_hash": audit["request_id_sha256"],
            "reservation_name": reservation_name,
            "completed_name": completed_name,
            "live_canonical_sha": live_sha,
        }
    )
    return 0


def dispatch(args: argparse.Namespace) -> int:
    audit_path = pathlib.Path(args.audit)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("schema_version") != SCHEMA or audit.get("phase") != "PREPARED":
        raise BridgeError("AUTO_DISPATCH_AUDIT_NOT_PREPARED")

    api = GitHubAPI(args.repo, args.token)
    pr = api.live_pr()
    validate_live_pr(pr, args.repo)
    request = parse_request_body(pr.get("body") or "")
    if request_id_hash(str(request["request_id"])) != audit.get("request_id_sha256"):
        raise BridgeError("AUTO_DISPATCH_REQUEST_CHANGED_AFTER_RESERVATION")

    live_sha = api.live_canonical_sha()
    if live_sha != audit.get("trusted_execution_sha"):
        raise BridgeError(
            f"AUTO_DISPATCH_CANONICAL_MOVED_AFTER_RESERVATION:prepared={audit.get('trusted_execution_sha')}:live={live_sha}"
        )
    api.dispatch_formal()
    audit["phase"] = "DISPATCHED"
    audit["dispatched_at"] = datetime.now(timezone.utc).isoformat()
    audit["dispatch_ref"] = CANONICAL_REF
    audit["dispatch_live_canonical_sha"] = live_sha
    _write_json(str(audit_path), audit)
    _append_output({"result": "DISPATCHED", "live_canonical_sha": live_sha})
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("prepare")
    q.add_argument("--event", required=True)
    q.add_argument("--repo", required=True)
    q.add_argument("--token", required=True)
    q.add_argument("--trusted-checkout-sha", required=True)
    q.add_argument("--audit-out", required=True)
    q.set_defaults(func=prepare)

    q = sub.add_parser("dispatch")
    q.add_argument("--repo", required=True)
    q.add_argument("--token", required=True)
    q.add_argument("--audit", required=True)
    q.set_defaults(func=dispatch)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        return int(args.func(args))
    except BridgeError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
