#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

FOOTBALL3_GOVERNED_PRODUCTION_TRANSPORT = "football3-formal-gpt-request-transport-v1"

HERE = Path(__file__).resolve().parent
RUNTIME_DIR = HERE.parent / "formal_fast_runtime_v1"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

import runtime as rt

SCHEMA = "football3-formal-gpt-gateway-v1"
FORMAL_PREDICTION_MODES = frozenset(
    {
        "predict",
        "PROSPECTIVE_FORMAL_PREDICTION",
        "ACTIVE_AT_CUTOFF_REPLAY",
        "CURRENT_MODEL_RETROSPECTIVE_REPLAY",
    }
)
INTERNAL_SELFTEST_MODES = frozenset(
    {"bootstrap_selftest", "cache_reuse_probe", "missing_data_probe"}
)
ALLOWED_MODES = FORMAL_PREDICTION_MODES | INTERNAL_SELFTEST_MODES
REQUIRED_MATCH_KEYS = frozenset(
    {
        "competition_id",
        "season",
        "home_team_name",
        "away_team_name",
        "kickoff",
        "cutoff",
    }
)
REQUIRED_TOP_LEVEL_KEYS = frozenset({"schema_version", "mode", "request_id"})


class FormalRequestContractError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise FormalRequestContractError(code)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def request_sha256(request: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(request)).hexdigest()


def _nonempty_string(value: Any, code: str, *, max_len: int = 512) -> str:
    if not isinstance(value, str):
        _fail(code)
    value = value.strip()
    if not value or len(value) > max_len:
        _fail(code)
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        _fail(code)
    return value


def _datetime(value: Any, code: str):
    if not isinstance(value, str):
        _fail(code)
    try:
        return rt._parse_dt(value, code)
    except Exception as exc:
        raise FormalRequestContractError(code) from exc


def supported_competitions() -> tuple[str, ...]:
    scope = tuple(str(x) for x in rt.FORMAL_SCOPE)
    if not scope or len(scope) != len(set(scope)):
        _fail("FORMAL_REQUEST_SUPPORTED_SCOPE_INVALID")
    return scope


def validate_request(value: Any, *, carrier_request: bool) -> dict[str, Any]:
    if type(value) is not dict:
        _fail("FORMAL_REQUEST_ROOT_INVALID")
    missing = REQUIRED_TOP_LEVEL_KEYS.difference(value)
    if missing:
        _fail("FORMAL_REQUEST_TOP_LEVEL_REQUIRED_FIELD_MISSING")
    if value.get("schema_version") != SCHEMA:
        _fail("FORMAL_REQUEST_SCHEMA_VERSION_INVALID")

    mode = _nonempty_string(value.get("mode"), "FORMAL_REQUEST_MODE_INVALID", max_len=128)
    allowed = FORMAL_PREDICTION_MODES if carrier_request else ALLOWED_MODES
    if mode not in allowed:
        _fail("FORMAL_REQUEST_MODE_INVALID")

    request_id = _nonempty_string(
        value.get("request_id"), "FORMAL_REQUEST_ID_INVALID", max_len=256
    )

    requires_match = carrier_request or mode in FORMAL_PREDICTION_MODES or mode == "missing_data_probe"
    if not requires_match:
        result = dict(value)
        result["schema_version"] = SCHEMA
        result["mode"] = mode
        result["request_id"] = request_id
        if "match" in result and result["match"] is not None:
            _fail("FORMAL_REQUEST_MATCH_UNEXPECTED")
        return result

    match = value.get("match")
    if type(match) is not dict:
        _fail("FORMAL_REQUEST_MATCH_INVALID")
    if REQUIRED_MATCH_KEYS.difference(match):
        _fail("FORMAL_REQUEST_MATCH_REQUIRED_FIELD_MISSING")

    competition_id = _nonempty_string(
        match.get("competition_id"), "FORMAL_REQUEST_COMPETITION_ID_INVALID", max_len=128
    )
    if competition_id not in set(supported_competitions()):
        _fail("FORMAL_REQUEST_COMPETITION_ID_INVALID")
    season = _nonempty_string(match.get("season"), "FORMAL_REQUEST_SEASON_INVALID", max_len=32)
    home = _nonempty_string(
        match.get("home_team_name"), "FORMAL_REQUEST_HOME_TEAM_INVALID", max_len=256
    )
    away = _nonempty_string(
        match.get("away_team_name"), "FORMAL_REQUEST_AWAY_TEAM_INVALID", max_len=256
    )
    try:
        home_token = rt._normalize_team(home)
        away_token = rt._normalize_team(away)
    except Exception as exc:
        raise FormalRequestContractError("FORMAL_REQUEST_TEAM_IDENTITY_INVALID") from exc
    if not home_token or not away_token:
        _fail("FORMAL_REQUEST_TEAM_IDENTITY_INVALID")
    if home_token == away_token:
        _fail("FORMAL_REQUEST_TEAMS_IDENTICAL")

    kickoff = _datetime(match.get("kickoff"), "FORMAL_REQUEST_KICKOFF_INVALID")
    cutoff = _datetime(match.get("cutoff"), "FORMAL_REQUEST_CUTOFF_INVALID")
    if cutoff >= kickoff:
        _fail("FORMAL_REQUEST_CUTOFF_NOT_BEFORE_KICKOFF")

    result = dict(value)
    result["schema_version"] = SCHEMA
    result["mode"] = mode
    result["request_id"] = request_id
    canonical_match = dict(match)
    canonical_match.update(
        {
            "competition_id": competition_id,
            "season": season,
            "home_team_name": home,
            "away_team_name": away,
            "kickoff": kickoff.isoformat(),
            "cutoff": cutoff.isoformat(),
        }
    )
    result["match"] = canonical_match
    return result


def parse_json(raw: str, *, carrier_request: bool) -> dict[str, Any]:
    if not isinstance(raw, str):
        _fail("FORMAL_REQUEST_JSON_INVALID")
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise FormalRequestContractError("FORMAL_REQUEST_JSON_INVALID") from exc
    return validate_request(value, carrier_request=carrier_request)


def load_request(path: Path, *, carrier_request: bool = False) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        raise FormalRequestContractError("FORMAL_REQUEST_FILE_UNREADABLE") from exc
    return parse_json(raw, carrier_request=carrier_request)


def execution_request(request: dict[str, Any]) -> dict[str, Any]:
    result = dict(request)
    if result.get("mode") in FORMAL_PREDICTION_MODES:
        result["mode"] = "predict"
    return result
