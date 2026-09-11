#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from typing import Any, Callable

FINALIZER_SCHEMA = "football3-formal-terminal-finalizer-freshness-v1"
FINAL_RECEIPT_SCHEMA = "football3-auto-dispatch-final-binding-receipt-v1"
CANONICAL_REF = "football3/formal-gpt-runner-integration-v1"
FORMAL_RUN_PREFIX = "Football3 Formal GPT Runner Integration V1"
RETROSPECTIVE_MODE = "CURRENT_V2_RETROSPECTIVE_REPLAY"
TRUSTED_FORMAL_REQUEST_MODES = frozenset(
    {
        "predict",
        "PROSPECTIVE_FORMAL_PREDICTION",
        "ACTIVE_AT_CUTOFF_REPLAY",
        "CURRENT_MODEL_RETROSPECTIVE_REPLAY",
        RETROSPECTIVE_MODE,
    }
)
RETROSPECTIVE_STATE_AUDIT_SCHEMA = "football3-current-v2-retrospective-state-integrity-audit-v1"
GET_FRESHNESS_HEADERS = {
    "Cache-Control": "no-cache, no-store, max-age=0",
    "Pragma": "no-cache",
}
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
RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})
ARTIFACT_REDIRECT_STATUSES = frozenset({302, 303, 307, 308})
DEFAULT_RUN_ATTEMPTS = 180
DEFAULT_RUN_DELAY_SECONDS = 5.0
DEFAULT_ARTIFACT_ATTEMPTS = 60
DEFAULT_ARTIFACT_DELAY_SECONDS = 2.0
DEFAULT_MAX_PAGES = 10
DEFAULT_MAX_HTTP_REQUESTS = 320
DEFAULT_MAX_ARTIFACT_REDIRECTS = 5


