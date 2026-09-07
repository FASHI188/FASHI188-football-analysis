#!/usr/bin/env python3
from __future__ import annotations

import copy
import email.utils
import io
import json
import os
import threading
import time
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SCHEMA = "football3-github-api-budget-transport-v1"
INVENTORY_SCHEMA = "football3-durable-candidate-inventory-v1"
ALLOWED_HEAD_BRANCHES = {
    "football3/formal-gpt-runner-integration-v1",
    "football3/formal-gpt-runner-request-carrier-v1",
    "football3/durable-state-cutoff-selector-governed-v1",
}
_MAX_ATTEMPTS = int(os.environ.get("FOOTBALL3_GITHUB_API_MAX_ATTEMPTS", "4"))
_MAX_PRIMARY_WAIT = float(os.environ.get("FOOTBALL3_GITHUB_API_MAX_PRIMARY_WAIT_SECONDS", "20"))
_MAX_SECONDARY_WAIT = float(os.environ.get("FOOTBALL3_GITHUB_API_MAX_SECONDARY_WAIT_SECONDS", "30"))
_API_BUDGET = int(os.environ.get("FOOTBALL3_GITHUB_API_REQUEST_BUDGET", "400"))

_ORIGINAL_URLOPEN = urllib.request.urlopen
_INSTALLED = False
_LOCK = threading.RLock()
_CACHE: dict[str, tuple[bytes, dict[str, str], int]] = {}
_RUN_META: dict[int, dict[str, Any]] = {}
_CANDIDATE_RUN_IDS: set[int] = set()
_INVENTORY_CACHE: dict[str, Any] | None = None
_STATS: dict[str, Any] = {}


class GitHubApiBudgetError(RuntimeError):
    pass


class _BufferedResponse(io.BytesIO):
    def __init__(self, payload: bytes, headers: dict[str, str] | None = None, status: int = 200, url: str | None = None):
        super().__init__(payload)
        msg = Message()
        for k, v in (headers or {}).items():
            msg[k] = str(v)
        self.headers = msg
        self.status = status
        self.code = status
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def _reset_stats() -> None:
    _STATS.clear()
    _STATS.update({
        "schema_version": SCHEMA,
        "artifact_list_pages": 0,
        "run_list_pages": 0,
        "suppressed_run_list_requests": 0,
        "individual_run_get_count": 0,
        "artifact_download_count": 0,
        "cache_hits": 0,
        "network_request_count": 0,
        "rate_limit_remaining_start": None,
        "rate_limit_remaining_end": None,
        "rate_limit_reset_end": None,
        "rate_limit_resource_end": None,
        "api_budget": _API_BUDGET,
        "inventory_sha": None,
        "inventory_mode": False,
    })


_reset_stats()


def reset_for_tests() -> None:
    global _INVENTORY_CACHE
    with _LOCK:
        _CACHE.clear()
        _RUN_META.clear()
        _CANDIDATE_RUN_IDS.clear()
        _INVENTORY_CACHE = None
        _reset_stats()


def snapshot() -> dict[str, Any]:
    with _LOCK:
        return copy.deepcopy(_STATS)


