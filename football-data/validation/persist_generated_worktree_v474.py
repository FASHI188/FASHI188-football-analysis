#!/usr/bin/env python3
"""Persist generated football artifacts from the current worktree via Contents API.

This helper is for long-running builders that generate a dynamic set of files.
It reads `git status --porcelain -z`, restricts writes to explicit generated-data
prefixes, and persists additions/modifications/deletions with optimistic SHA
concurrency and bounded retries. Source code, workflows, CURRENT files, and any
path outside the allowlist are never written by this helper.

V6.8.0 has one deliberately narrow synchronized-receipt exception: when the caller
explicitly allows the V6.8.0 market-ladder outputs, the V6.8.1 identifiability receipt
and V6.9 system-registry receipt may also be persisted. This closes the GitHub
GITHUB_TOKEN non-recursive workflow-trigger gap without broadening other workflows.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWED_PREFIXES = (
    "football-data/manifests/",
    "football-data/models/",
    "football-data/calibration/",
)
V680_SYNC_DEPENDENT_PATHS = {
    "football-data/manifests/v6_total_ladder_identifiability_v681_status.json",
    "football-data/manifests/v6_system_issue_registry_v690_status.json",
}
MAX_ATTEMPTS = 4


def _request(headers: dict[str, str], method: str, url: str, payload: dict[str, Any] | None = None, *, allow_conflict: bool = False):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return 404, None
        if allow_conflict and exc.code in {409, 422}:
            return exc.code, body
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code} {body}") from exc


def _status_entries() -> list[tuple[str, str]]:
    proc = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    raw = proc.stdout.decode("utf-8", errors="strict")
    items = raw.split("\0")
    out: list[tuple[str, str]] = []
    i = 0
    while i < len(items):
        item = items[i]
        if not item:
            i += 1
            continue
        status = item[:2]
        path = item[3:]
        if status[0] in {"R", "C"}:
            # Porcelain -z emits destination first and source as next NUL field.
            i += 1
            if i < len(items):
                _source = items[i]
        out.append((status, path))
        i += 1
    return out


def _v680_sync_requested(prefixes: tuple[str, ...]) -> bool:
    return any(
        prefix.startswith("football-data/evidence/market_ladders_v680/")
        or prefix.startswith("football-data/manifests/v6_full_market_ladder_v680_status.json")
        for prefix in prefixes
    )


def _allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    if "CURRENT_唯一正式规则" in Path(path).name:
        return False
    if any(path.startswith(prefix) for prefix in prefixes):
        return True
    return _v680_sync_requested(prefixes) and path in V680_SYNC_DEPENDENT_PATHS


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message-prefix", default="automation(football): persist generated")
    parser.add_argument("--allow-prefix", action="append", dest="prefixes")
    args = parser.parse_args()

    repo = os.environ.get("GH_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        raise SystemExit("GH_REPOSITORY/GITHUB_REPOSITORY and GH_TOKEN/GITHUB_TOKEN are required")
    prefixes = tuple(args.prefixes or DEFAULT_ALLOWED_PREFIXES)

    entries = _status_entries()
    blocked = [(status, path) for status, path in entries if not _allowed(path, prefixes)]
    if blocked:
        raise SystemExit(f"refusing to persist changed paths outside generated-artifact allowlist: {blocked}")
    if not entries:
        print(json.dumps({"status": "no_changes"}))
        return 0

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "football-persist-generated-worktree-v474",
    }

    for status, rel in entries:
        encoded = "/".join(urllib.parse.quote(part, safe="") for part in rel.split("/"))
        get_url = f"https://api.github.com/repos/{repo}/contents/{encoded}?ref={urllib.parse.quote(args.branch, safe='')}"
        write_url = f"https://api.github.com/repos/{repo}/contents/{encoded}"
        deleted = "D" in status
        local_path = ROOT / rel
        if not deleted and not local_path.is_file():
            raise SystemExit(f"changed generated path is not a regular file: {rel}")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            current_status, current = _request(headers, "GET", get_url)
            if deleted:
                if current_status == 404:
                    print(json.dumps({"status": "already_deleted", "path": rel}, ensure_ascii=False))
                    break
                payload = {
                    "message": f"{args.message_prefix} delete {rel}",
                    "sha": current["sha"],
                    "branch": args.branch,
                }
                result_status, detail = _request(headers, "DELETE", write_url, payload, allow_conflict=True)
            else:
                content = base64.b64encode(local_path.read_bytes()).decode("ascii")
                if current_status == 200 and current and current.get("content"):
                    remote = str(current["content"]).replace("\n", "")
                    if remote == content:
                        print(json.dumps({"status": "unchanged", "path": rel}, ensure_ascii=False))
                        break
                payload = {
                    "message": f"{args.message_prefix} {rel}",
                    "content": content,
                    "branch": args.branch,
                }
                if current_status == 200 and current:
                    payload["sha"] = current["sha"]
                result_status, detail = _request(headers, "PUT", write_url, payload, allow_conflict=True)

            if result_status in {200, 201}:
                print(json.dumps({"status": "persisted", "path": rel, "attempt": attempt}, ensure_ascii=False))
                break
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"failed to persist generated path {rel}: {result_status} {detail}")
            time.sleep(min(5, attempt))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
