#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
SPEC_PATH = HERE / "draw_auto_research_spec_r1.json"
IDENTITY_PATH = HERE / "draw_auto_research_identity_r1.json"
AUTH_PATH = HERE / "draw_composite_run_authorization_r1.json"


def read_json(path: pathlib.Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def canonical_sha(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def git_blob_sha(path: pathlib.Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha1(f"blob {len(raw)}\0".encode("ascii") + raw).hexdigest()


def validate_payload(
    authorization: dict[str, Any],
    spec: dict[str, Any],
    identity: dict[str, Any],
    *,
    identity_blob_sha: str,
) -> dict[str, Any]:
    if authorization.get("schema_version") != "DRAW-AUTO-RESEARCH-AUTHORIZATION-R1.0":
        raise ValueError("authorization schema mismatch")
    if authorization.get("status") != "AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH":
        raise ValueError("authorization status mismatch")
    if authorization.get("user_authorization_record") != "rec0WJJzXiuDvAqSb":
        raise ValueError("authorization record mismatch")
    if authorization.get("data_status") != "VIEWED_DEVELOPMENT_DATA" or authorization.get("formal_weight") != 0:
        raise ValueError("authorization data/formal boundary mismatch")
    if len(str(authorization.get("frozen_code_head") or "")) != 40:
        raise ValueError("authorization frozen code HEAD missing")
    if int(authorization.get("maximum_candidates", 0)) != int(spec["budget"]["maximum_candidates"]):
        raise ValueError("authorization candidate budget mismatch")
    if int(authorization.get("maximum_cumulative_seconds", 0)) != int(spec["budget"]["maximum_cumulative_seconds"]):
        raise ValueError("authorization time budget mismatch")
    if authorization.get("spec_canonical_sha256") != canonical_sha(spec):
        raise ValueError("authorization spec digest mismatch")
    if authorization.get("identity_canonical_sha256") != canonical_sha(identity):
        raise ValueError("authorization identity digest mismatch")
    if authorization.get("identity_git_blob_sha") != identity_blob_sha:
        raise ValueError("authorization identity Git blob mismatch")
    required = list(identity.get("authorization_required_bindings") or [])
    files = identity.get("files") or {}
    bindings = authorization.get("bindings") or {}
    if not required or set(bindings) != set(required):
        raise ValueError("authorization binding set mismatch")
    for key in required:
        if key not in files or bindings.get(key) != files[key]:
            raise ValueError(f"authorization binding mismatch: {key}")
    return {
        "status": "PASS_AUTHORIZATION_BINDINGS_ZERO_LABEL",
        "user_authorization_record": authorization["user_authorization_record"],
        "frozen_code_head": authorization["frozen_code_head"],
        "binding_count": len(required),
        "authorization_digest": canonical_sha(authorization),
        "identity_git_blob_sha": identity_blob_sha,
        "rows_parsed": 0,
        "labels_parsed": 0,
        "training_runs": 0,
        "scoring_runs": 0,
        "provider_requests": 0,
        "api_football_requests": 0,
        "formal_weight": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    try:
        result = validate_payload(
            read_json(AUTH_PATH),
            read_json(SPEC_PATH),
            read_json(IDENTITY_PATH),
            identity_blob_sha=git_blob_sha(IDENTITY_PATH),
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        failure = {
            "status": "FAIL_CLOSED_AUTHORIZATION_BINDINGS",
            "error": str(exc),
            "rows_parsed": 0,
            "labels_parsed": 0,
            "training_runs": 0,
            "scoring_runs": 0,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