def _persist_stats() -> None:
    path = os.environ.get("FOOTBALL3_GITHUB_API_STATS_PATH")
    if not path:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(snapshot(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _note_rate(headers: Any) -> None:
    if headers is None:
        return
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    resource = headers.get("x-ratelimit-resource")
    if remaining is not None and _STATS["rate_limit_remaining_start"] is None:
        _STATS["rate_limit_remaining_start"] = str(remaining)
    if remaining is not None:
        _STATS["rate_limit_remaining_end"] = str(remaining)
    if reset is not None:
        _STATS["rate_limit_reset_end"] = str(reset)
    if resource is not None:
        _STATS["rate_limit_resource_end"] = str(resource)


def _kind(url: str) -> str:
    if "/actions/artifacts?" in url:
        return "artifact_list"
    if "/actions/runs?" in url:
        return "run_list"
    if "/actions/runs/" in url:
        return "run_get"
    if "/actions/artifacts/" in url and url.endswith("/zip"):
        return "artifact_download"
    return "other"


def _note_network(kind: str) -> None:
    next_count = int(_STATS["network_request_count"]) + 1
    if next_count > int(_STATS["api_budget"]):
        raise GitHubApiBudgetError(f"API_REQUEST_BUDGET_EXCEEDED:count={next_count}:budget={_STATS['api_budget']}")
    _STATS["network_request_count"] = next_count
    if kind == "artifact_list":
        _STATS["artifact_list_pages"] += 1
    elif kind == "run_list":
        _STATS["run_list_pages"] += 1
    elif kind == "run_get":
        _STATS["individual_run_get_count"] += 1
    elif kind == "artifact_download":
        _STATS["artifact_download_count"] += 1


def _error_details(exc: urllib.error.HTTPError, url: str) -> dict[str, Any]:
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
    _note_rate(exc.headers)
    return {
        "status": int(exc.code),
        "url": url,
        "message": payload.get("message"),
        "documentation_url": payload.get("documentation_url"),
        "retry_after": exc.headers.get("retry-after") if exc.headers else None,
        "remaining": exc.headers.get("x-ratelimit-remaining") if exc.headers else None,
        "reset": exc.headers.get("x-ratelimit-reset") if exc.headers else None,
        "resource": exc.headers.get("x-ratelimit-resource") if exc.headers else None,
    }


def _permission_403(d: dict[str, Any]) -> bool:
    if d.get("status") != 403:
        return False
    msg = str(d.get("message") or "").lower()
    return any(x in msg for x in ("resource not accessible by integration", "insufficient permission", "must have admin rights", "requires authentication"))


def _primary_exhausted(d: dict[str, Any]) -> bool:
    return d.get("status") == 403 and str(d.get("remaining") or "") == "0"


def _secondary(d: dict[str, Any]) -> bool:
    if d.get("status") == 429:
        return True
    if d.get("status") != 403 or _permission_403(d) or _primary_exhausted(d):
        return False
    msg = str(d.get("message") or "").lower()
    return d.get("retry_after") is not None or "secondary rate" in msg or "abuse detection" in msg or "too many requests" in msg


def _retry_after(d: dict[str, Any], attempt: int) -> float:
    value = d.get("retry_after")
    if value is not None:
        s = str(value).strip()
        try:
            return max(float(s), 0.0)
        except ValueError:
            try:
                return max(email.utils.parsedate_to_datetime(s).timestamp() - time.time(), 0.0)
            except Exception:
                pass
    return min(float(2 ** attempt), _MAX_SECONDARY_WAIT)


def _network_bytes(req: Any, *args, **kwargs) -> tuple[bytes, dict[str, str], int]:
    url = req.full_url if hasattr(req, "full_url") else str(req)
    kind = _kind(url)
    for attempt in range(_MAX_ATTEMPTS):
        _note_network(kind)
        try:
            with _ORIGINAL_URLOPEN(req, *args, **kwargs) as response:
                _note_rate(response.headers)
                payload = response.read()
                headers = {k.lower(): v for k, v in response.headers.items()}
                status = int(getattr(response, "status", getattr(response, "code", 200)) or 200)
                _persist_stats()
                return payload, headers, status
        except urllib.error.HTTPError as exc:
            d = _error_details(exc, url)
            _persist_stats()
            if _permission_403(d):
                raise GitHubApiBudgetError("PERMISSION_403:" + json.dumps(d, sort_keys=True, separators=(",", ":"))) from exc
            if _primary_exhausted(d):
                try:
                    reset = float(str(d.get("reset") or ""))
                except ValueError:
                    raise GitHubApiBudgetError("RATE_LIMIT_EXHAUSTED:" + json.dumps(d, sort_keys=True, separators=(",", ":"))) from exc
                wait = max(reset - time.time(), 0.0)
                if wait > _MAX_PRIMARY_WAIT or attempt + 1 >= _MAX_ATTEMPTS:
                    raise GitHubApiBudgetError("RATE_LIMIT_EXHAUSTED:" + json.dumps({**d, "required_wait_seconds": wait, "allowed_wait_seconds": _MAX_PRIMARY_WAIT}, sort_keys=True, separators=(",", ":"))) from exc
                if wait:
                    time.sleep(wait)
                continue
            if _secondary(d) and attempt + 1 < _MAX_ATTEMPTS:
                wait = _retry_after(d, attempt)
                if wait > _MAX_SECONDARY_WAIT:
                    raise GitHubApiBudgetError("SECONDARY_RATE_LIMIT_RETRY_WINDOW_EXCEEDED:" + json.dumps(d, sort_keys=True, separators=(",", ":"))) from exc
                if wait:
                    time.sleep(wait)
                continue
            raise
    raise GitHubApiBudgetError("HTTP_RETRY_EXHAUSTED:" + url)


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_artifact_page(payload: bytes) -> None:
    try:
        obj = json.loads(payload)
    except Exception:
        return
    rows = obj.get("artifacts") if type(obj) is dict else None
    if type(rows) is not list:
        return
    for artifact in rows:
        if type(artifact) is not dict:
            continue
        if artifact.get("expired") or not str(artifact.get("name") or "").startswith("formal-gpt-runner-state-"):
            continue
        wr = artifact.get("workflow_run") or {}
        if type(wr) is not dict:
            continue
        branch = wr.get("head_branch")
        if branch is not None and branch not in ALLOWED_HEAD_BRANCHES:
            continue
        run_id = int(wr.get("id") or 0)
        if not run_id:
            continue
        _CANDIDATE_RUN_IDS.add(run_id)
        cur = _RUN_META.setdefault(run_id, {})
        for key in ("id", "head_branch", "head_sha", "status", "conclusion"):
            if wr.get(key) is not None and cur.get(key) is None:
                cur[key] = wr.get(key)


def _run_complete(row: dict[str, Any]) -> bool:
    return bool(row.get("id") and row.get("head_branch") and row.get("head_sha") and row.get("status") and row.get("conclusion"))


def _targeted_run(run_id: int, token_req: Any | None = None) -> dict[str, Any]:
    cur = _RUN_META.setdefault(run_id, {"id": run_id})
    if _run_complete(cur):
        _STATS["cache_hits"] += 1
        return copy.deepcopy(cur)
    url = f"https://api.github.com/repos/{_repo_from_url(token_req.full_url if token_req is not None else '')}/actions/runs/{run_id}"
    if not _repo_from_url(url):
        raise GitHubApiBudgetError(f"candidate run repository unavailable for {run_id}")
    headers = {}
    if token_req is not None and hasattr(token_req, "header_items"):
        headers = dict(token_req.header_items())
    req = urllib.request.Request(url, headers=headers)
    payload, _, _ = _network_bytes(req)
    row = json.loads(payload)
    if type(row) is not dict:
        raise GitHubApiBudgetError(f"run metadata schema mismatch for {run_id}")
    cur.update({k: row.get(k) for k in ("id", "head_branch", "head_sha", "status", "conclusion") if row.get(k) is not None})
    if not _run_complete(cur):
        raise GitHubApiBudgetError(f"run metadata incomplete for {run_id}")
    return copy.deepcopy(cur)


def _repo_from_url(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    return ""


def _page(url: str) -> tuple[int, int]:
    q = parse_qs(urlparse(url).query)
    return int((q.get("page") or ["1"])[0]), int((q.get("per_page") or ["100"])[0])


def _load_inventory() -> dict[str, Any] | None:
    global _INVENTORY_CACHE
    path = os.environ.get("FOOTBALL3_DURABLE_CANDIDATE_INVENTORY")
    if not path:
        return None
    if _INVENTORY_CACHE is None:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
        if type(obj) is not dict or obj.get("schema_version") != INVENTORY_SCHEMA:
            raise GitHubApiBudgetError("candidate inventory schema mismatch")
        core = {k: obj[k] for k in ("schema_version", "repository", "artifacts", "runs")}
        import hashlib
        sha = hashlib.sha256(_json_bytes(core)).hexdigest()
        if sha != obj.get("inventory_sha"):
            raise GitHubApiBudgetError("candidate inventory SHA mismatch")
        _INVENTORY_CACHE = obj
        _STATS["inventory_mode"] = True
        _STATS["inventory_sha"] = sha
    return _INVENTORY_CACHE


def _inventory_response(url: str) -> _BufferedResponse | None:
    inv = _load_inventory()
    if inv is None:
        return None
    page, per_page = _page(url)
    start = (page - 1) * per_page
    if "/actions/artifacts?" in url:
        rows = inv["artifacts"][start:start + per_page]
        return _BufferedResponse(_json_bytes({"artifacts": rows, "total_count": len(inv["artifacts"])}), {}, 200, url)
    if "/actions/runs?" in url:
        _STATS["suppressed_run_list_requests"] += 1
        rows = inv["runs"][start:start + per_page]
        return _BufferedResponse(_json_bytes({"workflow_runs": rows, "total_count": len(inv["runs"])}), {}, 200, url)
    return None


def _synthesized_run_list(req: Any, url: str) -> _BufferedResponse:
    _STATS["suppressed_run_list_requests"] += 1
    rows = [_targeted_run(run_id, req) for run_id in sorted(_CANDIDATE_RUN_IDS)]
    page, per_page = _page(url)
    start = (page - 1) * per_page
    return _BufferedResponse(_json_bytes({"workflow_runs": rows[start:start + per_page], "total_count": len(rows)}), {}, 200, url)


def urlopen(req: Any, *args, **kwargs):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    with _LOCK:
        inv_response = _inventory_response(url)
        if inv_response is not None:
            _STATS["cache_hits"] += 1
            _persist_stats()
            return inv_response
        if "/actions/runs?" in url:
            response = _synthesized_run_list(req, url)
            _persist_stats()
            return response
        cached = _CACHE.get(url)
        if cached is not None:
            _STATS["cache_hits"] += 1
            _persist_stats()
            payload, headers, status = cached
            return _BufferedResponse(payload, headers, status, url)
        payload, headers, status = _network_bytes(req, *args, **kwargs)
        _CACHE[url] = (payload, headers, status)
        if "/actions/artifacts?" in url:
            _record_artifact_page(payload)
        if "/actions/runs/" in url and "/actions/runs?" not in url:
            try:
                row = json.loads(payload)
                if type(row) is dict and row.get("id"):
                    _RUN_META[int(row["id"])] = {k: row.get(k) for k in ("id", "head_branch", "head_sha", "status", "conclusion")}
            except Exception:
                pass
        _persist_stats()
        return _BufferedResponse(payload, headers, status, url)


def install() -> None:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        urllib.request.urlopen = urlopen
        _INSTALLED = True


if __name__ == "__main__":
    print(json.dumps(snapshot(), sort_keys=True))
