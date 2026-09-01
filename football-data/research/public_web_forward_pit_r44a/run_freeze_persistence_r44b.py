#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import urllib.error
import urllib.request
from pathlib import Path


def load_auditor():
    path = Path(__file__).with_name("audit_freeze_persistence_r44b.py")
    spec = importlib.util.spec_from_file_location("r44b_auditor", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("R44B_AUDITOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def safe_api_bytes(module, url: str, token: str | None) -> bytes:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": module.API_VERSION}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(req, timeout=60) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers.get("Location")
        if not location:
            raise RuntimeError("ARTIFACT_DOWNLOAD_REDIRECT_LOCATION_MISSING")
    signed_req = urllib.request.Request(location, headers={"Accept": "application/zip"})
    with urllib.request.urlopen(signed_req, timeout=60) as response:
        return response.read()


def frozen_head_artifacts(module, original_list_artifacts, repository: str, branch: str | None, token: str | None):
    checked_out_head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip().lower()
    rows = original_list_artifacts(repository, branch, token)
    return [
        row for row in rows
        if str((row.get("workflow_run") or {}).get("head_sha") or "").strip().lower() == checked_out_head
    ]


def main() -> int:
    module = load_auditor()
    module.api_bytes = lambda url, token: safe_api_bytes(module, url, token)
    original_list_artifacts = module.list_artifacts
    module.list_artifacts = lambda repository, branch, token: frozen_head_artifacts(
        module, original_list_artifacts, repository, branch, token
    )
    return int(module.main())


if __name__ == "__main__":
    raise SystemExit(main())
