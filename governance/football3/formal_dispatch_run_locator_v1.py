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
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

FORMAL_WORKFLOW_FILE = "football3-formal-gpt-runner-integration-v1.yml"
FORMAL_WORKFLOW_PATH = f".github/workflows/{FORMAL_WORKFLOW_FILE}"
FORMAL_RUN_PREFIX = "Football3 Formal GPT Runner Integration V1"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
LOCATOR_SCHEMA = "football3-formal-dispatch-freshness-locator-v3"
RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})
DEFAULT_POLL_DELAYS = (1.0, 1.0, 2.0, 2.0, 3.0, 5.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0)
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_HTTP_REQUESTS = 160
REPOSITORY_CREATED_LOOKBACK = timedelta(minutes=10)
RATE_HEADER_KEYS = (
    "date",
    "x-github-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-ratelimit-resource",
    "retry-after",
    "content-length",
)
GET_FRESHNESS_HEADERS = {
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}


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


def _created_query_floor(anchor: datetime) -> str:
    value = anchor.astimezone(timezone.utc) - REPOSITORY_CREATED_LOOKBACK
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def expected_display_title(request_sha: str) -> str:
    return f"{FORMAL_RUN_PREFIX} {request_sha}"


def evaluate_formal_run(
    run: dict[str, Any],
    *,
    workflow_id: int,
    before_ids: set[int],
    request_sha: str,
    canonical_sha: str,
    dispatch_started_at: datetime,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    try:
        run_id = int(run.get("id"))
    except (TypeError, ValueError):
        run_id = -1
        reasons.append("RUN_ID_INVALID")
    try:
        actual_workflow_id = int(run.get("workflow_id"))
    except (TypeError, ValueError):
        actual_workflow_id = -1
        reasons.append("WORKFLOW_ID_INVALID")
    if run_id in before_ids:
        reasons.append("PRE_DISPATCH_INVENTORY_MEMBER")
    if actual_workflow_id != workflow_id:
        reasons.append("WORKFLOW_ID_MISMATCH")
    if run.get("event") != "workflow_dispatch":
        reasons.append("EVENT_MISMATCH")
    if run.get("head_branch") != CANONICAL_REF:
        reasons.append("HEAD_BRANCH_MISMATCH")
    if run.get("head_sha") != canonical_sha:
        reasons.append("HEAD_SHA_MISMATCH")
    if run.get("display_title") != expected_display_title(request_sha):
        reasons.append("DISPLAY_TITLE_REQUEST_SHA_MISMATCH")
    created_at = _parse_time(run.get("created_at"))
    if created_at is None:
        reasons.append("CREATED_AT_INVALID")
    elif created_at < dispatch_started_at.replace(microsecond=0):
        reasons.append("CREATED_BEFORE_DISPATCH_BASELINE")
    return not reasons, reasons


def validate_formal_run(
    run: dict[str, Any],
    *,
    workflow_id: int,
    before_ids: set[int],
    request_sha: str,
    canonical_sha: str,
    dispatch_started_at: datetime,
) -> bool:
    accepted, _ = evaluate_formal_run(
        run,
        workflow_id=workflow_id,
        before_ids=before_ids,
        request_sha=request_sha,
        canonical_sha=canonical_sha,
        dispatch_started_at=dispatch_started_at,
    )
    return accepted


def _run_id(run: dict[str, Any]) -> int | None:
    try:
        return int(run.get("id"))
    except (TypeError, ValueError):
        return None


def _near_candidate(run: dict[str, Any], *, workflow_id: int, request_sha: str, canonical_sha: str) -> bool:
    try:
        workflow_match = int(run.get("workflow_id")) == workflow_id
    except (TypeError, ValueError):
        workflow_match = False
    return bool(
        workflow_match
        or run.get("head_sha") == canonical_sha
        or run.get("head_branch") == CANONICAL_REF
        or request_sha in str(run.get("display_title") or "")
    )


def _run_diagnostic(
    run: dict[str, Any],
    *,
    workflow_id: int,
    before_ids: set[int],
    request_sha: str,
    canonical_sha: str,
    dispatch_started_at: datetime,
) -> dict[str, Any]:
    accepted, reasons = evaluate_formal_run(
        run,
        workflow_id=workflow_id,
        before_ids=before_ids,
        request_sha=request_sha,
        canonical_sha=canonical_sha,
        dispatch_started_at=dispatch_started_at,
    )
    return {
        "run_id": _run_id(run),
        "workflow_id": run.get("workflow_id"),
        "event": run.get("event"),
        "head_branch": run.get("head_branch"),
        "head_sha": run.get("head_sha"),
        "display_title": run.get("display_title"),
        "created_at": run.get("created_at"),
        "accepted": accepted,
        "rejection_reasons": reasons,
    }


class GitHubTransport:
    def __init__(
        self,
        repo: str,
        token: str,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_get_attempts: int = 4,
        max_http_requests: int = DEFAULT_MAX_HTTP_REQUESTS,
    ) -> None:
        if not repo or not token:
            _fail("FORMAL_RUN_GITHUB_CREDENTIALS_MISSING")
        self.repo = repo
        self.token = token
        self.sleep_fn = sleep_fn
        self.max_get_attempts = max(1, int(max_get_attempts))
        self.max_http_requests = max(1, int(max_http_requests))
        self.http_request_count = 0

    def _build_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> urllib.request.Request:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "football3-formal-run-locator-v3",
        }
        if method == "GET":
            headers.update(GET_FRESHNESS_HEADERS)
        return urllib.request.Request(
            f"https://api.github.com{path}",
            data=_canonical_json_bytes(payload) if payload is not None else None,
            method=method,
            headers=headers,
        )

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        self.http_request_count += 1
        if self.http_request_count > self.max_http_requests:
            _fail("FORMAL_RUN_GITHUB_REQUEST_BUDGET_EXCEEDED")
        req = self._build_request(method, path, payload)
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return int(response.status), {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, exc.read()
        except Exception as exc:
            raise LocatorError(f"FORMAL_RUN_GITHUB_API_UNAVAILABLE:{method}:{path}") from exc

    @staticmethod
    def _selected_headers(headers: dict[str, str]) -> dict[str, str]:
        return {key: headers[key] for key in RATE_HEADER_KEYS if key in headers}

    @staticmethod
    def _is_explicit_rate_limit(status: int, headers: dict[str, str]) -> bool:
        if status == 429:
            return True
        if status != 403:
            return False
        return headers.get("x-ratelimit-remaining") == "0" or bool(headers.get("retry-after"))

    def _rate_limit_delay(self, headers: dict[str, str], attempt: int) -> float:
        retry_after = headers.get("retry-after")
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        reset = headers.get("x-ratelimit-reset")
        if reset:
            try:
                return min(max(float(reset) - time.time(), 0.0), 30.0)
            except ValueError:
                pass
        return (0.5, 1.0, 2.0, 4.0)[min(attempt, 3)]

    def get_json_with_evidence(self, path: str) -> tuple[Any, dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        last_status = 0
        last_body = b""
        for attempt in range(self.max_get_attempts):
            observed_at = _utc_now()
            status, headers, body = self._request_once("GET", path)
            last_status, last_body = status, body
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "observed_at": _iso(observed_at),
                    "status": status,
                    "headers": self._selected_headers(headers),
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                }
            )
            if status == 200:
                try:
                    return json.loads(body), {"endpoint": path, "attempts": attempts}
                except Exception as exc:
                    raise LocatorError(f"FORMAL_RUN_GITHUB_API_JSON_INVALID:{path}") from exc
            explicit_rate_limit = self._is_explicit_rate_limit(status, headers)
            retryable = explicit_rate_limit or status in RETRYABLE_SERVER_STATUSES
            if status == 403 and not explicit_rate_limit:
                snippet = body.decode("utf-8", "replace")[:240]
                _fail(f"FORMAL_RUN_GITHUB_PERMISSION_DENIED:GET:{path}:403:{snippet}")
            if not retryable:
                snippet = body.decode("utf-8", "replace")[:240]
                _fail(f"FORMAL_RUN_GITHUB_API_ERROR:GET:{path}:{status}:{snippet}")
            if attempt + 1 >= self.max_get_attempts:
                break
            self.sleep_fn(self._rate_limit_delay(headers, attempt) if explicit_rate_limit else (0.5, 1.0, 2.0, 4.0)[min(attempt, 3)])
        snippet = last_body.decode("utf-8", "replace")[:240]
        if last_status in (403, 429):
            _fail(f"FORMAL_RUN_GITHUB_RATE_LIMITED:GET:{path}:{last_status}:{snippet}")
        _fail(f"FORMAL_RUN_GITHUB_API_ERROR:GET:{path}:{last_status}:{snippet}")

    def get_json(self, path: str) -> Any:
        value, _ = self.get_json_with_evidence(path)
        return value

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
            "response_headers": self._selected_headers(headers),
        }


