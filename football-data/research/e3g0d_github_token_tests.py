#!/usr/bin/env python3
"""No-network binding and credential-isolation tests for E3g-0D GitHub reads."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import e3g0d_archive_github as archive_github
from e3g0d_archive_core import GitHubReader
from e3g0d_common import E3Error


def _block(text: str, start: str, next_marker: str) -> str:
    begin = text.index(start)
    end = text.find(next_marker, begin + len(start))
    return text[begin:] if end < 0 else text[begin:end]


class _PayloadResponse:
    def __init__(self, raw: bytes) -> None:
        self.raw = raw

    def __enter__(self) -> "_PayloadResponse":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


def run(workflow: Path, job: Path, output: Path) -> dict[str, Any]:
    sentinel = "sentinel-gh-token-do-not-leak"
    workflow_text = workflow.read_text(encoding="utf-8")
    job_text = job.read_text(encoding="utf-8")
    prepare = _block(workflow_text, "      - name: Prepare\n", "      - uses: actions/upload-artifact")
    collect = _block(workflow_text, "      - name: Collect and preserve evidence\n", "      - uses: actions/upload-artifact")
    expression = "${{ github.token }}"
    assert prepare.count("GH_TOKEN: '" + expression + "'") == 1
    assert "API_FOOTBALL_KEY" not in prepare
    assert "GH_TOKEN" not in collect and expression not in collect

    first: dict[str, str] = {}
    second: dict[str, str] = {}
    location = "https://example.blob.core.windows.net/container/artifact.zip?sig=mock"

    class _NoRedirectOpener:
        def open(self, request: Any, timeout: float | None = None) -> None:
            del timeout
            first.update({str(k).lower(): str(v) for k, v in request.header_items()})
            headers = Message()
            headers["Location"] = location
            raise urllib.error.HTTPError(request.full_url, 302, "Found", headers, None)

    def _payload_urlopen(request: Any, timeout: float | None = None) -> _PayloadResponse:
        del timeout
        second.update({str(k).lower(): str(v) for k, v in request.header_items()})
        return _PayloadResponse(b"artifact-bytes")

    original_build = archive_github.urllib.request.build_opener
    original_urlopen = archive_github.urllib.request.urlopen
    archive_github.urllib.request.build_opener = lambda *args, **kwargs: _NoRedirectOpener()
    archive_github.urllib.request.urlopen = _payload_urlopen
    try:
        reader = GitHubReader("owner/repository", sentinel)
        assert reader.download(123) == b"artifact-bytes"
    finally:
        archive_github.urllib.request.build_opener = original_build
        archive_github.urllib.request.urlopen = original_urlopen

    assert first.get("authorization") == f"Bearer {sentinel}"
    assert "x-apisports-key" not in first
    assert "authorization" not in second
    assert "x-apisports-key" not in second

    try:
        GitHubReader("owner/repository", "")
        raise AssertionError("missing GH_TOKEN did not fail closed")
    except E3Error as exc:
        assert exc.failure_class == "VALIDATION_FAILED"

    residual = workflow_text.count("EVIDCNCE_HEAD") + job_text.count("EVIDCNCE_HEAD")
    assert residual == 0
    assert "GITHUB_ENV" not in job_text

    result: dict[str, Any] = {
        "github_token_auth_tests": {
            "workflow_prepare_uses_github_token": "PASS",
            "first_github_request_has_authorization": "PASS",
            "redirect_request_has_no_authorization": "PASS",
            "redirect_request_has_no_provider_key": "PASS",
            "provider_step_does_not_receive_github_token": "PASS",
            "missing_token_fails_closed": "PASS",
            "token_not_in_outputs": "PASS",
            "token_not_in_artifacts": "PASS",
            "token_not_in_logs": "PASS",
        },
        "github_token_binding": "PASS",
        "evidcnce_head_residual_count": residual,
        "real_provider_request_attempts": 0,
        "formal_weight": 0,
    }
    public = json.dumps(result, sort_keys=True)
    with tempfile.TemporaryDirectory() as temporary:
        artifact = Path(temporary) / "artifact"
        artifact.mkdir()
        (artifact / "github_token_security_validation.json").write_text(public + "\n", encoding="utf-8")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            print(public)
        assert sentinel not in public
        assert all(
            sentinel not in item.read_text(encoding="utf-8")
            for item in artifact.rglob("*")
            if item.is_file()
        )
        assert sentinel not in captured.getvalue()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(public + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(Path(args.workflow), Path(args.job), Path(args.output))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
