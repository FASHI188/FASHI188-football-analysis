#!/usr/bin/env python3
from __future__ import annotations

import copy
import email.utils
import hashlib
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
        for key, value in (headers or {}).items():
            msg[key] = str(value)
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
        "inventory_artifact_cache_hits": 0,
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
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _is_actions_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "api.github.com" and "/actions/" in parsed.path and parsed.path.startswith("/repos/")


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


def _permission_403(details: dict[str, Any]) -> bool:
    if details.get("status") != 403:
        return False
    message = str(details.get("message") or "").lower()
    return any(marker in message for marker in (
        "resource not accessible by integration",
        "insufficient permission",
        "must have admin rights",
        "requires authentication",
    ))


def _primary_exhausted(details: dict[str, Any]) -> bool:
    return details.get("status") == 403 and str(details.get("remaining") or "") == "0"


def _secondary(details: dict[str, Any]) -> bool:
    if details.get("status") == 429:
        return True
    if details.get("status") != 403 or _permission_403(details) or _primary_exhausted(details):
        return False
    message = str(details.get("message") or "").lower()
    return (
        details.get("retry_after") is not None
        or "secondary rate" in message
        or "abuse detection" in message
        or "too many requests" in message
    )


def _retry_after(details: dict[str, Any], attempt: int) -> float:
    value = details.get("retry_after")
    if value is not None:
        text = str(value).strip()
        try:
            return max(float(text), 0.0)
        except ValueError:
            try:
                return max(email.utils.parsedate_to_datetime(text).timestamp() - time.time(), 0.0)
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
                headers = {key.lower(): value for key, value in response.headers.items()}
                status = int(getattr(response, "status", getattr(response, "code", 200)) or 200)
                _persist_stats()
                return payload, headers, status
        except urllib.error.HTTPError as exc:
            details = _error_details(exc, url)
            _persist_stats()
            if _permission_403(details):
                raise GitHubApiBudgetError("PERMISSION_403:" + json.dumps(details, sort_keys=True, separators=(",", ":"))) from exc
            if _primary_exhausted(details):
                try:
                    reset = float(str(details.get("reset") or ""))
                except ValueError:
                    raise GitHubApiBudgetError("RATE_LIMIT_EXHAUSTED:" + json.dumps(details, sort_keys=True, separators=(",", ":"))) from exc
                wait = max(reset - time.time(), 0.0)
                if wait > _MAX_PRIMARY_WAIT or attempt + 1 >= _MAX_ATTEMPTS:
                    payload = {**details, "required_wait_seconds": wait, "allowed_wait_seconds": _MAX_PRIMARY_WAIT}
                    raise GitHubApiBudgetError("RATE_LIMIT_EXHAUSTED:" + json.dumps(payload, sort_keys=True, separators=(",", ":"))) from exc
                if wait:
                    time.sleep(wait)
                continue
            if _secondary(details) and attempt + 1 < _MAX_ATTEMPTS:
                wait = _retry_after(details, attempt)
                if wait > _MAX_SECONDARY_WAIT:
                    raise GitHubApiBudgetError("SECONDARY_RATE_LIMIT_RETRY_WINDOW_EXCEEDED:" + json.dumps(details, sort_keys=True, separators=(",", ":"))) from exc
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
        workflow_run = artifact.get("workflow_run") or {}
        if type(workflow_run) is not dict:
            continue
        branch = workflow_run.get("head_branch")
        if branch is not None and branch not in ALLOWED_HEAD_BRANCHES:
            continue
        run_id = int(workflow_run.get("id") or 0)
        if not run_id:
            continue
        _CANDIDATE_RUN_IDS.add(run_id)
        current = _RUN_META.setdefault(run_id, {})
        for key in ("id", "head_branch", "head_sha", "status", "conclusion"):
            if workflow_run.get(key) is not None and current.get(key) is None:
                current[key] = workflow_run.get(key)


def _run_complete(row: dict[str, Any]) -> bool:
    return bool(row.get("id") and row.get("head_branch") and row.get("head_sha") and row.get("status") and row.get("conclusion"))


def _repo_from_url(url: str) -> str:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 3 and parts[0] == "repos":
        return f"{parts[1]}/{parts[2]}"
    return ""


def _targeted_run(run_id: int, token_req: Any) -> dict[str, Any]:
    current = _RUN_META.setdefault(run_id, {"id": run_id})
    if _run_complete(current):
        _STATS["cache_hits"] += 1
        return copy.deepcopy(current)
    repo = _repo_from_url(token_req.full_url)
    if not repo:
        raise GitHubApiBudgetError(f"candidate run repository unavailable for {run_id}")
    headers = dict(token_req.header_items()) if hasattr(token_req, "header_items") else {}
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}", headers=headers)
    payload, _, _ = _network_bytes(req)
    row = json.loads(payload)
    if type(row) is not dict:
        raise GitHubApiBudgetError(f"run metadata schema mismatch for {run_id}")
    current.update({key: row.get(key) for key in ("id", "head_branch", "head_sha", "status", "conclusion") if row.get(key) is not None})
    if not _run_complete(current):
        raise GitHubApiBudgetError(f"run metadata incomplete for {run_id}")
    return copy.deepcopy(current)


def _page(url: str) -> tuple[int, int]:
    query = parse_qs(urlparse(url).query)
    return int((query.get("page") or ["1"])[0]), int((query.get("per_page") or ["100"])[0])


