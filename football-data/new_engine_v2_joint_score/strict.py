from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GovernanceError(RuntimeError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise GovernanceError(f"non-canonical JSON payload: {exc}") from exc


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_hex64(value: Any, field: str) -> str:
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise GovernanceError(f"{field} must be lowercase 64-hex")
    return value


def strict_nonnegative_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise GovernanceError(f"{field} must be a strict int, got {type(value).__name__}")
    if value < 0:
        raise GovernanceError(f"{field} must be >=0")
    return value


def strict_goal_text(value: Any, field: str) -> int:
    if not isinstance(value, str):
        raise GovernanceError(f"{field} raw CSV value must be string")
    token = value.strip()
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)", token):
        raise GovernanceError(f"{field} must be canonical nonnegative integer text, got {value!r}")
    return strict_nonnegative_int(int(token), field)


def finite_number(value: Any, field: str, *, lo: float | None = None, hi: float | None = None,
                  lo_open: bool = False, hi_open: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GovernanceError(f"{field} must be a real int/float, not {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise GovernanceError(f"{field} must be finite")
    if lo is not None and (number <= lo if lo_open else number < lo):
        op = ">" if lo_open else ">="
        raise GovernanceError(f"{field} must be {op} {lo}")
    if hi is not None and (number >= hi if hi_open else number > hi):
        op = "<" if hi_open else "<="
        raise GovernanceError(f"{field} must be {op} {hi}")
    return number


def parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GovernanceError(f"{field} must be a non-empty ISO string")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise GovernanceError(f"{field} invalid ISO datetime: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GovernanceError(f"{field} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def exact_keys(mapping: Any, field: str, required: set[str], optional: set[str] | None = None) -> Mapping[str, Any]:
    if not isinstance(mapping, dict):
        raise GovernanceError(f"{field} must be object")
    optional = optional or set()
    keys = set(mapping)
    missing = required - keys
    extra = keys - required - optional
    if missing or extra:
        raise GovernanceError(f"{field} schema mismatch missing={sorted(missing)} extra={sorted(extra)}")
    return mapping


def validate_probability_vector(mapping: Any, field: str, keys: tuple[str, ...] = ("home", "draw", "away")) -> dict[str, float]:
    exact_keys(mapping, field, set(keys))
    out = {k: finite_number(mapping[k], f"{field}.{k}", lo=0.0, hi=1.0) for k in keys}
    total = sum(out.values())
    if abs(total - 1.0) > 1e-9:
        raise GovernanceError(f"{field} sum={total} != 1")
    return out


def validate_forward_row_schema(row: Any) -> dict[str, Any]:
    top = exact_keys(
        row,
        "forward_row",
        {
            "schema_version", "fixture_id", "provider_event_id", "competition_id", "season",
            "canonical_home", "canonical_away", "kickoff_utc", "observed_at_utc", "prediction_cutoff",
            "source", "model", "prediction", "labels_present", "outcomes_read",
            "previous_row_hash", "row_hash",
        },
    )
    exact_keys(
        top["source"],
        "forward_row.source",
        {"provider", "capture_sha256", "manifest_sha256", "observed_at_utc"},
    )
    exact_keys(
        top["model"],
        "forward_row.model",
        {"candidate_head", "engine_sha256", "config_sha256"},
    )
    pred = exact_keys(
        top["prediction"],
        "forward_row.prediction",
        {"matrix", "one_x_two", "uncertainty", "cold_start_bucket"},
    )
    validate_probability_vector(pred["one_x_two"], "forward_row.prediction.one_x_two")
    matrix = pred["matrix"]
    if not isinstance(matrix, list) or not matrix:
        raise GovernanceError("forward_row.prediction.matrix must be non-empty list")
    for i, cell in enumerate(matrix):
        exact_keys(cell, f"matrix[{i}]", {"home_goals", "away_goals", "probability"})
        strict_nonnegative_int(cell["home_goals"], f"matrix[{i}].home_goals")
        strict_nonnegative_int(cell["away_goals"], f"matrix[{i}].away_goals")
        finite_number(cell["probability"], f"matrix[{i}].probability", lo=0.0, hi=1.0)
    if top["labels_present"] is not False or top["outcomes_read"] is not False:
        raise GovernanceError("forward ledger must remain zero-label")
    parse_utc(top["kickoff_utc"], "forward_row.kickoff_utc")
    parse_utc(top["observed_at_utc"], "forward_row.observed_at_utc")
    require_hex64(top["previous_row_hash"], "forward_row.previous_row_hash")
    require_hex64(top["row_hash"], "forward_row.row_hash")
    return dict(top)


def compute_forward_row_hash(row: Mapping[str, Any]) -> str:
    payload = dict(row)
    payload.pop("row_hash", None)
    return sha256_bytes(canonical_json_bytes(payload))


def verify_hash_chain(rows: list[dict[str, Any]], genesis_hash: str) -> str:
    previous = require_hex64(genesis_hash, "genesis_hash")
    for idx, row in enumerate(rows):
        validated = validate_forward_row_schema(row)
        if validated["previous_row_hash"] != previous:
            raise GovernanceError(f"hash-chain previous mismatch at row {idx}")
        actual = compute_forward_row_hash(validated)
        if validated["row_hash"] != actual:
            raise GovernanceError(f"row_hash mismatch at row {idx}")
        previous = actual
    return previous
