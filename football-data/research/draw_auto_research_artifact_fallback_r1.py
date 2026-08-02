#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import stat
import tempfile
import urllib.error
import urllib.request
import zipfile
from typing import Any

from draw_auto_research_restore_r1 import ArtifactIncompatibleError, ArtifactIntegrityError, restore_artifact

API_ROOT = "https://api.github.com"


def _request_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "football-draw-auto-research-r1",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("GitHub API object required")
    return value


def list_matching_artifacts(repository: str, prefix: str, token: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    page = 1
    while True:
        value = _request_json(f"{API_ROOT}/repos/{repository}/actions/artifacts?per_page=100&page={page}", token)
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list):
            raise ValueError("GitHub artifacts list missing")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise ValueError("invalid artifact object")
            if not artifact.get("expired") and str(artifact.get("name") or "").startswith(prefix):
                matches.append(artifact)
        if len(artifacts) < 100:
            break
        page += 1
    matches.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return matches


def download_zip(url: str, destination: pathlib.Path, token: str) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "football-draw-auto-research-r1",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def safe_extract(zip_path: pathlib.Path, destination: pathlib.Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        seen: set[str] = set()
        for info in archive.infolist():
            raw = info.filename.replace("\\", "/")
            pure = pathlib.PurePosixPath(raw)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise ArtifactIntegrityError(f"unsafe ZIP path: {raw}")
            relative = pure.as_posix()
            if relative in seen:
                raise ArtifactIntegrityError(f"duplicate ZIP path: {relative}")
            seen.add(relative)
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise ArtifactIntegrityError(f"ZIP symbolic link prohibited: {relative}")
        archive.extractall(destination)


def restore_latest(
    *, repository: str, prefix: str, state_dir: pathlib.Path, token: str,
    authorization_digest: str, frozen_code_head: str, spec_digest: str, identity_digest: str,
) -> dict[str, Any]:
    try:
        artifacts = list_matching_artifacts(repository, prefix, token)
    except Exception as exc:
        raise RuntimeError(f"ARTIFACT_API_QUERY_FAILED: {exc}") from exc
    if not artifacts:
        return {"status": "NO_HISTORICAL_ARTIFACT_CONFIRMED", "matching_artifacts": 0}
    incompatible: list[int] = []
    for artifact in artifacts:
        artifact_id = int(artifact.get("id"))
        download_url = str(artifact.get("archive_download_url") or "")
        if not download_url:
            raise RuntimeError(f"ARTIFACT_DOWNLOAD_URL_MISSING: {artifact_id}")
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            zip_path = root / "artifact.zip"
            extracted = root / "extracted"
            extracted.mkdir()
            try:
                download_zip(download_url, zip_path, token)
                safe_extract(zip_path, extracted)
            except Exception as exc:
                raise RuntimeError(f"ARTIFACT_DOWNLOAD_OR_EXTRACT_FAILED:{artifact_id}:{exc}") from exc
            try:
                restore_artifact(
                    extracted,
                    state_dir,
                    authorization_digest=authorization_digest,
                    frozen_code_head=frozen_code_head,
                    spec_digest=spec_digest,
                    identity_digest=identity_digest,
                )
                return {"status": "RESTORED_COMPATIBLE_ARTIFACT", "artifact_id": artifact_id, "matching_artifacts": len(artifacts)}
            except ArtifactIncompatibleError:
                incompatible.append(artifact_id)
                continue
            except ArtifactIntegrityError:
                raise
    raise ArtifactIncompatibleError(f"MATCHING_ARTIFACTS_FOUND_BUT_NONE_COMPATIBLE:{incompatible}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--artifact-prefix", required=True)
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--authorization-digest", required=True)
    parser.add_argument("--frozen-code-head", required=True)
    parser.add_argument("--spec-digest", required=True)
    parser.add_argument("--identity-digest", required=True)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print(json.dumps({"status": "ARTIFACT_API_QUERY_FAILED", "error": "GITHUB_TOKEN missing"}))
        return 21
    try:
        result = restore_latest(
            repository=args.repository,
            prefix=args.artifact_prefix,
            state_dir=args.state_dir,
            token=token,
            authorization_digest=args.authorization_digest,
            frozen_code_head=args.frozen_code_head,
            spec_digest=args.spec_digest,
            identity_digest=args.identity_digest,
        )
        print(json.dumps(result))
        return 0
    except ArtifactIncompatibleError as exc:
        print(json.dumps({"status": "MATCHING_ARTIFACTS_FOUND_BUT_NONE_COMPATIBLE", "error": str(exc)}))
        return 10
    except ArtifactIntegrityError as exc:
        print(json.dumps({"status": "ARTIFACT_INTEGRITY_FAILURE", "error": str(exc)}))
        return 20
    except urllib.error.URLError as exc:
        print(json.dumps({"status": "ARTIFACT_API_QUERY_FAILED", "error": str(exc)}))
        return 21
    except RuntimeError as exc:
        status = "ARTIFACT_API_QUERY_FAILED" if str(exc).startswith("ARTIFACT_API_QUERY_FAILED") else "ARTIFACT_DOWNLOAD_OR_EXTRACT_FAILED"
        print(json.dumps({"status": status, "error": str(exc)}))
        return 21 if status == "ARTIFACT_API_QUERY_FAILED" else 22
    except Exception as exc:
        print(json.dumps({"status": "ARTIFACT_FALLBACK_FAILED", "error": str(exc)}))
        return 23


if __name__ == "__main__":
    raise SystemExit(main())