def _load_inventory() -> dict[str, Any] | None:
    global _INVENTORY_CACHE
    path = os.environ.get("FOOTBALL3_DURABLE_CANDIDATE_INVENTORY")
    if not path:
        return None
    if _INVENTORY_CACHE is None:
        inventory_path = Path(path)
        obj = json.loads(inventory_path.read_text(encoding="utf-8"))
        if type(obj) is not dict or obj.get("schema_version") != INVENTORY_SCHEMA:
            raise GitHubApiBudgetError("candidate inventory schema mismatch")
        core = {key: obj[key] for key in ("schema_version", "repository", "artifacts", "runs")}
        sha = hashlib.sha256(_json_bytes(core)).hexdigest()
        if sha != obj.get("inventory_sha"):
            raise GitHubApiBudgetError("candidate inventory SHA mismatch")
        for artifact in obj.get("artifacts") or []:
            rel = artifact.get("cached_zip_relpath")
            expected = artifact.get("cached_zip_sha256")
            if rel:
                candidate = (inventory_path.parent / str(rel)).resolve()
                root = inventory_path.parent.resolve()
                if root not in candidate.parents:
                    raise GitHubApiBudgetError("candidate inventory cache path escape")
                if not candidate.is_file():
                    raise GitHubApiBudgetError(f"candidate cached artifact missing:{artifact.get('id')}")
                actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if actual != expected:
                    raise GitHubApiBudgetError(f"candidate cached artifact SHA mismatch:{artifact.get('id')}")
        _INVENTORY_CACHE = obj
        _STATS["inventory_mode"] = True
        _STATS["inventory_sha"] = sha
    return _INVENTORY_CACHE


def _inventory_artifact_bytes(inv: dict[str, Any], url: str) -> bytes | None:
    if not ("/actions/artifacts/" in url and url.endswith("/zip")):
        return None
    try:
        artifact_id = int(urlparse(url).path.rstrip("/").split("/")[-2])
    except Exception:
        return None
    inventory_path = Path(os.environ["FOOTBALL3_DURABLE_CANDIDATE_INVENTORY"])
    for artifact in inv.get("artifacts") or []:
        if int(artifact.get("id") or 0) != artifact_id:
            continue
        rel = artifact.get("cached_zip_relpath")
        if not rel:
            return None
        data = (inventory_path.parent / str(rel)).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact.get("cached_zip_sha256"):
            raise GitHubApiBudgetError(f"candidate cached artifact SHA mismatch:{artifact_id}")
        return data
    return None


def _inventory_response(url: str) -> _BufferedResponse | None:
    inventory = _load_inventory()
    if inventory is None:
        return None
    artifact_bytes = _inventory_artifact_bytes(inventory, url)
    if artifact_bytes is not None:
        _STATS["inventory_artifact_cache_hits"] += 1
        return _BufferedResponse(artifact_bytes, {}, 200, url)
    page, per_page = _page(url)
    start = (page - 1) * per_page
    if "/actions/artifacts?" in url:
        rows = inventory["artifacts"][start:start + per_page]
        return _BufferedResponse(_json_bytes({"artifacts": rows, "total_count": len(inventory["artifacts"])}), {}, 200, url)
    if "/actions/runs?" in url:
        _STATS["suppressed_run_list_requests"] += 1
        rows = inventory["runs"][start:start + per_page]
        return _BufferedResponse(_json_bytes({"workflow_runs": rows, "total_count": len(inventory["runs"])}), {}, 200, url)
    return None


def _synthesized_run_list(req: Any, url: str) -> _BufferedResponse:
    _STATS["suppressed_run_list_requests"] += 1
    rows = [_targeted_run(run_id, req) for run_id in sorted(_CANDIDATE_RUN_IDS)]
    page, per_page = _page(url)
    start = (page - 1) * per_page
    return _BufferedResponse(_json_bytes({"workflow_runs": rows[start:start + per_page], "total_count": len(rows)}), {}, 200, url)


def urlopen(req: Any, *args, **kwargs):
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not _is_actions_url(url):
        return _ORIGINAL_URLOPEN(req, *args, **kwargs)
    with _LOCK:
        cached = _CACHE.get(url)
        if cached is not None:
            _STATS["cache_hits"] += 1
            _persist_stats()
            payload, headers, status = cached
            return _BufferedResponse(payload, headers, status, url)
        inventory_response = _inventory_response(url)
        if inventory_response is not None:
            payload = inventory_response.getvalue()
            _CACHE[url] = (payload, {}, 200)
            _STATS["cache_hits"] += 1
            _persist_stats()
            return _BufferedResponse(payload, {}, 200, url)
        if "/actions/runs?" in url:
            response = _synthesized_run_list(req, url)
            payload = response.getvalue()
            _CACHE[url] = (payload, {}, 200)
            _persist_stats()
            return _BufferedResponse(payload, {}, 200, url)
        payload, headers, status = _network_bytes(req, *args, **kwargs)
        _CACHE[url] = (payload, headers, status)
        if "/actions/artifacts?" in url:
            _record_artifact_page(payload)
        if "/actions/runs/" in url and "/actions/runs?" not in url:
            try:
                row = json.loads(payload)
                if type(row) is dict and row.get("id"):
                    _RUN_META[int(row["id"])] = {key: row.get(key) for key in ("id", "head_branch", "head_sha", "status", "conclusion")}
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
