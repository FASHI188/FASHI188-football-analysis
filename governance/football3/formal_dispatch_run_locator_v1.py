#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

FORMAL_WORKFLOW_FILE = "football3-formal-gpt-runner-integration-v1.yml"
FORMAL_WORKFLOW_PATH = f".github/workflows/{FORMAL_WORKFLOW_FILE}"
FORMAL_RUN_PREFIX = "Football3 Formal GPT Runner Integration V1"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
RETRYABLE_GET_STATUSES = frozenset({403, 429, 500, 502, 503, 504})
DEFAULT_POLL_DELAYS = (1.0, 1.0, 2.0, 2.0, 3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0)


class LocatorError(RuntimeError):
    pass


def _fail(code: str) -> None:
    raise LocatorError(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def expected_display_title(request_sha: str) -> str:
    return f"{FORMAL_RUN_PREFIX} {request_sha}"


def validate_formal_run(
    run: dict[str, Any],
    *,
    workflow_id: int,
    before_ids: set[int],
    request_sha: str,
    canonical_sha: str,
    dispatch_started_at: datetime,
) -> bool:
    try:
        run_id = int(run.get("id"))
        actual_workflow_id = int(run.get("workflow_id"))
    except (TypeError, ValueError):
        return False
    created_at = _parse_time(run.get("created_at"))
    return bool(
        run_id not in before_ids
        and actual_workflow_id == workflow_id
        and run.get("event") == "workflow_dispatch"
        and run.get("head_branch") == CANONICAL_REF
        and run.get("head_sha") == canonical_sha
        and run.get("display_title") == expected_display_title(request_sha)
        and created_at is not None
        and created_at >= dispatch_started_at.replace(microsecond=0)
    )


def select_unique_formal_run(
    runs: Iterable[dict[str, Any]],
    *,
    workflow_id: int,
    before_ids: set[int],
    request_sha: str,
    canonical_sha: str,
    dispatch_started_at: datetime,
) -> dict[str, Any] | None:
    matches = [
        run
        for run in runs
        if isinstance(run, dict)
        and validate_formal_run(
            run,
            workflow_id=workflow_id,
            before_ids=before_ids,
            request_sha=request_sha,
            canonical_sha=canonical_sha,
            dispatch_started_at=dispatch_started_at,
        )
    ]
    if len(matches) > 1:
        _fail("FORMAL_RUN_AMBIGUOUS")
    return matches[0] if matches else None


class GitHubTransport:
    def __init__(
        self,
        repo: str,
        token: str,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_get_attempts: int = 4,
    ) -> None:
        if not repo or not token:
            _fail("FORMAL_RUN_GITHUB_CREDENTIALS_MISSING")
        self.repo = repo
        self.token = token
        self.sleep_fn = sleep_fn
        self.max_get_attempts = max(1, int(max_get_attempts))

    def _request_once(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, dict[str, str], bytes]:
        req = urllib.request.Request(
            f"https://api.github.com{path}",
            data=_canonical_json_bytes(payload) if payload is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "football3-formal-run-locator-v1",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return int(response.status), {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, exc.read()
        except Exception as exc:
            raise LocatorError(f"FORMAL_RUN_GITHUB_API_UNAVAILABLE:{method}:{path}") from exc

    def get_json(self, path: str) -> Any:
        delays = (0.5, 1.0, 2.0, 4.0)
        last_status = 0
        last_body = b""
        for attempt in range(self.max_get_attempts):
            status, _headers, body = self._request_once("GET", path)
            last_status, last_body = status, body
            if status == 200:
                try:
                    return json.loads(body)
                except Exception as exc:
                    raise LocatorError(f"FORMAL_RUN_GITHUB_API_JSON_INVALID:{path}") from exc
            if status not in RETRYABLE_GET_STATUSES or attempt + 1 >= self.max_get_attempts:
                break
            self.sleep_fn(delays[min(attempt, len(delays) - 1)])
        snippet = last_body.decode("utf-8", "replace")[:240]
        _fail(f"FORMAL_RUN_GITHUB_API_ERROR:GET:{path}:{last_status}:{snippet}")

    def post_dispatch_once(self, workflow_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        path = f"/repos/{self.repo}/actions/workflows/{workflow_id}/dispatches"
        called_at = _utc_now()
        status, headers, body = self._request_once("POST", path, payload)
        return {
            "endpoint": path,
            "workflow_id": workflow_id,
            "called_at": _iso(called_at),
            "payload": payload,
            "http_status": status,
            "response_body_utf8": body.decode("utf-8", "replace")[:1000],
            "response_body_sha256": hashlib.sha256(body).hexdigest(),
            "response_headers": {
                key: headers[key]
                for key in (
                    "date",
                    "x-github-request-id",
                    "x-ratelimit-limit",
                    "x-ratelimit-remaining",
                    "x-ratelimit-reset",
                    "retry-after",
                    "content-length",
                )
                if key in headers
            },
        }


class FormalRunLocator:
    def __init__(
        self,
        transport: Any,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        poll_delays: tuple[float, ...] = DEFAULT_POLL_DELAYS,
        max_pages: int = 100,
    ) -> None:
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.poll_delays = tuple(poll_delays)
        self.max_pages = max(1, int(max_pages))

    def resolve_workflow_id(self) -> int:
        value = self.transport.get_json(
            f"/repos/{self.transport.repo}/actions/workflows/{urllib.parse.quote(FORMAL_WORKFLOW_FILE, safe='')}"
        )
        if not isinstance(value, dict):
            _fail("FORMAL_WORKFLOW_METADATA_INVALID")
        try:
            workflow_id = int(value.get("id"))
        except (TypeError, ValueError):
            _fail("FORMAL_WORKFLOW_ID_INVALID")
        if value.get("path") != FORMAL_WORKFLOW_PATH:
            _fail("FORMAL_WORKFLOW_PATH_MISMATCH")
        return workflow_id

    def list_all_runs(self, workflow_id: int) -> tuple[list[dict[str, Any]], int]:
        runs: list[dict[str, Any]] = []
        pages = 0
        for page in range(1, self.max_pages + 1):
            pages = page
            value = self.transport.get_json(
                f"/repos/{self.transport.repo}/actions/workflows/{workflow_id}/runs?event=workflow_dispatch&per_page=100&page={page}"
            )
            batch = value.get("workflow_runs") if isinstance(value, dict) else None
            total = value.get("total_count") if isinstance(value, dict) else None
            if not isinstance(batch, list):
                _fail("FORMAL_RUN_LIST_INVALID")
            runs.extend(run for run in batch if isinstance(run, dict))
            if isinstance(total, int) and len(runs) >= total:
                return runs, pages
            if len(batch) < 100:
                return runs, pages
        _fail("FORMAL_RUN_LIST_PAGINATION_LIMIT")

    def inventory_before_dispatch(self, workflow_id: int) -> tuple[set[int], dict[str, Any]]:
        runs, pages = self.list_all_runs(workflow_id)
        ids: set[int] = set()
        for run in runs:
            try:
                ids.add(int(run.get("id")))
            except (TypeError, ValueError):
                continue
        return ids, {"run_count": len(runs), "pages": pages, "run_ids": sorted(ids)}

    def _reread_and_verify(
        self,
        candidate: dict[str, Any],
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        dispatch_started_at: datetime,
    ) -> dict[str, Any]:
        try:
            run_id = int(candidate["id"])
        except (KeyError, TypeError, ValueError):
            _fail("FORMAL_RUN_ID_INVALID")
        reread = self.transport.get_json(f"/repos/{self.transport.repo}/actions/runs/{run_id}")
        if not isinstance(reread, dict):
            _fail("FORMAL_RUN_REREAD_INVALID")
        if int(reread.get("id") or 0) != run_id or not validate_formal_run(
            reread,
            workflow_id=workflow_id,
            before_ids=before_ids,
            request_sha=request_sha,
            canonical_sha=canonical_sha,
            dispatch_started_at=dispatch_started_at,
        ):
            _fail("FORMAL_RUN_REVALIDATION_FAILED")
        return reread

    def locate(
        self,
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        dispatch_started_at: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        poll_count = 0
        page_counts: list[int] = []
        for delay in (0.0,) + self.poll_delays:
            if delay:
                self.sleep_fn(delay)
            poll_count += 1
            runs, pages = self.list_all_runs(workflow_id)
            page_counts.append(pages)
            found = select_unique_formal_run(
                runs,
                workflow_id=workflow_id,
                before_ids=before_ids,
                request_sha=request_sha,
                canonical_sha=canonical_sha,
                dispatch_started_at=dispatch_started_at,
            )
            if found is not None:
                reread = self._reread_and_verify(
                    found,
                    workflow_id=workflow_id,
                    before_ids=before_ids,
                    request_sha=request_sha,
                    canonical_sha=canonical_sha,
                    dispatch_started_at=dispatch_started_at,
                )
                return reread, {"poll_count": poll_count, "pages_per_poll": page_counts}
        _fail("FORMAL_RUN_NOT_FOUND")

    def dispatch_once_and_locate(
        self,
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = {
            "ref": CANONICAL_REF,
            "inputs": {"request_pr_number": "341", "expected_request_sha256": request_sha},
        }
        dispatch_started_at = _utc_now()
        http_evidence = self.transport.post_dispatch_once(workflow_id, payload)
        if int(http_evidence.get("http_status") or 0) != 204:
            _fail(f"FORMAL_DISPATCH_HTTP_STATUS:{http_evidence.get('http_status')}")
        run, locator_evidence = self.locate(
            workflow_id=workflow_id,
            before_ids=before_ids,
            request_sha=request_sha,
            canonical_sha=canonical_sha,
            dispatch_started_at=dispatch_started_at,
        )
        locator_evidence["dispatch_started_at"] = _iso(dispatch_started_at)
        return run, http_evidence, locator_evidence


def _write_audit(path: pathlib.Path, audit: dict[str, Any]) -> None:
    path.write_bytes(_canonical_json_bytes(audit) + b"\n")


def _append_output(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")


def dispatch_command(args: argparse.Namespace) -> int:
    import auto_dispatch_bridge_v1 as bridge

    audit_path = pathlib.Path(args.audit)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LocatorError("AUTO_DISPATCH_AUDIT_INVALID") from exc
    if not isinstance(audit, dict) or audit.get("schema_version") != bridge.SCHEMA or audit.get("phase") != "PREPARED":
        _fail("AUTO_DISPATCH_AUDIT_NOT_PREPARED")

    bridge_api = bridge.GitHubAPI(args.repo, args.token)
    request, pr, live_sha, _ = bridge._audit_live(bridge_api, str(audit.get("actor") or ""))
    bridge.assert_request_unchanged(audit, request)
    if (pr.get("head") or {}).get("sha") != audit.get("carrier_head_sha"):
        _fail("AUTO_DISPATCH_CARRIER_HEAD_MOVED_AFTER_RESERVATION")
    bridge.assert_canonical_unchanged(audit, live_sha)
    request_sha = str(audit.get("request_sha256") or "")
    if not bridge.SHA256_RE.fullmatch(request_sha):
        _fail("AUTO_DISPATCH_REQUEST_SHA_INVALID")

    transport = GitHubTransport(args.repo, args.token)
    locator = FormalRunLocator(transport)
    workflow_id = locator.resolve_workflow_id()
    before_ids, inventory = locator.inventory_before_dispatch(workflow_id)
    audit.update(
        {
            "formal_workflow_id": workflow_id,
            "formal_run_inventory_before_dispatch": inventory,
            "locator_helper": "formal_dispatch_run_locator_v1",
        }
    )
    _write_audit(audit_path, audit)

    payload = {
        "ref": CANONICAL_REF,
        "inputs": {"request_pr_number": "341", "expected_request_sha256": request_sha},
    }
    dispatch_started_at = _utc_now()
    try:
        http_evidence = transport.post_dispatch_once(workflow_id, payload)
    except LocatorError as exc:
        audit.update(
            {
                "phase": "FORMAL_DISPATCH_FAILED",
                "status": "FAIL_CLOSED",
                "dispatch_performed": False,
                "locator_error": str(exc),
            }
        )
        _write_audit(audit_path, audit)
        raise
    if int(http_evidence.get("http_status") or 0) != 204:
        audit.update(
            {
                "phase": "FORMAL_DISPATCH_FAILED",
                "status": "FAIL_CLOSED",
                "dispatch_performed": False,
                "formal_dispatch_http": http_evidence,
                "locator_error": f"FORMAL_DISPATCH_HTTP_STATUS:{http_evidence.get('http_status')}",
            }
        )
        _write_audit(audit_path, audit)
        _fail(f"FORMAL_DISPATCH_HTTP_STATUS:{http_evidence.get('http_status')}")

    audit.update(
        {
            "phase": "DISPATCH_ACCEPTED_LOCATING",
            "status": "IN_PROGRESS",
            "dispatch_performed": True,
            "formal_dispatch_http": http_evidence,
            "formal_dispatch_started_at": _iso(dispatch_started_at),
        }
    )
    _write_audit(audit_path, audit)

    try:
        run, locator_evidence = locator.locate(
            workflow_id=workflow_id,
            before_ids=before_ids,
            request_sha=request_sha,
            canonical_sha=live_sha,
            dispatch_started_at=dispatch_started_at,
        )
    except LocatorError as exc:
        audit.update(
            {
                "phase": "DISPATCH_ACCEPTED_LOCATOR_FAILED",
                "status": "FAIL_CLOSED",
                "dispatch_performed": True,
                "locator_error": str(exc),
            }
        )
        _write_audit(audit_path, audit)
        raise

    locator_evidence["dispatch_started_at"] = _iso(dispatch_started_at)
    run_id = int(run["id"])
    audit.update(
        {
            "phase": "DISPATCHED",
            "status": "IN_PROGRESS",
            "dispatch_performed": True,
            "formal_dispatch_http": http_evidence,
            "formal_run_locator": locator_evidence,
            "formal_run_id": run_id,
            "formal_run_url": run.get("html_url"),
            "formal_run_head_sha": run.get("head_sha"),
            "dispatched_at": _iso(_utc_now()),
        }
    )
    _write_audit(audit_path, audit)
    _append_output({"result": "DISPATCHED", "formal_run_id": str(run_id)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dispatch = sub.add_parser("dispatch")
    dispatch.add_argument("--repo", required=True)
    dispatch.add_argument("--token", required=True)
    dispatch.add_argument("--audit", required=True)
    dispatch.set_defaults(func=dispatch_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (LocatorError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