class FormalRunLocator:
    def __init__(
        self,
        transport: Any,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        poll_delays: tuple[float, ...] = DEFAULT_POLL_DELAYS,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.poll_delays = tuple(poll_delays)
        self.max_pages = max(1, int(max_pages))
        self._cache_counter = 0
        self._last_response_signature: dict[tuple[str, int], tuple[str, str, str]] = {}

    def _cache_buster(self, poll_number: int, channel: str, page: int) -> str:
        self._cache_counter += 1
        return f"f3-{poll_number}-{channel}-{page}-{self._cache_counter}"

    def resolve_workflow_id(self) -> tuple[int, dict[str, Any]]:
        path = f"/repos/{self.transport.repo}/actions/workflows/{urllib.parse.quote(FORMAL_WORKFLOW_FILE, safe='')}"
        value, api_evidence = self.transport.get_json_with_evidence(path)
        if not isinstance(value, dict):
            _fail("FORMAL_WORKFLOW_METADATA_INVALID")
        try:
            workflow_id = int(value.get("id"))
        except (TypeError, ValueError):
            _fail("FORMAL_WORKFLOW_ID_INVALID")
        if value.get("path") != FORMAL_WORKFLOW_PATH:
            _fail("FORMAL_WORKFLOW_PATH_MISMATCH")
        return workflow_id, {"workflow_id": workflow_id, "path": value.get("path"), "api": api_evidence}

    def _endpoint(
        self,
        channel: str,
        workflow_id: int,
        page: int,
        *,
        poll_number: int,
        created_anchor: datetime,
    ) -> tuple[str, str]:
        cache_buster = self._cache_buster(poll_number, channel, page)
        if channel == "workflow":
            query = urllib.parse.urlencode({"event": "workflow_dispatch", "per_page": "100", "page": str(page), "f3_cache_buster": cache_buster})
            return f"/repos/{self.transport.repo}/actions/workflows/{workflow_id}/runs?{query}", cache_buster
        if channel == "repository":
            query = urllib.parse.urlencode({"event": "workflow_dispatch", "created": f">={_created_query_floor(created_anchor)}", "per_page": "100", "page": str(page), "f3_cache_buster": cache_buster})
            return f"/repos/{self.transport.repo}/actions/runs?{query}", cache_buster
        raise AssertionError(channel)

    @staticmethod
    def _response_signature(api_evidence: dict[str, Any]) -> tuple[str, str, str] | None:
        attempts = api_evidence.get("attempts") if isinstance(api_evidence, dict) else None
        if not isinstance(attempts, list) or not attempts:
            return None
        last = attempts[-1] if isinstance(attempts[-1], dict) else {}
        headers = last.get("headers") if isinstance(last.get("headers"), dict) else {}
        request_id = str(headers.get("x-github-request-id") or "")
        date = str(headers.get("date") or "")
        body_sha = str(last.get("body_sha256") or "")
        if not request_id or not date or not body_sha:
            return None
        return request_id, date, body_sha

    def list_channel_runs(
        self,
        channel: str,
        *,
        workflow_id: int,
        canonical_sha: str,
        poll_number: int = 0,
        created_anchor: datetime | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        del canonical_sha
        anchor = created_anchor or _utc_now()
        runs: list[dict[str, Any]] = []
        pages_evidence: list[dict[str, Any]] = []
        for page in range(1, self.max_pages + 1):
            endpoint, cache_buster = self._endpoint(channel, workflow_id, page, poll_number=poll_number, created_anchor=anchor)
            value, api_evidence = self.transport.get_json_with_evidence(endpoint)
            batch = value.get("workflow_runs") if isinstance(value, dict) else None
            total = value.get("total_count") if isinstance(value, dict) else None
            if not isinstance(batch, list):
                _fail(f"FORMAL_RUN_LIST_INVALID:{channel}")
            clean_batch = [run for run in batch if isinstance(run, dict)]
            runs.extend(clean_batch)
            signature = self._response_signature(api_evidence)
            stale = False
            if signature is not None:
                key = (channel, page)
                stale = self._last_response_signature.get(key) == signature
                self._last_response_signature[key] = signature
            pages_evidence.append({
                "page": page,
                "endpoint": endpoint,
                "cache_buster": cache_buster,
                "total_count": total,
                "returned_run_ids": [rid for run in clean_batch if (rid := _run_id(run)) is not None],
                "api": api_evidence,
                "freshness_status": "STALE_RESPONSE_SUSPECTED" if stale else "OBSERVED",
                "stale_response_suspected": stale,
            })
            if isinstance(total, int) and len(runs) >= total:
                break
            if len(batch) < 100:
                break
        else:
            _fail(f"FORMAL_RUN_LIST_PAGINATION_LIMIT:{channel}")
        return runs, {"channel": channel, "pages": pages_evidence, "run_count": len(runs)}

    def inventory_before_dispatch(self, workflow_id: int, canonical_sha: str) -> tuple[set[int], dict[str, Any]]:
        observed_at = _utc_now()
        union: set[int] = set()
        channels: dict[str, Any] = {}
        for channel in ("workflow", "repository"):
            runs, evidence = self.list_channel_runs(channel, workflow_id=workflow_id, canonical_sha=canonical_sha, poll_number=0, created_anchor=observed_at)
            ids = sorted({rid for run in runs if (rid := _run_id(run)) is not None})
            union.update(ids)
            evidence["run_ids"] = ids
            channels[channel] = evidence
        return union, {"observed_at": _iso(observed_at), "channels": channels, "union_run_ids": sorted(union), "run_count": len(union)}

    def _reread_and_verify(
        self,
        candidate: dict[str, Any],
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        dispatch_started_at: datetime,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        run_id = _run_id(candidate)
        if run_id is None:
            _fail("FORMAL_RUN_ID_INVALID")
        cache_buster = self._cache_buster(999999, "reread", run_id)
        path = f"/repos/{self.transport.repo}/actions/runs/{run_id}?{urllib.parse.urlencode({'f3_cache_buster': cache_buster})}"
        reread, api_evidence = self.transport.get_json_with_evidence(path)
        if not isinstance(reread, dict):
            _fail("FORMAL_RUN_REREAD_INVALID")
        accepted, reasons = evaluate_formal_run(reread, workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at)
        if int(reread.get("id") or 0) != run_id or not accepted:
            _fail(f"FORMAL_RUN_REVALIDATION_FAILED:{','.join(reasons) or 'RUN_ID_MISMATCH'}")
        return reread, {"endpoint": path, "cache_buster": cache_buster, "api": api_evidence, "revalidated": True}

    def _poll_channel(
        self,
        channel: str,
        *,
        poll_number: int,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        dispatch_started_at: datetime,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        runs, list_evidence = self.list_channel_runs(channel, workflow_id=workflow_id, canonical_sha=canonical_sha, poll_number=poll_number, created_anchor=dispatch_started_at)
        matches: list[dict[str, Any]] = []
        near: list[dict[str, Any]] = []
        for run in runs:
            accepted, _ = evaluate_formal_run(run, workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at)
            if accepted:
                matches.append(run)
            if accepted or _near_candidate(run, workflow_id=workflow_id, request_sha=request_sha, canonical_sha=canonical_sha):
                near.append(_run_diagnostic(run, workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at))
        list_evidence["near_candidates"] = near
        list_evidence["exact_match_run_ids"] = [rid for run in matches if (rid := _run_id(run)) is not None]
        return matches, list_evidence

    def locate(
        self,
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        dispatch_started_at: datetime,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        polls: list[dict[str, Any]] = []
        for poll_number, delay in enumerate((0.0,) + self.poll_delays, start=1):
            if delay:
                self.sleep_fn(delay)
            poll_started = _utc_now()
            channel_matches: dict[str, list[dict[str, Any]]] = {}
            channel_evidence: dict[str, Any] = {}
            for channel in ("workflow", "repository"):
                matches, evidence = self._poll_channel(channel, poll_number=poll_number, workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at)
                channel_matches[channel] = matches
                channel_evidence[channel] = evidence
            poll_record = {"poll_number": poll_number, "poll_started_at": _iso(poll_started), "poll_finished_at": _iso(_utc_now()), "channels": channel_evidence}
            polls.append(poll_record)
            per_channel_ids: dict[str, list[int]] = {}
            for channel, matches in channel_matches.items():
                ids = [rid for run in matches if (rid := _run_id(run)) is not None]
                per_channel_ids[channel] = ids
                if len(set(ids)) > 1:
                    poll_record["decision"] = f"AMBIGUOUS_{channel.upper()}"
                    if audit_sink:
                        audit_sink(poll_record)
                    _fail(f"FORMAL_RUN_AMBIGUOUS:{channel}")
            workflow_ids = set(per_channel_ids["workflow"])
            repository_ids = set(per_channel_ids["repository"])
            union_ids = workflow_ids | repository_ids
            if workflow_ids and repository_ids and workflow_ids != repository_ids:
                poll_record["decision"] = "CHANNEL_CONFLICT"
                if audit_sink:
                    audit_sink(poll_record)
                _fail("FORMAL_RUN_CHANNEL_CONFLICT")
            if len(union_ids) > 1:
                poll_record["decision"] = "AMBIGUOUS_UNION"
                if audit_sink:
                    audit_sink(poll_record)
                _fail("FORMAL_RUN_AMBIGUOUS:union")
            if len(union_ids) == 1:
                selected_id = next(iter(union_ids))
                poll_record["decision"] = "SELECTED_FOR_REREAD"
                poll_record["selected_run_id"] = selected_id
            else:
                poll_record["decision"] = "NO_MATCH"
            if audit_sink:
                audit_sink(poll_record)
            if len(union_ids) == 1:
                selected_id = next(iter(union_ids))
                source_run = None
                for matches in channel_matches.values():
                    for run in matches:
                        if _run_id(run) == selected_id:
                            source_run = run
                            break
                    if source_run is not None:
                        break
                assert source_run is not None
                reread, reread_evidence = self._reread_and_verify(source_run, workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at)
                return reread, {
                    "schema_version": LOCATOR_SCHEMA,
                    "poll_count": poll_number,
                    "polls": polls,
                    "selected_run_id": selected_id,
                    "selected_channels": [channel for channel, ids in per_channel_ids.items() if selected_id in ids],
                    "reread": reread_evidence,
                }
        _fail("FORMAL_RUN_NOT_FOUND")

    def dispatch_once_and_locate(
        self,
        *,
        workflow_id: int,
        before_ids: set[int],
        request_sha: str,
        canonical_sha: str,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        payload = {"ref": CANONICAL_REF, "inputs": {"request_pr_number": "341", "expected_request_sha256": request_sha}}
        dispatch_started_at = _utc_now()
        http_evidence = self.transport.post_dispatch_once(workflow_id, payload)
        if int(http_evidence.get("http_status") or 0) != 204:
            _fail(f"FORMAL_DISPATCH_HTTP_STATUS:{http_evidence.get('http_status')}")
        run, locator_evidence = self.locate(workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=canonical_sha, dispatch_started_at=dispatch_started_at, audit_sink=audit_sink)
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
    workflow_id, workflow_metadata = locator.resolve_workflow_id()
    before_ids, inventory = locator.inventory_before_dispatch(workflow_id, live_sha)
    diagnostics: dict[str, Any] = {"schema_version": LOCATOR_SCHEMA, "workflow_metadata": workflow_metadata, "inventory": inventory, "polls": []}
    audit.update({"formal_workflow_id": workflow_id, "formal_run_inventory_before_dispatch": inventory, "locator_helper": LOCATOR_SCHEMA, "formal_run_locator_diagnostics": diagnostics})
    _write_audit(audit_path, audit)
    payload = {"ref": CANONICAL_REF, "inputs": {"request_pr_number": "341", "expected_request_sha256": request_sha}}
    dispatch_started_at = _utc_now()
    try:
        http_evidence = transport.post_dispatch_once(workflow_id, payload)
    except LocatorError as exc:
        audit.update({"phase": "FORMAL_DISPATCH_RESULT_UNKNOWN", "status": "FAIL_CLOSED", "dispatch_performed": "UNKNOWN", "locator_error": str(exc)})
        _write_audit(audit_path, audit)
        raise
    if int(http_evidence.get("http_status") or 0) != 204:
        status = int(http_evidence.get("http_status") or 0)
        dispatch_state: bool | str = False if 400 <= status < 500 and status not in (408, 429) else "UNKNOWN"
        audit.update({"phase": "FORMAL_DISPATCH_FAILED", "status": "FAIL_CLOSED", "dispatch_performed": dispatch_state, "formal_dispatch_http": http_evidence, "locator_error": f"FORMAL_DISPATCH_HTTP_STATUS:{status}"})
        _write_audit(audit_path, audit)
        _fail(f"FORMAL_DISPATCH_HTTP_STATUS:{status}")
    audit.update({"phase": "DISPATCH_ACCEPTED_LOCATING", "status": "IN_PROGRESS", "dispatch_performed": True, "formal_dispatch_http": http_evidence, "formal_dispatch_started_at": _iso(dispatch_started_at)})
    diagnostics["dispatch_started_at"] = _iso(dispatch_started_at)
    _write_audit(audit_path, audit)
    def persist_poll(poll_record: dict[str, Any]) -> None:
        diagnostics.setdefault("polls", []).append(poll_record)
        audit["formal_run_locator_diagnostics"] = diagnostics
        _write_audit(audit_path, audit)
    try:
        run, locator_evidence = locator.locate(workflow_id=workflow_id, before_ids=before_ids, request_sha=request_sha, canonical_sha=live_sha, dispatch_started_at=dispatch_started_at, audit_sink=persist_poll)
    except LocatorError as exc:
        audit.update({"phase": "DISPATCH_ACCEPTED_LOCATOR_FAILED", "status": "FAIL_CLOSED", "dispatch_performed": True, "locator_error": str(exc), "formal_run_locator_diagnostics": diagnostics})
        _write_audit(audit_path, audit)
        raise
    locator_evidence["dispatch_started_at"] = _iso(dispatch_started_at)
    run_id = int(run["id"])
    diagnostics["selected_run_id"] = run_id
    diagnostics["selected_channels"] = locator_evidence.get("selected_channels")
    diagnostics["reread"] = locator_evidence.get("reread")
    audit.update({"phase": "DISPATCHED", "status": "IN_PROGRESS", "dispatch_performed": True, "formal_dispatch_http": http_evidence, "formal_run_locator": locator_evidence, "formal_run_locator_diagnostics": diagnostics, "formal_run_id": run_id, "formal_run_url": run.get("html_url"), "formal_run_head_sha": run.get("head_sha"), "dispatched_at": _iso(_utc_now())})
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
