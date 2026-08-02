#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
from typing import Any


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"object required: {path}")
    return value


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_manifest(artifact_dir: pathlib.Path) -> pathlib.Path:
    manifests = list(artifact_dir.rglob("manifest.json"))
    if len(manifests) != 1:
        raise ValueError(f"expected one manifest, found {len(manifests)}")
    return manifests[0]


def validate_artifact(artifact_dir: pathlib.Path, *, authorization_digest: str, frozen_code_head: str, spec_digest: str, identity_digest: str) -> pathlib.Path:
    manifest_path = locate_manifest(artifact_dir)
    root = manifest_path.parent
    manifest = read_json(manifest_path)
    expected = {
        "authorization_digest": authorization_digest,
        "frozen_code_head": frozen_code_head,
        "spec_digest": spec_digest,
        "identity_digest": identity_digest,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"artifact identity mismatch: {key}")
    files = manifest.get("files") or {}
    if "checkpoint.json" not in files:
        raise ValueError("artifact checkpoint missing from manifest")
    for relative, expected_sha in files.items():
        path = root / relative
        if not path.is_file() or sha256_file(path) != expected_sha:
            raise ValueError(f"artifact file hash mismatch: {relative}")
    checkpoint = read_json(root / "checkpoint.json")
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ValueError(f"checkpoint identity mismatch: {key}")
    return root


def restore_artifact(artifact_dir: pathlib.Path, state_dir: pathlib.Path, **expected: str) -> None:
    root = validate_artifact(artifact_dir, **expected)
    if state_dir.exists():
        shutil.rmtree(state_dir)
    shutil.copytree(root, state_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=pathlib.Path, required=True)
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--authorization-digest", required=True)
    parser.add_argument("--frozen-code-head", required=True)
    parser.add_argument("--spec-digest", required=True)
    parser.add_argument("--identity-digest", required=True)
    args = parser.parse_args()
    restore_artifact(
        args.artifact_dir,
        args.state_dir,
        authorization_digest=args.authorization_digest,
        frozen_code_head=args.frozen_code_head,
        spec_digest=args.spec_digest,
        identity_digest=args.identity_digest,
    )
    print(json.dumps({"status": "RESTORED_COMPATIBLE_ARTIFACT", "state_dir": str(args.state_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
