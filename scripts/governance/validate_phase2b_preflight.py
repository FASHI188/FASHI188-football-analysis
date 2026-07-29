#!/usr/bin/env python3
"""Deterministic Phase-2B-0 preflight ledger validator.

This validator is intentionally local-Git authoritative. It does not call GitHub
APIs or Actions. It reconstructs the frozen-main drift directly from Git objects.
Any mismatch, missing revision, malformed JSON, or unavailable Git checkout exits
non-zero.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE = "c9311d3c33d37f0ff52774ecfc4a7816209e3a2a"
HEAD = "6beea539280293c8e414a3c2eaa2f0f8dd558942"
EXPECTED_COMMIT_COUNT = 161
EXPECTED_FILE_COUNT = 161

ROOT = Path(__file__).resolve().parents[2]
COMMIT_LEDGER = ROOT / "governance" / "main_drift_commit_ledger.json"
FILE_LEDGER = ROOT / "governance" / "main_drift_file_ledger.json"


class ValidationError(RuntimeError):
    pass


def run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise ValidationError(
            f"git {' '.join(args)} failed rc={proc.returncode}: {proc.stderr.strip()}"
        )
    return proc.stdout


def canonical_time(value: str) -> str:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValidationError(f"timestamp lacks timezone: {value!r}")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def classify_path(path: str) -> dict[str, Any]:
    modifies_workflow = path.startswith(".github/workflows/")
    modifies_formal_current = "CURRENT_唯一正式规则" in path
    modifies_config = path.startswith("football-data/config/")
    modifies_model_code = path.endswith(".py") and (
        path.startswith("football-data/engine/")
        or path.startswith("football-data/validation/")
    )
    generated_prefixes = (
        "football-data/evidence/",
        "football-data/forward/",
        "football-data/manifests/",
        "football-data/cache/",
    )
    only_generated = path.startswith(generated_prefixes)

    if modifies_formal_current:
        classification = "FORMAL_CURRENT_CONFLICT"
        recommendation = "PRESERVE_AND_BLOCK_REVIEW"
        blocker = "formal CURRENT changed after frozen main"
    elif modifies_workflow:
        classification = "WORKFLOW_CHANGE_REVIEW"
        recommendation = "PRESERVE_IN_PLACE_REVIEW_WORKFLOW_DO_NOT_AUTO_MERGE"
        blocker = "post-freeze workflow mutation requires explicit review"
    elif modifies_model_code:
        classification = "MODEL_CODE_CHANGE_REVIEW"
        recommendation = "PRESERVE_IN_PLACE_REVIEW_MODEL_CODE_DO_NOT_AUTO_MERGE"
        blocker = "post-freeze model-code mutation requires explicit review"
    elif modifies_config:
        classification = "CONFIG_OR_RUNTIME_CHANGE_REVIEW"
        recommendation = "PRESERVE_IN_PLACE_REVIEW_RUNTIME_CONFIG_DO_NOT_AUTO_MERGE"
        blocker = (
            "post-freeze runtime/config mutation requires explicit review; "
            "no formal CURRENT or model-code change detected"
        )
    elif only_generated:
        classification = "GENERATED_EVIDENCE_PRESERVE"
        recommendation = "PRESERVE_GENERATED_EVIDENCE_DO_NOT_AUTO_MERGE"
        blocker = None
    else:
        classification = "UNKNOWN"
        recommendation = "PRESERVE_PENDING_REVIEW"
        blocker = "path does not match a deterministic governance class"

    return {
        "modifies_workflow": modifies_workflow,
        "modifies_formal_current": modifies_formal_current,
        "modifies_config": modifies_config,
        "modifies_model_code": modifies_model_code,
        "only_evidence_forward_manifest_cache": only_generated,
        "classification": classification,
        "preserve_recommendation": recommendation,
        "blocking_reason": blocker,
    }


def classify_commit(paths: list[str]) -> dict[str, Any]:
    if not paths:
        raise ValidationError("drift commit has no changed files")
    per_file = [classify_path(path) for path in paths]
    # Deterministic priority matches the preflight governance hard-gate order.
    priorities = (
        "FORMAL_CURRENT_CONFLICT",
        "WORKFLOW_CHANGE_REVIEW",
        "MODEL_CODE_CHANGE_REVIEW",
        "CONFIG_OR_RUNTIME_CHANGE_REVIEW",
        "GENERATED_EVIDENCE_PRESERVE",
        "UNKNOWN",
    )
    classes = {item["classification"] for item in per_file}
    classification = next((name for name in priorities if name in classes), "UNKNOWN")

    if classification == "FORMAL_CURRENT_CONFLICT":
        recommendation = "PRESERVE_AND_BLOCK_REVIEW"
        blocker = "formal CURRENT changed after frozen main"
    elif classification == "WORKFLOW_CHANGE_REVIEW":
        recommendation = "PRESERVE_IN_PLACE_REVIEW_WORKFLOW_DO_NOT_AUTO_MERGE"
        blocker = "post-freeze workflow mutation requires explicit review"
    elif classification == "MODEL_CODE_CHANGE_REVIEW":
        recommendation = "PRESERVE_IN_PLACE_REVIEW_MODEL_CODE_DO_NOT_AUTO_MERGE"
        blocker = "post-freeze model-code mutation requires explicit review"
    elif classification == "CONFIG_OR_RUNTIME_CHANGE_REVIEW":
        recommendation = "PRESERVE_IN_PLACE_REVIEW_RUNTIME_CONFIG_DO_NOT_AUTO_MERGE"
        blocker = (
            "post-freeze runtime/config mutation requires explicit review; "
            "no formal CURRENT or model-code change detected"
        )
    elif classification == "GENERATED_EVIDENCE_PRESERVE":
        recommendation = "PRESERVE_GENERATED_EVIDENCE_DO_NOT_AUTO_MERGE"
        blocker = None
    else:
        recommendation = "PRESERVE_PENDING_REVIEW"
        blocker = "commit contains a path without a deterministic governance class"

    return {
        "modifies_workflow": any(x["modifies_workflow"] for x in per_file),
        "modifies_formal_current": any(x["modifies_formal_current"] for x in per_file),
        "modifies_config": any(x["modifies_config"] for x in per_file),
        "modifies_model_code": any(x["modifies_model_code"] for x in per_file),
        "only_evidence_forward_manifest_cache": all(
            x["only_evidence_forward_manifest_cache"] for x in per_file
        ),
        "classification": classification,
        "preserve_recommendation": recommendation,
        "blocking_reason": blocker,
    }


def path_class(path: str) -> str:
    if path.startswith("football-data/config/"):
        return "CONFIG"
    if path.startswith("football-data/forward/"):
        return "FORWARD"
    if path.startswith("football-data/manifests/"):
        return "MANIFEST"
    if path.startswith("football-data/evidence/"):
        return "EVIDENCE"
    if path.startswith("football-data/cache/"):
        return "CACHE"
    return "OTHER"


def git_metadata(sha: str) -> dict[str, str]:
    raw = run_git("show", "-s", "--format=%H%x00%an%x00%cn%x00%aI%x00%cI%x00%B", sha)
    parts = raw.split("\x00", 5)
    if len(parts) != 6:
        raise ValidationError(f"unexpected git show metadata format for {sha}")
    got_sha, author, committer, authored_at, committed_at, message = parts
    return {
        "commit_sha": got_sha.strip(),
        "author": author,
        "committer": committer,
        "authored_at": canonical_time(authored_at),
        "committed_at": canonical_time(committed_at),
        "message": message.rstrip("\n"),
    }


def changed_files(sha: str) -> list[str]:
    out = run_git("diff-tree", "--no-commit-id", "--name-only", "-r", sha)
    return [line for line in out.splitlines() if line]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValidationError(f"JSON parse failed: {path.relative_to(ROOT)}: {exc}") from exc


def validate_all_governance_json() -> int:
    count = 0
    for path in sorted((ROOT / "governance").glob("*.json")):
        load_json(path)
        count += 1
    return count


def assert_equal(label: str, actual: Any, expected: Any) -> None:
    if actual != expected:
        raise ValidationError(f"{label} mismatch: actual={actual!r} expected={expected!r}")


def main() -> int:
    try:
        # Hard prerequisite: real Git objects must be available locally.
        run_git("rev-parse", "--verify", f"{BASE}^{{commit}}")
        run_git("rev-parse", "--verify", f"{HEAD}^{{commit}}")

        commit_doc = load_json(COMMIT_LEDGER)
        file_doc = load_json(FILE_LEDGER)
        parsed_json_count = validate_all_governance_json()

        commit_records = commit_doc.get("records")
        file_records = file_doc.get("records")
        if not isinstance(commit_records, list):
            raise ValidationError("commit ledger records is not an array")
        if not isinstance(file_records, list):
            raise ValidationError("file ledger records is not an array")

        git_shas = [
            line
            for line in run_git("rev-list", "--reverse", f"{BASE}..{HEAD}").splitlines()
            if line
        ]
        assert_equal("Git drift commit count", len(git_shas), EXPECTED_COMMIT_COUNT)
        assert_equal("commit ledger record count", len(commit_records), EXPECTED_COMMIT_COUNT)

        ledger_shas = [rec.get("commit_sha") for rec in commit_records]
        assert_equal("commit SHA order", ledger_shas, git_shas)
        assert_equal("commit SHA set", set(ledger_shas), set(git_shas))

        expected_file_records: list[dict[str, Any]] = []
        reconstructed_classes: Counter[str] = Counter()

        for index, (sha, rec) in enumerate(zip(git_shas, commit_records, strict=True)):
            meta = git_metadata(sha)
            paths = changed_files(sha)
            governance = classify_commit(paths)

            for key in ("commit_sha", "author", "committer", "message"):
                assert_equal(f"commit[{index}].{key}", rec.get(key), meta[key])
            assert_equal(
                f"commit[{index}].authored_at",
                canonical_time(str(rec.get("authored_at"))),
                meta["authored_at"],
            )
            assert_equal(
                f"commit[{index}].committed_at",
                canonical_time(str(rec.get("committed_at"))),
                meta["committed_at"],
            )
            assert_equal(f"commit[{index}].changed_files", rec.get("changed_files"), paths)
            assert_equal(
                f"commit[{index}].is_github_actions_bot",
                rec.get("is_github_actions_bot"),
                meta["author"] == "github-actions[bot]",
            )
            for key, value in governance.items():
                assert_equal(f"commit[{index}].{key}", rec.get(key), value)

            reconstructed_classes[governance["classification"]] += 1

            for path in paths:
                file_governance = classify_path(path)
                expected_file_records.append(
                    {
                        "commit_sha": sha,
                        "path": path,
                        "path_class": path_class(path),
                        "classification": file_governance["classification"],
                    }
                )

        assert_equal("file ledger record count", len(file_records), EXPECTED_FILE_COUNT)
        assert_equal("Git-derived changed-file count", len(expected_file_records), EXPECTED_FILE_COUNT)
        assert_equal("file ledger records", file_records, expected_file_records)

        # One-to-one join is required by this frozen drift: one file per commit.
        file_shas = [rec.get("commit_sha") for rec in file_records]
        assert_equal("commit/file ledger SHA order", file_shas, git_shas)
        assert_equal("commit/file ledger SHA set", set(file_shas), set(git_shas))

        stored_commit_counts = Counter(rec.get("classification") for rec in commit_records)
        stored_file_counts = Counter(rec.get("classification") for rec in file_records)
        assert_equal("commit classification recompute", stored_commit_counts, reconstructed_classes)
        assert_equal(
            "commit summary classification_counts",
            commit_doc.get("classification_counts"),
            dict(sorted(stored_commit_counts.items())),
        )
        assert_equal(
            "file summary classification_counts",
            file_doc.get("classification_counts"),
            dict(sorted(stored_file_counts.items())),
        )
        assert_equal(
            "file summary path_class_counts",
            file_doc.get("path_class_counts"),
            dict(sorted(Counter(rec.get("path_class") for rec in file_records).items())),
        )

        unknown_count = stored_commit_counts.get("UNKNOWN", 0) + stored_file_counts.get("UNKNOWN", 0)
        assert_equal("combined UNKNOWN count", unknown_count, 0)
        assert_equal("commit summary unknown_count", commit_doc.get("unknown_count"), 0)
        assert_equal("file summary unknown_count", file_doc.get("unknown_count"), 0)

        result = {
            "status": "PASS",
            "range": f"{BASE}..{HEAD}",
            "commit_count": len(git_shas),
            "file_count": len(expected_file_records),
            "committer_values": sorted({rec["committer"] for rec in commit_records}),
            "classification_counts": dict(sorted(stored_commit_counts.items())),
            "unknown_count": 0,
            "governance_json_files_parsed": parsed_json_count,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "range": f"{BASE}..{HEAD}",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