class FinalizerError(RuntimeError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fail(code: str) -> None:
    raise FinalizerError(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_hex(value: Any, n: int) -> bool:
    return isinstance(value, str) and len(value) == n and all(c in "0123456789abcdef" for c in value)


def _trusted_audit_request_mode(audit: dict[str, Any]) -> str:
    if type(audit) is not dict:
        _fail("AUTO_DISPATCH_AUDIT_REQUEST_MODE_INVALID")
    mode = audit.get("request_mode")
    if not isinstance(mode, str) or mode not in TRUSTED_FORMAL_REQUEST_MODES:
        _fail("AUTO_DISPATCH_AUDIT_REQUEST_MODE_INVALID")
    return mode


def _validate_state_integrity_contract(
    audit: dict[str, Any],
    state: dict[str, Any],
    prediction: dict[str, Any],
    prediction_raw: bytes,
    execution: dict[str, Any],
    *,
    request_mode: str,
    run_id: int,
    request_sha: str,
    canonical_sha: str,
    prediction_sha: str,
) -> None:
    if request_mode != RETROSPECTIVE_MODE:
        if state.get("schema_version") == RETROSPECTIVE_STATE_AUDIT_SCHEMA:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if state.get("request_mode") == RETROSPECTIVE_MODE:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if prediction.get("request_mode") == RETROSPECTIVE_MODE or prediction.get("mode") == RETROSPECTIVE_MODE:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if state.get("request_mode") is not None and state.get("request_mode") != request_mode:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if prediction.get("request_mode") is not None and prediction.get("request_mode") != request_mode:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if prediction.get("mode") is not None and prediction.get("mode") not in {request_mode, "predict"}:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
        if state.get("status") != "PASS" or execution.get("state_integrity_guard_status") != "PASS":
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_NOT_PASS")
        return

    if prediction.get("request_mode") != request_mode or prediction.get("mode") != request_mode:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("schema_version") != RETROSPECTIVE_STATE_AUDIT_SCHEMA:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("status") != "PASS":
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_NOT_PASS")
    if state.get("request_mode") != request_mode:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("request_id") != audit.get("request_id") or state.get("request_sha256") != request_sha:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    try:
        if int(state.get("formal_run_id") or 0) != run_id:
            _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    except (TypeError, ValueError):
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("canonical_execution_sha") != canonical_sha or state.get("prediction_sha") != prediction_sha:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("source_prediction_receipt_sha256") != hashlib.sha256(prediction_raw).hexdigest():
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    guard = state.get("state_integrity_guard")
    if type(guard) is not dict or guard.get("status") != "PASS" or state.get("state_integrity_guard_status") != "PASS":
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_NOT_PASS")
    if prediction.get("state_integrity_guard") != guard or prediction.get("state_integrity") != guard:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if execution.get("state_integrity_guard_status") != "PASS":
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_NOT_PASS")
    if state.get("source") != "SAME_FORMAL_RUN_RETROSPECTIVE_PREDICTION_RECEIPT":
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("manual_or_auxiliary_artifact_generation_used") is not False:
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("formal_model_head") != prediction.get("formal_model_head"):
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("current_sha256") != prediction.get("actual_current_sha"):
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")
    if state.get("fusion_weights") != prediction.get("fusion_weights"):
        _fail("AUTO_DISPATCH_FORMAL_STATE_INTEGRITY_BINDING_INVALID")


def _atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def expected_display_title(request_sha: str) -> str:
    return f"{FORMAL_RUN_PREFIX} {request_sha}"


def _selected_headers(headers: dict[str, str]) -> dict[str, str]:
    lower = {str(k).lower(): str(v) for k, v in headers.items()}
    return {k: lower[k] for k in RATE_HEADER_KEYS if k in lower}


def _url_parts(url: str) -> urllib.parse.SplitResult:
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port
    except Exception as exc:
        raise FinalizerError("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOCATION_INVALID") from exc
    if not parts.scheme or not parts.netloc or not parts.hostname:
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOCATION_INVALID")
    if parts.scheme.lower() != "https":
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_HTTPS_REQUIRED")
    if parts.username is not None or parts.password is not None:
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_USERINFO_REJECTED")
    if parts.fragment:
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOCATION_INVALID")
    return parts


def _is_github_api_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except Exception:
        return False
    return (
        parts.scheme.lower() == "https"
        and (parts.hostname or "").lower() == "api.github.com"
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
    )


class GitHubTransport:
    def __init__(
        self,
        repo: str,
        token: str,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_get_attempts: int = 4,
        max_http_requests: int = DEFAULT_MAX_HTTP_REQUESTS,
        max_artifact_redirects: int = DEFAULT_MAX_ARTIFACT_REDIRECTS,
    ) -> None:
        if not repo or not token:
            _fail("AUTO_DISPATCH_GITHUB_CREDENTIALS_MISSING")
        self.repo = repo
        self.token = token
        self.sleep_fn = sleep_fn
        self.max_get_attempts = max(1, int(max_get_attempts))
        self.max_http_requests = max(1, int(max_http_requests))
        self.max_artifact_redirects = max(0, int(max_artifact_redirects))
        self.http_request_count = 0
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def _perform_http(self, req: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
        try:
            with self._opener.open(req, timeout=30) as response:
                return int(response.status), {k.lower(): v for k, v in response.headers.items()}, response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), {k.lower(): v for k, v in exc.headers.items()}, exc.read()
        except Exception as exc:
            raise FinalizerError("AUTO_DISPATCH_GITHUB_API_UNAVAILABLE:GET") from exc

    def _request_url_once(self, url: str) -> tuple[int, dict[str, str], bytes, bool]:
        _url_parts(url)
        self.http_request_count += 1
        if self.http_request_count > self.max_http_requests:
            _fail("AUTO_DISPATCH_FINALIZER_HTTP_BUDGET_EXCEEDED")
        github_auth = _is_github_api_url(url)
        headers = {
            "User-Agent": "football3-formal-terminal-finalizer-v1",
            "Accept": "application/vnd.github+json" if github_auth else "application/octet-stream",
        }
        if github_auth:
            headers.update({
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                **GET_FRESHNESS_HEADERS,
            })
        req = urllib.request.Request(url, method="GET", headers=headers)
        status, response_headers, body = self._perform_http(req)
        return int(status), {str(k).lower(): str(v) for k, v in response_headers.items()}, body, github_auth

    def _request_once(self, path: str) -> tuple[int, dict[str, str], bytes]:
        status, headers, body, _ = self._request_url_once(f"https://api.github.com{path}")
        return status, headers, body

    @staticmethod
    def _explicit_rate_limit(status: int, headers: dict[str, str]) -> bool:
        if status == 429:
            return True
        if status != 403:
            return False
        return headers.get("x-ratelimit-remaining") == "0" or bool(headers.get("retry-after"))

    @staticmethod
    def _retry_delay(headers: dict[str, str], attempt: int) -> float:
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
            observed_at = _now()
            status, headers, body = self._request_once(path)
            body_sha = hashlib.sha256(body).hexdigest()
            last_status, last_body = status, body
            attempts.append({
                "attempt": attempt + 1,
                "observed_at": observed_at,
                "http_status": status,
                "headers": _selected_headers(headers),
                "body_sha256": body_sha,
            })
            if status == 200:
                try:
                    return json.loads(body), {"endpoint": path, "attempts": attempts}
                except Exception as exc:
                    raise FinalizerError(f"AUTO_DISPATCH_GITHUB_API_JSON_INVALID:{path}") from exc
            explicit_rate = self._explicit_rate_limit(status, headers)
            if status == 403 and not explicit_rate:
                snippet = body.decode("utf-8", "replace")[:240]
                _fail(f"AUTO_DISPATCH_GITHUB_PERMISSION_DENIED:GET:{path}:403:{snippet}")
            if not (explicit_rate or status in RETRYABLE_SERVER_STATUSES):
                snippet = body.decode("utf-8", "replace")[:240]
                _fail(f"AUTO_DISPATCH_GITHUB_API_ERROR:GET:{path}:{status}:{snippet}")
            if attempt + 1 < self.max_get_attempts:
                self.sleep_fn(self._retry_delay(headers, attempt))
        snippet = last_body.decode("utf-8", "replace")[:240]
        if last_status in (403, 429):
            _fail(f"AUTO_DISPATCH_GITHUB_RATE_LIMITED:GET:{path}:{last_status}:{snippet}")
        _fail(f"AUTO_DISPATCH_GITHUB_API_ERROR:GET:{path}:{last_status}:{snippet}")

    @staticmethod
    def _download_record(
        *,
        redirect_number: int,
        url: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
        authorization_sent: bool,
    ) -> dict[str, Any]:
        parts = _url_parts(url)
        location = headers.get("location")
        return {
            "redirect_number": redirect_number,
            "scheme": parts.scheme.lower(),
            "host": (parts.hostname or "").lower(),
            "status": int(status),
            "location_sha256": hashlib.sha256(location.encode("utf-8")).hexdigest() if location else None,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "authorization_sent": bool(authorization_sent),
        }

    @staticmethod
    def _validated_location(location: str) -> str:
        if not isinstance(location, str) or not location:
            _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOCATION_INVALID")
        parts = _url_parts(location)
        if not parts.hostname:
            _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOCATION_INVALID")
        return location

    def download_artifact_zip(
        self,
        artifact_id: int,
        diagnostics: dict[str, Any] | None = None,
        persist: Callable[[], None] | None = None,
    ) -> bytes:
        current_url = f"https://api.github.com/repos/{self.repo}/actions/artifacts/{artifact_id}/zip"
        visited = {current_url}
        redirect_number = 0
        records = diagnostics.setdefault("artifact_download_requests", []) if isinstance(diagnostics, dict) else None

        while True:
            last_status = 0
            for attempt in range(self.max_get_attempts):
                status, headers, body, auth_sent = self._request_url_once(current_url)
                last_status = status
                if records is not None:
                    records.append(self._download_record(
                        redirect_number=redirect_number,
                        url=current_url,
                        status=status,
                        headers=headers,
                        body=body,
                        authorization_sent=auth_sent,
                    ))
                    if persist is not None:
                        persist()

                if status == 200:
                    return body

                if status in ARTIFACT_REDIRECT_STATUSES:
                    location = self._validated_location(headers.get("location") or "")
                    if redirect_number >= self.max_artifact_redirects:
                        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LIMIT_EXCEEDED")
                    if location in visited:
                        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_REDIRECT_LOOP")
                    visited.add(location)
                    current_url = location
                    redirect_number += 1
                    break

                is_api = _is_github_api_url(current_url)
                explicit_rate = self._explicit_rate_limit(status, headers)
                if is_api and status == 401:
                    _fail("AUTO_DISPATCH_FORMAL_RECEIPT_API_DOWNLOAD_FAILED:401")
                if is_api and status == 403 and not explicit_rate:
                    _fail("AUTO_DISPATCH_GITHUB_PERMISSION_DENIED:GET:ARTIFACT_ZIP:403")
                if not is_api and status in (401, 403):
                    _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED:{status}")
                retryable = explicit_rate or status in RETRYABLE_SERVER_STATUSES
                if not retryable:
                    _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED:{status}")
                if attempt + 1 < self.max_get_attempts:
                    self.sleep_fn(self._retry_delay(headers, attempt))
                    continue
                if is_api and last_status in (403, 429):
                    _fail(f"AUTO_DISPATCH_GITHUB_RATE_LIMITED:GET:ARTIFACT_ZIP:{last_status}")
                if not is_api and last_status in (403, 429):
                    _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_RATE_LIMITED:{last_status}")
                _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED:{last_status}")
            else:
                _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED:{last_status}")

            if last_status not in ARTIFACT_REDIRECT_STATUSES:
                _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_DOWNLOAD_FAILED:{last_status}")


class FormalTerminalFinalizer:
    def __init__(
        self,
        transport: Any,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        run_attempts: int = DEFAULT_RUN_ATTEMPTS,
        run_delay_seconds: float = DEFAULT_RUN_DELAY_SECONDS,
        artifact_attempts: int = DEFAULT_ARTIFACT_ATTEMPTS,
        artifact_delay_seconds: float = DEFAULT_ARTIFACT_DELAY_SECONDS,
        max_pages: int = DEFAULT_MAX_PAGES,
    ) -> None:
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.run_attempts = max(1, int(run_attempts))
        self.run_delay_seconds = max(0.0, float(run_delay_seconds))
        self.artifact_attempts = max(1, int(artifact_attempts))
        self.artifact_delay_seconds = max(0.0, float(artifact_delay_seconds))
        self.max_pages = max(1, int(max_pages))
        self.counter = 0
        self.last_signature: dict[str, tuple[str, str, str]] = {}

    def _buster(self, poll: int, kind: str, page: int = 0) -> str:
        self.counter += 1
        return f"f3-finalizer-{kind}-{poll}-{page}-{self.counter}"

    def _record(self, kind: str, poll: int, endpoint: str, evidence: dict[str, Any], value: Any, reason: str, page: int | None = None) -> dict[str, Any]:
        attempts = evidence.get("attempts") if isinstance(evidence, dict) else []
        last = attempts[-1] if isinstance(attempts, list) and attempts else {}
        headers = last.get("headers") if isinstance(last, dict) and isinstance(last.get("headers"), dict) else {}
        body_sha = str(last.get("body_sha256") or "")
        signature = (str(headers.get("date") or ""), str(headers.get("x-github-request-id") or ""), body_sha)
        stale = signature == self.last_signature.get(kind) and all(signature)
        self.last_signature[kind] = signature
        artifacts = value.get("artifacts") if kind == "artifacts" and isinstance(value, dict) else []
        ids = []
        if isinstance(artifacts, list):
            for item in artifacts:
                try:
                    ids.append(int(item.get("id")))
                except Exception:
                    pass
        rec = {
            "endpoint": endpoint,
            "poll_number": poll,
            "observed_at": last.get("observed_at") or _now(),
            "http_status": last.get("http_status"),
            "run_status": value.get("status") if kind == "run" and isinstance(value, dict) else None,
            "run_conclusion": value.get("conclusion") if kind == "run" and isinstance(value, dict) else None,
            "returned_artifact_ids": ids,
            "date": headers.get("date"),
            "x_github_request_id": headers.get("x-github-request-id"),
            "body_sha256": body_sha,
            "rate_limit_headers": {k: v for k, v in headers.items() if k.startswith("x-ratelimit-") or k == "retry-after"},
            "wait_or_rejection_reason": reason,
            "stale_response_suspected": stale,
            "freshness_status": "STALE_RESPONSE_SUSPECTED" if stale else "FRESH_RESPONSE_SIGNATURE",
            "api_attempts": attempts,
        }
        if page is not None:
            rec["page"] = page
        return rec

    @staticmethod
    def _workflow_id(audit: dict[str, Any]) -> int:
        try:
            value = int(audit.get("formal_workflow_id"))
            if value > 0:
                return value
        except Exception:
            pass
        _fail("AUTO_DISPATCH_FORMAL_WORKFLOW_ID_MISSING")

    @staticmethod
    def _identity_reasons(run: Any, audit: dict[str, Any], workflow_id: int) -> list[str]:
        if not isinstance(run, dict):
            return ["RUN_JSON_INVALID"]
        reasons: list[str] = []
        try:
            if int(run.get("id")) != int(audit.get("formal_run_id")):
                reasons.append("RUN_ID_MISMATCH")
        except Exception:
            reasons.append("RUN_ID_MISMATCH")
        try:
            if int(run.get("workflow_id")) != workflow_id:
                reasons.append("WORKFLOW_ID_MISMATCH")
        except Exception:
            reasons.append("WORKFLOW_ID_MISMATCH")
        if run.get("event") != "workflow_dispatch":
            reasons.append("EVENT_MISMATCH")
        if run.get("head_branch") != CANONICAL_REF:
            reasons.append("CANONICAL_REF_MISMATCH")
        if run.get("head_sha") != audit.get("canonical_execution_sha"):
            reasons.append("CANONICAL_SHA_MISMATCH")
        if run.get("display_title") != expected_display_title(str(audit.get("request_sha256") or "")):
            reasons.append("REQUEST_SHA_DISPLAY_TITLE_MISMATCH")
        return reasons

    def wait_terminal(self, audit: dict[str, Any], diagnostics: dict[str, Any], persist: Callable[[], None]) -> dict[str, Any]:
        run_id = int(audit.get("formal_run_id") or 0)
        workflow_id = self._workflow_id(audit)
        if run_id <= 0:
            _fail("AUTO_DISPATCH_FORMAL_RUN_ID_INVALID")
        records = diagnostics.setdefault("run_polls", [])
        for poll in range(1, self.run_attempts + 1):
            query = urllib.parse.urlencode({"f3_terminal_cache_buster": self._buster(poll, "run")})
            endpoint = f"/repos/{self.transport.repo}/actions/runs/{run_id}?{query}"
            value, evidence = self.transport.get_json_with_evidence(endpoint)
            reasons = self._identity_reasons(value, audit, workflow_id)
            if reasons:
                records.append(self._record("run", poll, endpoint, evidence, value, "IDENTITY_REJECTED:" + ",".join(reasons))); persist()
                _fail("AUTO_DISPATCH_FORMAL_RUN_IDENTITY_MISMATCH:" + ",".join(reasons))
            status = value.get("status")
            conclusion = value.get("conclusion")
            if status == "completed":
                if conclusion != "success":
                    records.append(self._record("run", poll, endpoint, evidence, value, f"TERMINAL_FAIL_CLOSED:{conclusion}")); persist()
                    _fail(f"AUTO_DISPATCH_FORMAL_RUN_FAILED:{conclusion}")
                records.append(self._record("run", poll, endpoint, evidence, value, "TERMINAL_SUCCESS_VERIFIED")); diagnostics["terminal_verified_at"] = _now(); persist()
                return value
            records.append(self._record("run", poll, endpoint, evidence, value, f"WAIT_FOR_TERMINAL:{status}")); persist()
            if poll < self.run_attempts:
                self.sleep_fn(self.run_delay_seconds)
        _fail("AUTO_DISPATCH_FORMAL_RUN_TIMEOUT")

    def wait_receipt_artifact(self, audit: dict[str, Any], diagnostics: dict[str, Any], persist: Callable[[], None]) -> dict[str, Any]:
        run_id = int(audit["formal_run_id"])
        target = f"formal-gpt-runner-receipt-{run_id}"
        records = diagnostics.setdefault("artifact_polls", [])
        for poll in range(1, self.artifact_attempts + 1):
            visible: list[dict[str, Any]] = []
            seen = 0
            for page in range(1, self.max_pages + 1):
                query = urllib.parse.urlencode({"per_page": 100, "page": page, "f3_artifact_cache_buster": self._buster(poll, "artifacts", page)})
                endpoint = f"/repos/{self.transport.repo}/actions/runs/{run_id}/artifacts?{query}"
                value, evidence = self.transport.get_json_with_evidence(endpoint)
                artifacts = value.get("artifacts") if isinstance(value, dict) else None
                if not isinstance(artifacts, list):
                    records.append(self._record("artifacts", poll, endpoint, evidence, value, "ARTIFACT_LIST_INVALID", page)); persist()
                    _fail("AUTO_DISPATCH_FORMAL_ARTIFACT_LIST_INVALID")
                clean = [a for a in artifacts if isinstance(a, dict)]
                visible.extend(clean); seen += len(clean)
                records.append(self._record("artifacts", poll, endpoint, evidence, value, "ARTIFACT_PAGE_OBSERVED", page)); persist()
                total = value.get("total_count")
                if len(artifacts) < 100 or (isinstance(total, int) and seen >= total):
                    break
                if page == self.max_pages:
                    _fail("AUTO_DISPATCH_FORMAL_ARTIFACT_PAGINATION_LIMIT")
            matches = [a for a in visible if a.get("name") == target and a.get("expired") is not True]
            if len(matches) > 1:
                _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_AMBIGUOUS")
            if len(matches) == 1:
                artifact = matches[0]
                try:
                    int(artifact.get("id"))
                except Exception:
                    _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_ID_INVALID")
                digest = artifact.get("digest")
                if not isinstance(digest, str) or not digest.startswith("sha256:") or not _is_hex(digest[7:], 64):
                    _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_DIGEST_MISSING")
                diagnostics["receipt_artifact_visible_at"] = _now(); diagnostics["receipt_artifact_id"] = int(artifact["id"]); persist()
                return artifact
            diagnostics["artifact_wait_reason"] = "FORMAL_RUN_SUCCESS_RECEIPT_NOT_YET_VISIBLE"; persist()
            if poll < self.artifact_attempts:
                self.sleep_fn(self.artifact_delay_seconds)
        _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_TIMEOUT")

    @staticmethod
    def _zip_json(zf: zipfile.ZipFile, name: str) -> tuple[dict[str, Any], bytes]:
        matches = [n for n in zf.namelist() if pathlib.PurePosixPath(n).name == name]
        if len(matches) != 1:
            _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_FILE_MISSING:{name}")
        raw = zf.read(matches[0])
        try:
            value = json.loads(raw)
        except Exception as exc:
            raise FinalizerError(f"AUTO_DISPATCH_FORMAL_RECEIPT_JSON_INVALID:{name}") from exc
        if not isinstance(value, dict):
            _fail(f"AUTO_DISPATCH_FORMAL_RECEIPT_JSON_INVALID:{name}")
        return value, raw

    def validate_receipt_zip(self, audit: dict[str, Any], artifact: dict[str, Any], zip_bytes: bytes) -> dict[str, Any]:
        request_mode = _trusted_audit_request_mode(audit)
        zip_sha = hashlib.sha256(zip_bytes).hexdigest()
        if artifact.get("digest") != f"sha256:{zip_sha}":
            _fail("AUTO_DISPATCH_FORMAL_RECEIPT_ARTIFACT_DIGEST_MISMATCH")
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except Exception as exc:
            raise FinalizerError("AUTO_DISPATCH_FORMAL_RECEIPT_ZIP_INVALID") from exc
        binding, binding_raw = self._zip_json(zf, "request_sha_binding_receipt.json")
        summary, summary_raw = self._zip_json(zf, "summary.json")
        prediction, prediction_raw = self._zip_json(zf, "prediction_receipt.json")
        execution, execution_raw = self._zip_json(zf, "production_execution_binding_receipt.json")
        state, state_raw = self._zip_json(zf, "state_integrity_audit.json")
        run_id = int(audit["formal_run_id"])
        request_sha = audit.get("request_sha256")
        canonical_sha = audit.get("canonical_execution_sha")
        if binding.get("status") != "PASS" or binding.get("request_id") != audit.get("request_id") or binding.get("request_sha256") != request_sha or binding.get("expected_request_sha256") != request_sha or binding.get("request_sha_verified") is not True:
            _fail("AUTO_DISPATCH_FORMAL_REQUEST_BINDING_MISMATCH")
        if int(binding.get("production_run_id") or 0) != run_id or binding.get("canonical_execution_sha") != canonical_sha or binding.get("carrier_head_sha") != audit.get("carrier_head_sha"):
            _fail("AUTO_DISPATCH_FORMAL_AUTHORITY_BINDING_MISMATCH")
        prediction_sha = summary.get("prediction_sha")
        if summary.get("status") != "PASS" or not _is_hex(prediction_sha, 64) or prediction.get("prediction_sha") != prediction_sha:
            _fail("AUTO_DISPATCH_FORMAL_PREDICTION_SHA_MISMATCH")
        _validate_state_integrity_contract(
            audit,
            state,
            prediction,
            prediction_raw,
            execution,
            request_mode=request_mode,
            run_id=run_id,
            request_sha=str(request_sha or ""),
            canonical_sha=str(canonical_sha or ""),
            prediction_sha=prediction_sha,
        )
        if execution.get("status") != "PASS" or int(execution.get("production_run_id") or 0) != run_id or int(execution.get("run_id") or 0) != run_id:
            _fail("AUTO_DISPATCH_FORMAL_EXECUTION_BINDING_MISMATCH")
        if execution.get("request_sha256") != request_sha or execution.get("canonical_execution_sha") != canonical_sha or execution.get("workflow_sha") != canonical_sha or execution.get("prediction_sha") != prediction_sha:
            _fail("AUTO_DISPATCH_FORMAL_EXECUTION_AUTHORITY_MISMATCH")
        if execution.get("summary_sha256") != hashlib.sha256(summary_raw).hexdigest() or execution.get("prediction_receipt_sha256") != hashlib.sha256(prediction_raw).hexdigest():
            _fail("AUTO_DISPATCH_FORMAL_INTERNAL_SHA_MISMATCH")
        return {
            "artifact_zip_sha256": zip_sha,
            "request_binding_sha256": hashlib.sha256(binding_raw).hexdigest(),
            "prediction_receipt_sha256": hashlib.sha256(prediction_raw).hexdigest(),
            "execution_binding_sha256": hashlib.sha256(execution_raw).hexdigest(),
            "state_integrity_sha256": hashlib.sha256(state_raw).hexdigest(),
            "prediction_sha": prediction_sha,
        }

    def finalize(self, audit: dict[str, Any], diagnostics: dict[str, Any], persist: Callable[[], None]) -> dict[str, Any]:
        if not isinstance(audit, dict) or audit.get("phase") != "DISPATCHED" or audit.get("dispatch_performed") is not True:
            _fail("AUTO_DISPATCH_AUDIT_NOT_DISPATCHED")
        if not _is_hex(audit.get("request_sha256"), 64) or not _is_hex(audit.get("canonical_execution_sha"), 40):
            _fail("AUTO_DISPATCH_FINALIZER_AUDIT_IDENTITY_INVALID")
        _trusted_audit_request_mode(audit)
        run = self.wait_terminal(audit, diagnostics, persist)
        artifact = self.wait_receipt_artifact(audit, diagnostics, persist)
        zip_bytes = self.transport.download_artifact_zip(int(artifact["id"]), diagnostics, persist)
        receipt = self.validate_receipt_zip(audit, artifact, zip_bytes)
        diagnostics["receipt_validation"] = {"status": "PASS", **receipt}; persist()
        return {
            "schema_version": FINAL_RECEIPT_SCHEMA,
            "status": "PASS",
            "request_id": audit.get("request_id"),
            "request_sha256": audit.get("request_sha256"),
            "carrier_pr_number": audit.get("carrier_pr_number"),
            "carrier_head_sha": audit.get("carrier_head_sha"),
            "trusted_dispatcher_sha": audit.get("trusted_dispatcher_sha"),
            "trusted_dispatcher_run_id": audit.get("trusted_dispatcher_run_id"),
            "receiver_run_id": audit.get("receiver_run_id"),
            "canonical_integration_execution_sha": audit.get("canonical_execution_sha"),
            "resulting_production_run_id": int(run["id"]),
            "formal_receipt_artifact_id": int(artifact["id"]),
            "formal_receipt_artifact_digest": artifact.get("digest"),
            "formal_request_binding_receipt_sha256": receipt["request_binding_sha256"],
            "formal_prediction_receipt_sha256": receipt["prediction_receipt_sha256"],
            "formal_execution_binding_receipt_sha256": receipt["execution_binding_sha256"],
            "formal_state_integrity_receipt_sha256": receipt["state_integrity_sha256"],
            "prediction_sha": receipt["prediction_sha"],
            "dispatch_performed": True,
            "request_carrier_code_executed": False,
            "finalizer_schema": FINALIZER_SCHEMA,
            "completed_at": _now(),
        }


def finalize_command(args: argparse.Namespace) -> int:
    audit_path = pathlib.Path(args.audit)
    diag_path = pathlib.Path(args.diagnostic_out)
    final_path = pathlib.Path(args.final_receipt_out)
    try:
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FinalizerError("AUTO_DISPATCH_AUDIT_INVALID") from exc
    diagnostics: dict[str, Any] = {
        "schema_version": FINALIZER_SCHEMA,
        "status": "IN_PROGRESS",
        "formal_run_id": audit.get("formal_run_id") if isinstance(audit, dict) else None,
        "request_sha256": audit.get("request_sha256") if isinstance(audit, dict) else None,
        "canonical_execution_sha": audit.get("canonical_execution_sha") if isinstance(audit, dict) else None,
        "started_at": _now(),
        "run_polls": [],
        "artifact_polls": [],
        "artifact_download_requests": [],
    }

    def persist() -> None:
        _atomic_json(diag_path, diagnostics)

    persist()
    transport = GitHubTransport(args.repo, args.token)
    finalizer = FormalTerminalFinalizer(transport)
    try:
        final_receipt = finalizer.finalize(audit, diagnostics, persist)
    except FinalizerError as exc:
        diagnostics.update({"status": "FAIL_CLOSED", "error_code": str(exc), "failed_at": _now(), "http_request_count": transport.http_request_count})
        persist()
        raise
    diagnostics.update({"status": "PASS", "completed_at": _now(), "http_request_count": transport.http_request_count})
    persist()
    audit.update({
        "phase": "COMPLETED",
        "status": "PASS",
        "prediction_sha": final_receipt["prediction_sha"],
        "final_binding_receipt": final_receipt,
        "formal_terminal_finalizer_diagnostics_sha256": hashlib.sha256(diag_path.read_bytes()).hexdigest(),
    })
    _atomic_json(audit_path, audit)
    _atomic_json(final_path, final_receipt)
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"result=COMPLETED\nformal_run_id={audit['formal_run_id']}\nformal_receipt_artifact_id={final_receipt['formal_receipt_artifact_id']}\nprediction_sha={final_receipt['prediction_sha']}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    final = sub.add_parser("finalize")
    final.add_argument("--repo", required=True)
    final.add_argument("--token", required=True)
    final.add_argument("--audit", required=True)
    final.add_argument("--final-receipt-out", required=True)
    final.add_argument("--diagnostic-out", required=True)
    final.set_defaults(func=finalize_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except FinalizerError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
