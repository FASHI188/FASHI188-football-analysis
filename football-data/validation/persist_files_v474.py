#!/usr/bin/env python3
"""Safely persist generated football artifacts with the GitHub Contents API.

Designed for GitHub Actions. It avoids detached-HEAD commits, git pull/rebase races,
and direct git push from CI. Each file is updated with the current blob SHA and
bounded conflict retries. This is engineering infrastructure only and has no
ability to change CURRENT or model weights unless the caller explicitly supplies
such a tracked file path (workflows should never do that).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BRANCH = "main"
MAX_ATTEMPTS = 4


def _request(headers: dict[str, str], method: str, url: str, payload: dict[str, Any] | None = None, *, allow_conflict: bool = False):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            return 404, None
        if allow_conflict and exc.code in {409, 422}:
            return exc.code, body
        raise RuntimeError(f"GitHub API {method} {url} failed: {exc.code} {body}") from exc


def persist(path: Path, *, repo: str, token: str, branch: str, message: str) -> None:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"refusing to persist path outside repository: {path}")
    if not resolved.is_file():
        raise FileNotFoundError(path)
    rel = resolved.relative_to(ROOT).as_posix()
    if "CURRENT_唯一正式规则" in resolved.name:
        raise ValueError("formal CURRENT files must never be persisted from GitHub Actions")

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "football-persist-files-v474",
    }
    encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in rel.split("/"))
    get_url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}?ref={urllib.parse.quote(branch, safe='')}"
    put_url = f"https://api.github.com/repos/{repo}/contents/{encoded_path}"
    content = base64.b64encode(resolved.read_bytes()).decode("ascii")

    for attempt in range(1, MAX_ATTEMPTS + 1):
        status, current = _request(headers, "GET", get_url)
        payload: dict[str, Any] = {
            "message": message,
            "content": content,
            "branch": branch,
        }
        if status == 200 and current:
            payload["sha"] = current["sha"]
        put_status, detail = _request(headers, "PUT", put_url, payload, allow_conflict=True)
        if put_status in {200, 201}:
            print(json.dumps({"status": "persisted", "path": rel, "attempt": attempt}, ensure_ascii=False))
            return
        if attempt == MAX_ATTEMPTS:
            raise RuntimeError(f"failed to persist {rel} after {MAX_ATTEMPTS} attempts: {put_status} {detail}")
        time.sleep(min(5, attempt))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--message-prefix", default="automation(football): persist")
    args = parser.parse_args()

    repo = os.environ.get("GH_REPOSITORY") or os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        raise SystemExit("GH_REPOSITORY/GITHUB_REPOSITORY and GH_TOKEN/GITHUB_TOKEN are required")

    for item in args.files:
        path = Path(item)
        if not path.is_absolute():
            path = ROOT / path
        rel = path.resolve().relative_to(ROOT).as_posix()
        persist(
            path,
            repo=repo,
            token=token,
            branch=args.branch,
            message=f"{args.message_prefix} {rel}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
