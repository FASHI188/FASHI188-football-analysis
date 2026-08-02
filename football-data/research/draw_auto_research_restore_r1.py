#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
from typing import Any


class ArtifactIncompatibleError(ValueError):
    """Identity mismatch: caller may inspect an older matching artifact."""


class ArtifactIntegrityError(ValueError):
    """Integrity/security failure: caller must stop immediately."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactIntegrityError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: pathlib.Path, *, strict: bool = False) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    value = json.loads(text, object_pairs_hook=_reject_duplicate_keys if strict else None)
    if not isinstance(value, dict):
        raise ArtifactIntegrityError(f"object required: {path}")
    return value


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locate_manifest(artifact_dir: pathlib.Path) -> pathlib.Path:
    manifests = [path for path in artifact_dir.rglob("manifest.json") if path.is_file()]
    if len(manifests) != 1:
        raise ArtifactIntegrityError(f"expected one manifest, found {len(manifests)}")
    return manifests[0]


def _normalise_manifest_path(relative: str) -> str:
    if not isinstance(relative, str) or not relative:
        raise ArtifactIntegrityError("empty manifest path")
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or relative.startswith(("/", "\\")):
        raise ArtifactIntegrityError(f"absolute manifest path: {relative}")
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise ArtifactIntegrityError(f"unsafe manifest path: {relative}")
    normalised = pure.as_posix()
    if normalised != relative.replace("\\", "/"):
        raise ArtifactIntegrityError(f"non-canonical manifest path: {relative}")
    return normalised


def _actual_file_set(root: pathlib.Path) -> set[str]:
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ArtifactIntegrityError(f"symbolic link prohibited: {path.relative_to(root).as_posix()}")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    return actual


def _validate_checkpoint_references(root: pathlib.Path, files: dict[str, str], checkpoint: dict[str, Any]) -> None:
    completed = checkpoint.get("completed_candidates") or []
    if not isinstance(completed, list):
        raise ArtifactIntegrityError("completed_candidates must be a list")
    for candidate_id in completed:
        relative = f"results/{candidate_id}.json"
        if relative not in files:
            raise ArtifactIntegrityError(f"checkpoint result not registered: {relative}")
    failed = checkpoint.get("failed_candidates") or []
    duplicates = checkpoint.get("duplicate_prediction_candidates") or []
    if completed or failed or duplicates or "run_failure_receipt.json" in files:
        if "ledger.jsonl" not in files:
            raise ArtifactIntegrityError("checkpoint activity requires registered ledger.jsonl")
    batch_index = int(checkpoint.get("batch_index") or 0)
    for index in range(1, batch_index + 1):
        relative = f"summaries/batch_{index:02d}.md"
        if relative not in files:
            raise ArtifactIntegrityError(f"checkpoint summary not registered: {relative}")
    status = str(checkpoint.get("status") or "")
    if status in {"FAILED_RUNTIME", "FAILED_SAFETY"} and "run_failure_receipt.json" not in files:
        raise ArtifactIntegrityError("terminal checkpoint missing registered run_failure_receipt.json")
    if status != "RUNNING" and "final_report.md" not in files:
        raise ArtifactIntegrityError("terminal checkpoint missing registered final_report.md")
    if "top5.json" in files:
        top5 = read_json(root / "top5.json")
        if not isinstance(top5.get("top5"), list):
            raise ArtifactIntegrityError("invalid top5.json")


def validate_state_root(root: pathlib.Path, *, authorization_digest: str, frozen_code_head: str, spec_digest: str, identity_digest: str) -> pathlib.Path:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise ArtifactIntegrityError("manifest.json missing")
    manifest = read_json(manifest_path, strict=True)
    expected = {
        "authorization_digest": authorization_digest,
        "frozen_code_head": frozen_code_head,
        "spec_digest": spec_digest,
        "identity_digest": identity_digest,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ArtifactIncompatibleError(f"artifact identity mismatch: {key}")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise ArtifactIntegrityError("manifest files object missing")
    files: dict[str, str] = {}
    for raw_relative, expected_sha in raw_files.items():
        relative = _normalise_manifest_path(raw_relative)
        if relative == "manifest.json":
            raise ArtifactIntegrityError("manifest must not self-register")
        if relative in files:
            raise ArtifactIntegrityError(f"duplicate manifest path: {relative}")
        if not isinstance(expected_sha, str) or len(expected_sha) != 64:
            raise ArtifactIntegrityError(f"invalid SHA-256 registration: {relative}")
        files[relative] = expected_sha
    expected_actual = set(files) | {"manifest.json"}
    actual = _actual_file_set(root)
    if actual != expected_actual:
        missing = sorted(expected_actual - actual)
        extra = sorted(actual - expected_actual)
        raise ArtifactIntegrityError(f"artifact file universe mismatch; missing={missing}; extra={extra}")
    for relative, expected_sha in files.items():
        path = root / pathlib.PurePosixPath(relative)
        if not path.is_file() or path.is_symlink():
            raise ArtifactIntegrityError(f"artifact file invalid: {relative}")
        if sha256_file(path) != expected_sha:
            raise ArtifactIntegrityError(f"artifact file hash mismatch: {relative}")
    if "checkpoint.json" not in files:
        raise ArtifactIntegrityError("artifact checkpoint missing from manifest")
    checkpoint = read_json(root / "checkpoint.json", strict=True)
    for key, value in expected.items():
        if checkpoint.get(key) != value:
            raise ArtifactIntegrityError(f"checkpoint identity mismatch: {key}")
    _validate_checkpoint_references(root, files, checkpoint)
    return root


def validate_artifact(artifact_dir: pathlib.Path, *, authorization_digest: str, frozen_code_head: str, spec_digest: str, identity_digest: str) -> pathlib.Path:
    manifest_path = locate_manifest(artifact_dir)
    return validate_state_root(
        manifest_path.parent,
        authorization_digest=authorization_digest,
        frozen_code_head=frozen_code_head,
        spec_digest=spec_digest,
        identity_digest=identity_digest,
    )


def restore_artifact(artifact_dir: pathlib.Path, state_dir: pathlib.Path, **expected: str) -> None:
    root = validate_artifact(artifact_dir, **expected)
    if state_dir.exists():
        shutil.rmtree(state_dir)
    shutil.copytree(root, state_dir, symlinks=False)


def validate_existing_state(state_dir: pathlib.Path, **expected: str) -> None:
    validate_state_root(state_dir, **expected)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", type=pathlib.Path)
    parser.add_argument("--state-dir", type=pathlib.Path, required=True)
    parser.add_argument("--validate-existing", action="store_true")
    parser.add_argument("--authorization-digest", required=True)
    parser.add_argument("--frozen-code-head", required=True)
    parser.add_argument("--spec-digest", required=True)
    parser.add_argument("--identity-digest", required=True)
    args = parser.parse_args()
    expected = {
        "authorization_digest": args.authorization_digest,
        "frozen_code_head": args.frozen_code_head,
        "spec_digest": args.spec_digest,
        "identity_digest": args.identity_digest,
    }
    try:
        if args.validate_existing:
            validate_existing_state(args.state_dir, **expected)
            status = "VALID_EXISTING_STATE"
        else:
            if args.artifact_dir is None:
                raise ArtifactIntegrityError("--artifact-dir required for restore")
            restore_artifact(args.artifact_dir, args.state_dir, **expected)
            status = "RESTORED_COMPATIBLE_ARTIFACT"
        print(json.dumps({"status": status, "state_dir": str(args.state_dir)}))
        return 0
    except ArtifactIncompatibleError as exc:
        print(json.dumps({"status": "INCOMPATIBLE_ARTIFACT", "error": str(exc)}))
        return 10
    except Exception as exc:
        print(json.dumps({"status": "ARTIFACT_INTEGRITY_FAILURE", "error": str(exc)}))
        return 20


if __name__ == "__main__":
    raise SystemExit(main())
